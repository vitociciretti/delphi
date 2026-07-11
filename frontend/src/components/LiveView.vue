<template>
  <div class="live-view">
    <div class="live-header">
      <div class="lh-title">Live Simulation</div>
      <div class="lh-meta">
        <span v-if="totalRounds" class="lh-round">Round {{ currentRound }} / {{ totalRounds }}</span>
        <span class="lh-stat">{{ nodes.length }} agents</span>
        <span class="lh-stat">{{ totalActions }} actions</span>
        <span class="lh-status" :class="status">{{ status || '—' }}</span>
      </div>
    </div>

    <div class="live-body">
      <!-- Interaction graph -->
      <div class="panel graph-panel">
        <div class="panel-title">Agent interaction graph</div>
        <svg ref="graphSvg" class="graph-svg"></svg>
        <div class="legend">
          <span v-for="s in stanceOrder" :key="s" class="legend-item">
            <span class="dot" :style="{ background: stanceColor(s) }"></span>{{ s }}
          </span>
        </div>
        <div v-if="!edges.length && nodes.length" class="empty-hint">
          No interactions yet — agents are still posting. Edges appear as they like / reply / repost each other.
        </div>
      </div>

      <!-- Stance convergence histogram -->
      <div class="panel hist-panel">
        <div class="panel-title">Stance convergence over rounds</div>
        <svg ref="histSvg" class="hist-svg"></svg>
        <div class="empty-hint" v-if="!rounds.length">Waiting for the first round…</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as d3 from 'd3'
import { getLiveView } from '../api/simulation'

const props = defineProps({
  simulationId: { type: String, required: true },
  active: { type: Boolean, default: true },
})

const nodes = ref([])
const edges = ref([])
const rounds = ref([])
const stanceOrder = ref(['supportive', 'opposing', 'neutral', 'observer'])
const currentRound = ref(0)
const totalRounds = ref(0)
const totalActions = ref(0)
const status = ref('')

const graphSvg = ref(null)
const histSvg = ref(null)
let pollTimer = null
let sim = null

const STANCE_COLORS = {
  supportive: '#0a7d34',
  opposing: '#d1342f',
  neutral: '#8a8a8a',
  observer: '#1a6ec4',
}
const stanceColor = (s) => STANCE_COLORS[s] || '#bbb'

async function fetchData() {
  try {
    const res = await getLiveView(props.simulationId)
    if (!res.success) return
    const d = res.data
    nodes.value = d.nodes || []
    edges.value = d.edges || []
    rounds.value = d.rounds || []
    stanceOrder.value = d.stances || stanceOrder.value
    currentRound.value = d.current_round || d.max_round || 0
    totalRounds.value = d.total_rounds || 0
    totalActions.value = d.total_actions || 0
    status.value = d.status || ''
    await nextTick()
    renderGraph()
    renderHist()
    // stop polling once finished
    if (['stopped', 'completed', 'failed'].includes(status.value)) stopPolling()
  } catch (e) {
    // transient; keep polling
  }
}

function renderGraph() {
  const el = graphSvg.value
  if (!el) return
  const width = el.clientWidth || 600
  const height = el.clientHeight || 360
  const svg = d3.select(el)
  svg.selectAll('*').remove()
  if (!nodes.value.length) return

  // clone so d3 can mutate x/y without touching reactive state
  const N = nodes.value.map(n => ({ ...n }))
  const idset = new Set(N.map(n => n.id))
  const E = edges.value
    .filter(e => idset.has(e.source) && idset.has(e.target))
    .map(e => ({ ...e }))

  const g = svg.append('g')
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', ev => g.attr('transform', ev.transform)))

  const maxAct = d3.max(N, n => n.action_count) || 1
  const r = d3.scaleSqrt().domain([0, maxAct]).range([6, 22])

  const link = g.append('g').attr('stroke', '#ccc').attr('stroke-opacity', 0.6)
    .selectAll('line').data(E).join('line')
    .attr('stroke-width', d => Math.min(4, 1 + Math.log2(d.weight + 1)))

  const node = g.append('g').selectAll('g').data(N).join('g')
  node.append('circle')
    .attr('r', d => r(d.action_count))
    .attr('fill', d => stanceColor(d.stance))
    .attr('stroke', '#fff').attr('stroke-width', 1.5)
  node.append('title').text(d => `${d.name} · ${d.entity_type} · ${d.stance} · ${d.action_count} actions`)
  node.append('text').text(d => d.name)
    .attr('font-size', 10).attr('dx', d => r(d.action_count) + 3).attr('dy', 3).attr('fill', '#444')

  if (sim) sim.stop()
  sim = d3.forceSimulation(N)
    .force('link', d3.forceLink(E).id(d => d.id).distance(70))
    .force('charge', d3.forceManyBody().strength(-260))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(d => r(d.action_count) + 6))
    .on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })
}

function renderHist() {
  const el = histSvg.value
  if (!el || !rounds.value.length) return
  const width = el.clientWidth || 600
  const height = el.clientHeight || 300
  const margin = { top: 12, right: 12, bottom: 28, left: 34 }
  const iw = width - margin.left - margin.right
  const ih = height - margin.top - margin.bottom

  const svg = d3.select(el)
  svg.selectAll('*').remove()
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

  const data = rounds.value
  const keys = stanceOrder.value
  const stack = d3.stack().keys(keys).value((d, k) => (d.by_stance && d.by_stance[k]) || 0)
  const series = stack(data)

  const x = d3.scaleBand().domain(data.map(d => d.round)).range([0, iw]).padding(0.15)
  const yMax = d3.max(data, d => keys.reduce((s, k) => s + ((d.by_stance && d.by_stance[k]) || 0), 0)) || 1
  const y = d3.scaleLinear().domain([0, yMax]).range([ih, 0]).nice()

  g.append('g').selectAll('g').data(series).join('g')
    .attr('fill', d => stanceColor(d.key))
    .selectAll('rect').data(d => d).join('rect')
    .attr('x', d => x(d.data.round))
    .attr('y', d => y(d[1]))
    .attr('height', d => Math.max(0, y(d[0]) - y(d[1])))
    .attr('width', x.bandwidth())

  // axes (sparse ticks)
  const everyN = Math.ceil(data.length / 12)
  g.append('g').attr('transform', `translate(0,${ih})`)
    .call(d3.axisBottom(x).tickValues(data.map(d => d.round).filter((_, i) => i % everyN === 0)))
    .attr('font-size', 9)
  g.append('g').call(d3.axisLeft(y).ticks(4)).attr('font-size', 9)
}

function startPolling() {
  fetchData()
  stopPolling()
  pollTimer = setInterval(() => { if (props.active) fetchData() }, 3000)
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

watch(() => props.active, (a) => { if (a) { fetchData() } })
onMounted(startPolling)
onUnmounted(() => { stopPolling(); if (sim) sim.stop() })
</script>

<style scoped>
.live-view { display: flex; flex-direction: column; height: 100%; background: #FAFAFA;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; }
.live-header { display: flex; justify-content: space-between; align-items: center;
  padding: 12px 20px; border-bottom: 1px solid #eee; background: #fff; }
.lh-title { font-size: 15px; font-weight: 700; }
.lh-meta { display: flex; gap: 14px; align-items: center; font-size: 12px; color: #666; }
.lh-round { font-weight: 600; color: #FF4500; }
.lh-status { text-transform: capitalize; padding: 2px 8px; border-radius: 10px; background: #f0f0f0; font-size: 11px; }
.lh-status.running { background: rgba(10,125,52,0.12); color: #0a7d34; }
.lh-status.completed, .lh-status.stopped { background: rgba(26,110,196,0.12); color: #1a6ec4; }

.live-body { flex: 1; display: grid; grid-template-columns: 1.3fr 1fr; gap: 14px; padding: 14px; min-height: 0; }
.panel { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; min-height: 0; position: relative; }
.panel-title { font-size: 12px; font-weight: 600; color: #444; margin-bottom: 8px; }
.graph-svg, .hist-svg { flex: 1; width: 100%; min-height: 260px; }
.legend { display: flex; gap: 12px; flex-wrap: wrap; padding-top: 6px; font-size: 11px; color: #666; }
.legend-item { display: flex; align-items: center; gap: 4px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.empty-hint { position: absolute; bottom: 16px; left: 12px; right: 12px; text-align: center;
  font-size: 11.5px; color: #aaa; line-height: 1.4; }
@media (max-width: 900px) { .live-body { grid-template-columns: 1fr; } }
</style>
