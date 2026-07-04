<template>
  <div class="outcomes">
    <!-- ======= header ======= -->
    <header class="oc-header">
      <div class="oc-title-block">
        <h1 class="oc-title">DELPHI</h1>
        <p class="oc-subtitle">the oracle — distributions · consensus · minorities · what-ifs</p>
      </div>
      <div class="oc-controls">
        <input
          v-model="simIdInput"
          class="oc-input"
          placeholder="simulation id…"
          @keyup.enter="loadSimulation(simIdInput)"
        />
        <button class="oc-btn" @click="loadSimulation(simIdInput)">LOAD</button>
        <button class="oc-btn oc-btn-ghost" :disabled="busy" @click="loadDemo('social')">
          DEMO: OPINION
        </button>
        <button class="oc-btn oc-btn-ghost" :disabled="busy" @click="loadDemo('market')">
          DEMO: MARKET
        </button>
      </div>
    </header>

    <div v-if="error" class="oc-error">{{ error }}</div>

    <template v-if="rounds.length">
      <!-- ======= status + KPI row ======= -->
      <div class="oc-statusbar">
        <span class="oc-simid">{{ simId }}</span>
        <span class="oc-pill" :class="{ live: isLive }">
          {{ isLive ? '● LIVE' : runnerStatus.toUpperCase() }}
        </span>
        <span class="oc-rounds">{{ rounds.length }} rounds · {{ agentCount }} agents</span>
        <button class="oc-btn oc-btn-small" :class="{ active: showTable }" @click="showTable = !showTable">
          {{ showTable ? 'CHARTS' : 'DATA' }}
        </button>
      </div>

      <div class="oc-kpis">
        <div v-for="kpi in kpis" :key="kpi.label" class="oc-tile">
          <div class="oc-tile-label">{{ kpi.label }}</div>
          <div class="oc-tile-value">{{ kpi.value }}</div>
          <div class="oc-tile-delta" :class="kpi.deltaClass">{{ kpi.delta }}</div>
          <svg class="oc-spark" :ref="el => sparkRefs[kpi.key] = el"></svg>
        </div>
      </div>

      <!-- ======= table view ======= -->
      <div v-if="showTable" class="oc-panel">
        <div class="oc-panel-title">PER-ROUND METRICS</div>
        <div class="oc-tablewrap">
          <table class="oc-table">
            <thead>
              <tr>
                <th>round</th><th>mean</th><th>std</th><th>polarization</th>
                <th>consensus</th><th>camps</th><th>minorities</th><th>interventions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in rounds" :key="r.round_num"
                  :class="{ sel: r.round_num === selectedRound }"
                  @click="selectedRound = r.round_num">
                <td>{{ r.round_num }}</td>
                <td>{{ r.mean.toFixed(3) }}</td>
                <td>{{ r.std.toFixed(3) }}</td>
                <td>{{ r.polarization.toFixed(3) }}</td>
                <td>{{ r.consensus.toFixed(3) }}</td>
                <td>{{ r.clusters.length }}</td>
                <td>{{ r.minorities.length }}</td>
                <td>{{ r.interventions.length ? '⚡' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <template v-else>
        <!-- ======= opinion flow (stacked shares over time) ======= -->
        <div class="oc-panel">
          <div class="oc-panel-head">
            <div class="oc-panel-title">OPINION FLOW</div>
            <div class="oc-legend">
              <span v-for="b in bucketDefs" :key="b.key" class="oc-key">
                <i class="oc-swatch" :style="{ background: b.color }"></i>{{ b.label }}
              </span>
              <span class="oc-key"><i class="oc-swatch oc-swatch-iv"></i>intervention</span>
            </div>
          </div>
          <div ref="flowRef" class="oc-chart oc-chart-flow"></div>
        </div>

        <!-- ======= distribution at round (scrubber + play) ======= -->
        <div class="oc-panel">
          <div class="oc-panel-head">
            <div class="oc-panel-title">
              DISTRIBUTION <span class="oc-dim">— round {{ selectedRound }}</span>
            </div>
            <div class="oc-scrub">
              <button class="oc-btn oc-btn-small" @click="togglePlay">
                {{ playing ? 'PAUSE' : 'PLAY ▸' }}
              </button>
              <input
                type="range" class="oc-range"
                :min="0" :max="rounds.length - 1"
                :value="selectedIndex"
                @input="onScrub($event)"
              />
            </div>
          </div>
          <div ref="histRef" class="oc-chart oc-chart-hist"></div>
          <div class="oc-hist-foot">
            <span>consensus <strong>{{ fmt(selected?.consensus) }}</strong></span>
            <span>polarization <strong>{{ fmt(selected?.polarization) }}</strong></span>
            <span>mean <strong>{{ fmt(selected?.mean, true) }}</strong></span>
            <span v-if="selected?.minorities.length" class="oc-minority-note">
              ▲ {{ selected.minorities.length }} minority camp{{ selected.minorities.length > 1 ? 's' : '' }}
            </span>
          </div>
        </div>

        <div class="oc-row">
          <!-- ======= camps & minorities ======= -->
          <div class="oc-panel oc-half">
            <div class="oc-panel-head">
              <div class="oc-panel-title">CAMPS &amp; MINORITIES</div>
              <div class="oc-legend">
                <span class="oc-key"><i class="oc-line-key gray"></i>camp centroid</span>
                <span class="oc-key"><i class="oc-line-key orange"></i>minority (&lt;25%)</span>
              </div>
            </div>
            <div ref="campsRef" class="oc-chart oc-chart-camps"></div>
          </div>

          <!-- ======= conviction (consensus vs polarization) ======= -->
          <div class="oc-panel oc-half">
            <div class="oc-panel-head">
              <div class="oc-panel-title">CONVICTION</div>
              <div class="oc-legend">
                <span class="oc-key"><i class="oc-line-key blue"></i>consensus</span>
                <span class="oc-key"><i class="oc-line-key red"></i>polarization</span>
              </div>
            </div>
            <div ref="convRef" class="oc-chart oc-chart-conv"></div>
          </div>
        </div>

        <!-- ======= market (only for market-engine runs) ======= -->
        <div v-if="market.length" class="oc-panel">
          <div class="oc-panel-head">
            <div class="oc-panel-title">MARKET</div>
            <div class="oc-legend">
              <span class="oc-key"><i class="oc-line-key blue"></i>price</span>
              <span class="oc-key"><i class="oc-line-key gray"></i>fair value</span>
              <span class="oc-key"><i class="oc-swatch oc-swatch-iv"></i>shock</span>
            </div>
          </div>
          <div ref="priceRef" class="oc-chart oc-chart-price"></div>
          <div ref="volRef" class="oc-chart oc-chart-vol"></div>
        </div>

        <div class="oc-row">
          <!-- ======= ensemble outcomes ======= -->
          <div class="oc-panel oc-half">
            <div class="oc-panel-head">
              <div class="oc-panel-title">OUTCOMES ACROSS RUNS</div>
              <button
                v-if="canEnsemble" class="oc-btn oc-btn-small"
                :disabled="busy" @click="runEnsemble"
              >RUN ×12</button>
            </div>
            <template v-if="ensemble">
              <div class="oc-ens-tiles">
                <div class="oc-tile oc-tile-flat">
                  <div class="oc-tile-label">consensus probability</div>
                  <div class="oc-tile-value">{{ pct(ensemble.distribution.consensus_probability) }}</div>
                </div>
                <div class="oc-tile oc-tile-flat">
                  <div class="oc-tile-label">outcome divergence</div>
                  <div class="oc-tile-value">{{ ensemble.distribution.divergence.toFixed(3) }}</div>
                </div>
                <div v-if="ensemble.distribution.price_range" class="oc-tile oc-tile-flat">
                  <div class="oc-tile-label">final price range</div>
                  <div class="oc-tile-value">
                    {{ ensemble.distribution.price_range.min.toFixed(1) }}–{{ ensemble.distribution.price_range.max.toFixed(1) }}
                  </div>
                </div>
              </div>
              <div ref="ensRef" class="oc-chart oc-chart-ens"></div>
            </template>
            <div v-else class="oc-empty">
              {{ canEnsemble
                ? 'Run a seeded ensemble to see the distribution of final outcomes.'
                : 'Ensembles auto-run only on the in-process market engine; LLM runs must be launched individually.' }}
            </div>
          </div>

          <!-- ======= interventions ======= -->
          <div class="oc-panel oc-half">
            <div class="oc-panel-title">INTERVENE</div>
            <div class="oc-iv-form">
              <textarea
                v-model="ivText" class="oc-textarea" rows="2"
                placeholder="What if… (the event lands as a real post at the next round)"
              ></textarea>
              <div class="oc-iv-row">
                <label v-if="market.length" class="oc-iv-mag">
                  shock {{ ivMagnitude >= 0 ? '+' : '' }}{{ Number(ivMagnitude).toFixed(1) }}
                  <input type="range" min="-1" max="1" step="0.1" v-model="ivMagnitude" class="oc-range" />
                </label>
                <button class="oc-btn" :disabled="busy || !ivText.trim() || !isLive" @click="sendIntervention">
                  INJECT ⚡
                </button>
              </div>
              <div v-if="!isLive" class="oc-dim oc-iv-hint">simulation not running — injection needs a live run</div>
            </div>
            <div class="oc-iv-list">
              <div v-for="iv in interventions" :key="iv.intervention_id" class="oc-iv-item">
                <span class="oc-iv-status" :class="iv.status">{{ iv.status }}</span>
                <span class="oc-iv-text">{{ iv.text }}</span>
              </div>
              <div v-if="!interventions.length" class="oc-empty">no interventions yet</div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <div v-else-if="!error" class="oc-splash">
      <p>Load a simulation id, or start from a demo — no API keys needed.</p>
      <div class="oc-controls">
        <button class="oc-btn" :disabled="busy" @click="loadDemo('social')">DEMO: OPINION DYNAMICS</button>
        <button class="oc-btn" :disabled="busy" @click="loadDemo('market')">DEMO: MARKET ENGINE</button>
      </div>
    </div>

    <div ref="tooltipRef" class="oc-tooltip" style="display:none"></div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as d3 from 'd3'
import {
  createDemo, createEnsemble, getEnsembleOutcomes, getInterventions,
  getMarketTimeline, getOpinionTimeline, intervene, openStream,
} from '../api/insights'

/* ---------- palette (validated: see docs/DELPHI.md) ---------- */
const C = {
  oppStrong: '#c13332', opp: '#ea8a89', mid: '#b3b1a9',
  sup: '#86b6ef', supStrong: '#2a78d6',
  blue: '#2a78d6', red: '#e34948', accent: '#FF4500',
  ink: '#0a0a0a', inkSoft: '#6b6a66', grid: '#e8e7e2', surface: '#ffffff',
}
const bucketDefs = [
  { key: 'oppStrong', label: 'opposed', color: C.oppStrong, lo: -1.01, hi: -0.5 },
  { key: 'opp', label: 'leaning opp.', color: C.opp, lo: -0.5, hi: -0.15 },
  { key: 'mid', label: 'undecided', color: C.mid, lo: -0.15, hi: 0.15 },
  { key: 'sup', label: 'leaning sup.', color: C.sup, lo: 0.15, hi: 0.5 },
  { key: 'supStrong', label: 'supportive', color: C.supStrong, lo: 0.5, hi: 1.01 },
]

/* ---------- state ---------- */
const route = useRoute()
const router = useRouter()
const simId = ref('')
const simIdInput = ref('')
const timeline = ref(null)
const interventions = ref([])
const market = ref([])
const ensemble = ref(null)
const runnerStatus = ref('idle')
const selectedRound = ref(0)
const playing = ref(false)
const showTable = ref(false)
const busy = ref(false)
const error = ref('')
const ivText = ref('')
const ivMagnitude = ref(0.5)

const sparkRefs = reactive({})
const flowRef = ref(null); const histRef = ref(null); const campsRef = ref(null)
const convRef = ref(null); const priceRef = ref(null); const volRef = ref(null)
const ensRef = ref(null); const tooltipRef = ref(null)

let stream = null
let playTimer = null
let resizeObserver = null

/* ---------- derived ---------- */
const rounds = computed(() => timeline.value?.rounds || [])
const agentCount = computed(() => Object.keys(timeline.value?.agents || {}).length)
const isLive = computed(() => ['running', 'starting'].includes(runnerStatus.value))
const selectedIndex = computed(() =>
  Math.max(0, rounds.value.findIndex(r => r.round_num === selectedRound.value)))
const selected = computed(() => rounds.value[selectedIndex.value])
const canEnsemble = computed(() => market.value.length > 0)

const fmt = (v, signed = false) =>
  v == null ? '—' : (signed && v > 0 ? '+' : '') + v.toFixed(2)
const pct = (v) => `${Math.round(v * 100)}%`

const kpis = computed(() => {
  const rs = rounds.value
  if (!rs.length) return []
  const last = rs[rs.length - 1]
  const first = rs[0]
  const delta = (a, b) => (a - b >= 0 ? '+' : '') + (a - b).toFixed(2)
  const cls = (a, b, upGood) => (a === b ? '' : (a > b) === upGood ? 'up' : 'down')
  const top = last.clusters.length
    ? Math.max(...last.clusters.map(c => c.share)) : 0
  return [
    { key: 'consensus', label: 'consensus', value: last.consensus.toFixed(2),
      delta: delta(last.consensus, first.consensus), deltaClass: cls(last.consensus, first.consensus, true),
      series: rs.map(r => r.consensus) },
    { key: 'polarization', label: 'polarization', value: last.polarization.toFixed(2),
      delta: delta(last.polarization, first.polarization), deltaClass: cls(last.polarization, first.polarization, false),
      series: rs.map(r => r.polarization) },
    { key: 'mean', label: 'mean stance', value: fmt(last.mean, true),
      delta: delta(last.mean, first.mean), deltaClass: '',
      series: rs.map(r => r.mean) },
    { key: 'top', label: 'largest camp', value: pct(top),
      delta: `${last.clusters.length} camps`, deltaClass: '',
      series: rs.map(r => r.clusters.length ? Math.max(...r.clusters.map(c => c.share)) : 0) },
  ]
})

/* per-round bucket shares from agent stances */
const bucketSeries = computed(() =>
  rounds.value.map(r => {
    const values = Object.values(r.agent_stances || {})
    const n = values.length || 1
    const shares = {}
    for (const b of bucketDefs) {
      shares[b.key] = values.filter(v => v >= b.lo && v < b.hi).length / n
    }
    return { round: r.round_num, ...shares }
  }))

const interventionRounds = computed(() =>
  rounds.value.filter(r => r.interventions.length)
    .map(r => ({ round: r.round_num, texts: r.interventions.map(i => i.text) })))

/* cluster trajectories keyed by stable cluster_id */
const clusterTracks = computed(() => {
  const tracks = new Map()
  for (const r of rounds.value) {
    const minorityIds = new Set(r.minorities.map(m => m.cluster_id))
    for (const c of r.clusters) {
      if (!tracks.has(c.cluster_id)) tracks.set(c.cluster_id, [])
      tracks.get(c.cluster_id).push({
        round: r.round_num, centroid: c.centroid, share: c.share,
        minority: minorityIds.has(c.cluster_id),
      })
    }
  }
  return [...tracks.entries()].map(([id, points]) => ({ id, points }))
})

/* ---------- data loading ---------- */
async function loadSimulation(id) {
  if (!id) return
  error.value = ''
  busy.value = true
  stopStream(); stopPlay()
  try {
    const res = await getOpinionTimeline(id)
    timeline.value = res.data
    simId.value = id
    simIdInput.value = id
    selectedRound.value = rounds.value.length ? rounds.value[rounds.value.length - 1].round_num : 0
    if (route.params.simulationId !== id) {
      router.replace({ name: 'Delphi', params: { simulationId: id } })
    }
    await Promise.all([refreshInterventions(), refreshMarket(), refreshEnsemble()])
    attachStream()
    await nextTick()
    drawAll()
  } catch (e) {
    timeline.value = null
    error.value = e?.message || `could not load simulation "${id}"`
  } finally {
    busy.value = false
  }
}

async function refreshInterventions() {
  try { interventions.value = (await getInterventions(simId.value)).data || [] }
  catch { interventions.value = [] }
}
async function refreshMarket() {
  try { market.value = (await getMarketTimeline(simId.value)).data || [] }
  catch { market.value = [] }
}
async function refreshEnsemble() {
  try { ensemble.value = (await getEnsembleOutcomes(simId.value)).data }
  catch { ensemble.value = null }
}

async function loadDemo(kind) {
  busy.value = true
  error.value = ''
  try {
    const res = await createDemo(kind, 20)
    await loadSimulation(res.data.simulation_id)
  } catch (e) {
    error.value = e?.message || 'demo creation failed'
  } finally {
    busy.value = false
  }
}

async function runEnsemble() {
  busy.value = true
  try {
    await createEnsemble(simId.value, { variants: 12, base_seed: 7 })
    await refreshEnsemble()
    await nextTick(); drawEnsemble()
  } catch (e) {
    error.value = e?.message || 'ensemble failed'
  } finally {
    busy.value = false
  }
}

async function sendIntervention() {
  busy.value = true
  try {
    const payload = { text: ivText.value.trim() }
    if (market.value.length) payload.magnitude = Number(ivMagnitude.value)
    await intervene(simId.value, payload)
    ivText.value = ''
    await refreshInterventions()
  } catch (e) {
    error.value = e?.message || 'intervention failed'
  } finally {
    busy.value = false
  }
}

/* ---------- live stream ---------- */
function attachStream() {
  stopStream()
  stream = openStream(simId.value, {
    onStatus: (s) => { if (s.runner_status) runnerStatus.value = s.runner_status },
    onOpinion: (payload) => {
      if (!timeline.value) return
      const existing = new Set(rounds.value.map(r => r.round_num))
      const fresh = (payload.new_rounds || []).filter(r => !existing.has(r.round_num))
      if (fresh.length) {
        timeline.value.rounds = [...rounds.value, ...fresh]
        selectedRound.value = fresh[fresh.length - 1].round_num
        drawAll()
      }
    },
    onDone: async (s) => {
      runnerStatus.value = s.runner_status || 'completed'
      await Promise.all([refreshMarket(), refreshEnsemble(), refreshInterventions()])
      drawAll()
    },
    onError: () => { /* stream closed; status stays as last seen */ },
  })
}
function stopStream() { if (stream) { stream.close(); stream = null } }

/* ---------- play / scrub ---------- */
function togglePlay() { playing.value ? stopPlay() : startPlay() }
function startPlay() {
  playing.value = true
  if (selectedIndex.value >= rounds.value.length - 1) selectedRound.value = rounds.value[0].round_num
  playTimer = setInterval(() => {
    const next = selectedIndex.value + 1
    if (next >= rounds.value.length) { stopPlay(); return }
    selectedRound.value = rounds.value[next].round_num
  }, 180)
}
function stopPlay() { playing.value = false; if (playTimer) { clearInterval(playTimer); playTimer = null } }
function onScrub(e) { stopPlay(); selectedRound.value = rounds.value[Number(e.target.value)].round_num }

/* ---------- tooltip ---------- */
function showTip(event, html) {
  const tip = tooltipRef.value
  if (!tip) return
  tip.innerHTML = html
  tip.style.display = 'block'
  const pad = 14
  const rect = document.querySelector('.outcomes').getBoundingClientRect()
  let x = event.clientX - rect.left + pad
  let y = event.clientY - rect.top + pad
  if (x + tip.offsetWidth > rect.width - 8) x -= tip.offsetWidth + 2 * pad
  tip.style.left = `${x}px`
  tip.style.top = `${y}px`
}
function hideTip() { if (tooltipRef.value) tooltipRef.value.style.display = 'none' }
const esc = (s) => String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]))

/* ---------- chart scaffolding ---------- */
function svgIn(el, height) {
  const width = el.clientWidth || 600
  d3.select(el).selectAll('*').remove()
  const svg = d3.select(el).append('svg')
    .attr('width', width).attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)
  return { svg, width, height }
}

function axes(svg, x, y, width, height, m, yFmt = d3.format('.1f')) {
  svg.append('g')
    .attr('transform', `translate(0,${height - m.b})`)
    .call(d3.axisBottom(x).ticks(6).tickSize(0).tickPadding(8))
    .call(g => g.select('.domain').attr('stroke', C.grid))
    .selectAll('text').attr('fill', C.inkSoft).attr('font-size', 10)
  svg.append('g')
    .attr('transform', `translate(${m.l},0)`)
    .call(d3.axisLeft(y).ticks(4).tickSize(-(width - m.l - m.r)).tickPadding(6).tickFormat(yFmt))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', C.grid))
    .selectAll('text').attr('fill', C.inkSoft).attr('font-size', 10)
}

function drawInterventionMarkers(svg, x, height, m) {
  for (const iv of interventionRounds.value) {
    const xp = x(iv.round)
    svg.append('line')
      .attr('x1', xp).attr('x2', xp).attr('y1', m.t).attr('y2', height - m.b)
      .attr('stroke', C.accent).attr('stroke-width', 1.5)
    svg.append('text')
      .attr('x', xp).attr('y', m.t - 3).attr('text-anchor', 'middle')
      .attr('fill', C.accent).attr('font-size', 11).text('⚡')
  }
}

/* ---------- charts ---------- */
function drawSparks() {
  for (const kpi of kpis.value) {
    const el = sparkRefs[kpi.key]
    if (!el) continue
    const w = el.clientWidth || 120; const h = 26
    d3.select(el).selectAll('*').remove()
    const svg = d3.select(el).attr('width', w).attr('height', h)
    const x = d3.scaleLinear([0, kpi.series.length - 1], [1, w - 5])
    const ext = d3.extent(kpi.series)
    const y = d3.scaleLinear(ext[0] === ext[1] ? [ext[0] - 1, ext[1] + 1] : ext, [h - 3, 3])
    svg.append('path')
      .attr('d', d3.line().x((_, i) => x(i)).y(v => y(v))(kpi.series))
      .attr('fill', 'none').attr('stroke', C.mid).attr('stroke-width', 2)
      .attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round')
    const li = kpi.series.length - 1
    svg.append('circle')
      .attr('cx', x(li)).attr('cy', y(kpi.series[li])).attr('r', 4)
      .attr('fill', C.accent).attr('stroke', C.surface).attr('stroke-width', 2)
  }
}

function drawFlow() {
  const el = flowRef.value
  if (!el || !bucketSeries.value.length) return
  const m = { t: 18, r: 12, b: 26, l: 40 }
  const { svg, width, height } = svgIn(el, 240)
  const data = bucketSeries.value
  const keys = bucketDefs.map(b => b.key)
  const x = d3.scaleLinear(d3.extent(data, d => d.round), [m.l, width - m.r])
  const y = d3.scaleLinear([0, 1], [height - m.b, m.t])
  const stack = d3.stack().keys(keys)(data)
  const area = d3.area()
    .x(d => x(d.data.round)).y0(d => y(d[0])).y1(d => y(d[1]))
    .curve(d3.curveMonotoneX)
  axes(svg, x, y, width, height, m, d3.format('.0%'))
  stack.forEach((layer, i) => {
    svg.append('path').attr('d', area(layer))
      .attr('fill', bucketDefs[i].color)
      .attr('stroke', C.surface).attr('stroke-width', 2)
  })
  drawInterventionMarkers(svg, x, height, m)

  /* crosshair + all-series tooltip */
  const hair = svg.append('line')
    .attr('y1', m.t).attr('y2', height - m.b)
    .attr('stroke', C.ink).attr('stroke-width', 1).style('display', 'none')
  svg.append('rect')
    .attr('x', m.l).attr('y', m.t)
    .attr('width', width - m.l - m.r).attr('height', height - m.t - m.b)
    .attr('fill', 'transparent')
    .on('mousemove', (event) => {
      const round = Math.round(x.invert(d3.pointer(event)[0]))
      const row = data.find(d => d.round === round)
      if (!row) return
      hair.style('display', null).attr('x1', x(round)).attr('x2', x(round))
      const iv = interventionRounds.value.find(i => i.round === round)
      showTip(event,
        `<div class="tt-title">round ${round}</div>` +
        bucketDefs.map(b =>
          `<div class="tt-row"><i style="background:${b.color}"></i>` +
          `<strong>${Math.round(row[b.key] * 100)}%</strong> ${b.label}</div>`
        ).join('') +
        (iv ? `<div class="tt-iv">⚡ ${esc(iv.texts[0])}</div>` : ''))
    })
    .on('mouseleave', () => { hair.style('display', 'none'); hideTip() })
}

function drawHistogram() {
  const el = histRef.value
  const snap = selected.value
  if (!el || !snap) return
  const m = { t: 14, r: 12, b: 50, l: 40 }
  const { svg, width, height } = svgIn(el, 226)
  const bins = snap.histogram
  const n = bins.length
  const x = d3.scaleLinear([-1, 1], [m.l, width - m.r])
  const y = d3.scaleLinear([0, Math.max(4, d3.max(bins))], [height - m.b, m.t])
  axes(svg, x, y, width, height, m, d3.format('d'))
  const bw = (width - m.l - m.r) / n
  const colorFor = (i) => {
    const v = -1 + (i + 0.5) * (2 / n)
    return bucketDefs.find(b => v >= b.lo && v < b.hi)?.color || C.mid
  }
  bins.forEach((count, i) => {
    if (!count) return
    const bx = m.l + i * bw + 1
    const by = y(count)
    svg.append('path')
      .attr('d', roundedTopRect(bx, by, Math.max(1, bw - 2), height - m.b - by, 4))
      .attr('fill', colorFor(i))
      .on('mousemove', (event) => showTip(event,
        `<div class="tt-row"><strong>${count}</strong> agent${count > 1 ? 's' : ''} at ` +
        `${(-1 + i * 2 / n).toFixed(2)}…${(-1 + (i + 1) * 2 / n).toFixed(2)}</div>`))
      .on('mouseleave', hideTip)
  })
  /* cluster centroids under the axis; minorities in the accent */
  const minorityIds = new Set(snap.minorities.map(mi => mi.cluster_id))
  for (const c of snap.clusters) {
    const cx = x(c.centroid)
    const minority = minorityIds.has(c.cluster_id)
    svg.append('path')
      .attr('d', `M ${cx - 5} ${height - m.b + 30} L ${cx + 5} ${height - m.b + 30} L ${cx} ${height - m.b + 22} Z`)
      .attr('fill', minority ? C.accent : C.ink)
    if (minority) {
      svg.append('text')
        .attr('x', cx).attr('y', height - m.b + 42).attr('text-anchor', 'middle')
        .attr('fill', C.ink).attr('font-size', 10)
        .text(`${Math.round(c.share * 100)}%`)
    }
  }
}

function roundedTopRect(x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h)
  return `M ${x} ${y + h} L ${x} ${y + rr} Q ${x} ${y} ${x + rr} ${y}` +
    ` L ${x + w - rr} ${y} Q ${x + w} ${y} ${x + w} ${y + rr} L ${x + w} ${y + h} Z`
}

function drawCamps() {
  const el = campsRef.value
  if (!el || !clusterTracks.value.length) return
  const m = { t: 18, r: 14, b: 26, l: 40 }
  const { svg, width, height } = svgIn(el, 220)
  const x = d3.scaleLinear(d3.extent(rounds.value, r => r.round_num), [m.l, width - m.r])
  const y = d3.scaleLinear([-1, 1], [height - m.b, m.t])
  axes(svg, x, y, width, height, m, d3.format('+.1f'))
  svg.append('line')
    .attr('x1', m.l).attr('x2', width - m.r).attr('y1', y(0)).attr('y2', y(0))
    .attr('stroke', C.inkSoft).attr('stroke-width', 1)
  drawInterventionMarkers(svg, x, height, m)
  const line = d3.line()
    .x(p => x(p.round)).y(p => y(p.centroid)).curve(d3.curveMonotoneX)
  for (const track of clusterTracks.value) {
    if (track.points.length < 2) continue
    /* split into minority / majority segments so color follows state */
    let seg = [track.points[0]]
    const segments = []
    for (let i = 1; i < track.points.length; i++) {
      const p = track.points[i]
      if (p.minority !== seg[0].minority) { segments.push(seg); seg = [track.points[i - 1]] }
      seg.push(p)
    }
    segments.push(seg)
    for (const s of segments) {
      svg.append('path').attr('d', line(s)).attr('fill', 'none')
        .attr('stroke', s[s.length - 1].minority ? C.accent : C.mid)
        .attr('stroke-width', 2 + 6 * d3.mean(s, p => p.share))
        .attr('stroke-linecap', 'round').attr('opacity', 0.9)
        .on('mousemove', (event) => {
          const round = Math.round(x.invert(d3.pointer(event)[0]))
          const p = track.points.find(pt => pt.round === round) || s[s.length - 1]
          showTip(event,
            `<div class="tt-title">camp ${track.id} · round ${p.round}</div>` +
            `<div class="tt-row"><strong>${Math.round(p.share * 100)}%</strong> of agents at ${p.centroid.toFixed(2)}` +
            `${p.minority ? ' · minority' : ''}</div>`)
        })
        .on('mouseleave', hideTip)
    }
  }
}

function drawConviction() {
  const el = convRef.value
  if (!el || rounds.value.length < 2) return
  const m = { t: 18, r: 76, b: 26, l: 40 }
  const { svg, width, height } = svgIn(el, 220)
  const x = d3.scaleLinear(d3.extent(rounds.value, r => r.round_num), [m.l, width - m.r])
  const y = d3.scaleLinear([0, 1], [height - m.b, m.t])
  axes(svg, x, y, width, height, m)
  drawInterventionMarkers(svg, x, height, m)
  const series = [
    { key: 'consensus', color: C.blue, label: 'consensus' },
    { key: 'polarization', color: C.red, label: 'polarization' },
  ]
  const line = d3.line().x(r => x(r.round_num)).curve(d3.curveMonotoneX)
  for (const s of series) {
    svg.append('path')
      .attr('d', line.y(r => y(r[s.key]))(rounds.value))
      .attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 2)
      .attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round')
    const last = rounds.value[rounds.value.length - 1]
    svg.append('circle')
      .attr('cx', x(last.round_num)).attr('cy', y(last[s.key])).attr('r', 4)
      .attr('fill', s.color).attr('stroke', C.surface).attr('stroke-width', 2)
    svg.append('text')
      .attr('x', x(last.round_num) + 8).attr('y', y(last[s.key]) + 3)
      .attr('fill', C.ink).attr('font-size', 10).text(last[s.key].toFixed(2))
  }
  const hair = svg.append('line')
    .attr('y1', m.t).attr('y2', height - m.b)
    .attr('stroke', C.ink).attr('stroke-width', 1).style('display', 'none')
  svg.append('rect')
    .attr('x', m.l).attr('y', m.t)
    .attr('width', width - m.l - m.r).attr('height', height - m.t - m.b)
    .attr('fill', 'transparent')
    .on('mousemove', (event) => {
      const round = Math.round(x.invert(d3.pointer(event)[0]))
      const row = rounds.value.find(r => r.round_num === round)
      if (!row) return
      hair.style('display', null).attr('x1', x(round)).attr('x2', x(round))
      showTip(event,
        `<div class="tt-title">round ${round}</div>` +
        series.map(s =>
          `<div class="tt-row"><i style="background:${s.color}"></i>` +
          `<strong>${row[s.key].toFixed(2)}</strong> ${s.label}</div>`).join(''))
    })
    .on('mouseleave', () => { hair.style('display', 'none'); hideTip() })
}

function drawMarket() {
  const priceEl = priceRef.value; const volEl = volRef.value
  if (!priceEl || !volEl || !market.value.length) return
  const data = market.value
  const m = { t: 16, r: 14, b: 4, l: 46 }
  const { svg, width, height } = svgIn(priceEl, 200)
  const x = d3.scaleLinear(d3.extent(data, d => d.round), [m.l, width - m.r])
  const ext = d3.extent([...data.map(d => d.price), ...data.map(d => d.fair_value)])
  const y = d3.scaleLinear([ext[0] * 0.995, ext[1] * 1.005], [height - m.b, m.t])
  svg.append('g')
    .attr('transform', `translate(${m.l},0)`)
    .call(d3.axisLeft(y).ticks(4).tickSize(-(width - m.l - m.r)).tickPadding(6))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', C.grid))
    .selectAll('text').attr('fill', C.inkSoft).attr('font-size', 10)
  const line = d3.line().x(d => x(d.round)).curve(d3.curveMonotoneX)
  svg.append('path').attr('d', line.y(d => y(d.fair_value))(data))
    .attr('fill', 'none').attr('stroke', C.mid).attr('stroke-width', 2)
  svg.append('path').attr('d', line.y(d => y(d.price))(data))
    .attr('fill', 'none').attr('stroke', C.blue).attr('stroke-width', 2)
    .attr('stroke-linecap', 'round')
  for (const d of data.filter(d => d.shock)) {
    svg.append('line')
      .attr('x1', x(d.round)).attr('x2', x(d.round))
      .attr('y1', m.t).attr('y2', height - m.b)
      .attr('stroke', C.accent).attr('stroke-width', 1.5)
  }
  const hair = svg.append('line')
    .attr('y1', m.t).attr('y2', height - m.b)
    .attr('stroke', C.ink).attr('stroke-width', 1).style('display', 'none')
  svg.append('rect')
    .attr('x', m.l).attr('y', m.t)
    .attr('width', width - m.l - m.r).attr('height', height - m.t - m.b)
    .attr('fill', 'transparent')
    .on('mousemove', (event) => {
      const round = Math.round(x.invert(d3.pointer(event)[0]))
      const row = data.find(d => d.round === round)
      if (!row) return
      hair.style('display', null).attr('x1', x(round)).attr('x2', x(round))
      showTip(event,
        `<div class="tt-title">round ${round}</div>` +
        `<div class="tt-row"><i style="background:${C.blue}"></i><strong>${row.price.toFixed(2)}</strong> price</div>` +
        `<div class="tt-row"><i style="background:${C.mid}"></i><strong>${row.fair_value.toFixed(2)}</strong> fair value</div>` +
        `<div class="tt-row"><strong>${row.volume}</strong> volume · sentiment ${row.mean_sentiment.toFixed(2)}</div>`)
    })
    .on('mouseleave', () => { hair.style('display', 'none'); hideTip() })

  /* volume as its own panel (never a second axis on the price chart) */
  const mv = { t: 4, r: 14, b: 22, l: 46 }
  const vol = svgIn(volEl, 74)
  const xv = d3.scaleLinear(d3.extent(data, d => d.round), [mv.l, vol.width - mv.r])
  const yv = d3.scaleLinear([0, d3.max(data, d => d.volume) || 1], [vol.height - mv.b, mv.t])
  vol.svg.append('g')
    .attr('transform', `translate(0,${vol.height - mv.b})`)
    .call(d3.axisBottom(xv).ticks(6).tickSize(0).tickPadding(8))
    .call(g => g.select('.domain').attr('stroke', C.grid))
    .selectAll('text').attr('fill', C.inkSoft).attr('font-size', 10)
  const bwv = Math.max(1, (vol.width - mv.l - mv.r) / data.length - 2)
  for (const d of data) {
    if (!d.volume) continue
    vol.svg.append('rect')
      .attr('x', xv(d.round) - bwv / 2).attr('y', yv(d.volume))
      .attr('width', bwv).attr('height', vol.height - mv.b - yv(d.volume))
      .attr('fill', d.imbalance >= 0 ? C.sup : C.opp)
  }
}

function drawEnsemble() {
  const el = ensRef.value
  if (!el || !ensemble.value) return
  const m = { t: 14, r: 12, b: 30, l: 36 }
  const { svg, width, height } = svgIn(el, 150)
  const bins = ensemble.value.distribution.final_mean_histogram
  const n = bins.length
  const x = d3.scaleLinear([-1, 1], [m.l, width - m.r])
  const y = d3.scaleLinear([0, Math.max(3, d3.max(bins))], [height - m.b, m.t])
  axes(svg, x, y, width, height, m, d3.format('d'))
  const bw = (width - m.l - m.r) / n
  bins.forEach((count, i) => {
    if (!count) return
    const bx = m.l + i * bw + 1
    const by = y(count)
    svg.append('path')
      .attr('d', roundedTopRect(bx, by, Math.max(1, bw - 2), height - m.b - by, 4))
      .attr('fill', C.blue)
      .on('mousemove', (event) => showTip(event,
        `<div class="tt-row"><strong>${count}</strong> run${count > 1 ? 's' : ''} ended near ` +
        `${(-1 + (i + 0.5) * 2 / n).toFixed(2)}</div>`))
      .on('mouseleave', hideTip)
  })
}

function drawAll() {
  drawSparks(); drawFlow(); drawHistogram(); drawCamps(); drawConviction()
  drawMarket(); drawEnsemble()
}

/* ---------- lifecycle ---------- */
watch(selectedRound, () => drawHistogram())
watch(showTable, async (v) => { if (!v) { await nextTick(); drawAll() } })

onMounted(() => {
  resizeObserver = new ResizeObserver(() => drawAll())
  resizeObserver.observe(document.querySelector('.outcomes'))
  const fromRoute = route.params.simulationId
  if (fromRoute) loadSimulation(String(fromRoute))
})

onBeforeUnmount(() => {
  stopStream(); stopPlay()
  if (resizeObserver) resizeObserver.disconnect()
})
</script>

<style scoped>
.outcomes {
  --black: #0a0a0a;
  --white: #ffffff;
  --orange: #ff4500;
  --gray-light: #f4f3ef;
  --border: #0a0a0a;
  --ink-soft: #6b6a66;
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', sans-serif;
  position: relative;
  max-width: 1240px;
  margin: 0 auto;
  padding: 24px 20px 80px;
  font-family: var(--font-sans);
  color: var(--black);
  background: var(--white);
}

/* header */
.oc-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 16px; flex-wrap: wrap;
  border-bottom: 3px solid var(--border); padding-bottom: 16px; margin-bottom: 16px;
}
.oc-title { font-size: 34px; letter-spacing: -0.02em; margin: 0; }
.oc-subtitle { font-family: var(--font-mono); font-size: 12px; color: var(--ink-soft); margin: 4px 0 0; }
.oc-controls { display: flex; gap: 8px; flex-wrap: wrap; }
.oc-input {
  font-family: var(--font-mono); font-size: 12px; padding: 8px 10px;
  border: 2px solid var(--border); background: var(--white); min-width: 210px;
}
.oc-btn {
  font-family: var(--font-mono); font-size: 12px; font-weight: 700;
  padding: 8px 14px; border: 2px solid var(--border);
  background: var(--black); color: var(--white); cursor: pointer;
}
.oc-btn:hover:not(:disabled) { background: var(--orange); border-color: var(--orange); }
.oc-btn:disabled { opacity: 0.4; cursor: default; }
.oc-btn-ghost { background: var(--white); color: var(--black); }
.oc-btn-small { padding: 4px 10px; font-size: 11px; }
.oc-btn-small.active { background: var(--orange); border-color: var(--orange); }

.oc-error {
  font-family: var(--font-mono); font-size: 12px; color: var(--white);
  background: var(--black); border-left: 6px solid var(--orange);
  padding: 10px 12px; margin-bottom: 14px;
}

/* status bar */
.oc-statusbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.oc-simid { font-family: var(--font-mono); font-size: 13px; font-weight: 700; }
.oc-pill {
  font-family: var(--font-mono); font-size: 10px; font-weight: 700;
  padding: 3px 8px; border: 2px solid var(--border);
}
.oc-pill.live { color: var(--white); background: var(--orange); border-color: var(--orange); animation: pulse 1.6s infinite; }
@keyframes pulse { 50% { opacity: 0.65; } }
.oc-rounds { font-family: var(--font-mono); font-size: 11px; color: var(--ink-soft); flex: 1; }

/* KPI tiles */
.oc-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.oc-tile { border: 2px solid var(--border); padding: 10px 12px 8px; background: var(--white); }
.oc-tile-flat { border-width: 1px; }
.oc-tile-label { font-family: var(--font-mono); font-size: 10px; color: var(--ink-soft); text-transform: uppercase; }
.oc-tile-value { font-size: 26px; font-weight: 600; line-height: 1.2; }
.oc-tile-delta { font-family: var(--font-mono); font-size: 10px; color: var(--ink-soft); min-height: 14px; }
.oc-tile-delta.up { color: #008300; }
.oc-tile-delta.down { color: #c13332; }
.oc-spark { width: 100%; height: 26px; display: block; margin-top: 4px; }

/* panels */
.oc-panel { border: 2px solid var(--border); padding: 12px 14px 14px; margin-bottom: 14px; background: var(--white); }
.oc-panel-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.oc-panel-title { font-family: var(--font-mono); font-size: 12px; font-weight: 700; letter-spacing: 0.06em; }
.oc-dim { color: var(--ink-soft); font-weight: 400; }
.oc-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.oc-half { margin-bottom: 14px; }
@media (max-width: 860px) { .oc-row { grid-template-columns: 1fr; } .oc-kpis { grid-template-columns: repeat(2, 1fr); } }

/* legend */
.oc-legend { display: flex; gap: 12px; flex-wrap: wrap; }
.oc-key { display: inline-flex; align-items: center; gap: 5px; font-family: var(--font-mono); font-size: 10px; color: var(--ink-soft); }
.oc-swatch { width: 10px; height: 10px; display: inline-block; }
.oc-swatch-iv { background: var(--orange); }
.oc-line-key { width: 14px; height: 2px; display: inline-block; }
.oc-line-key.blue { background: #2a78d6; }
.oc-line-key.red { background: #e34948; }
.oc-line-key.gray { background: #b3b1a9; }
.oc-line-key.orange { background: var(--orange); }

/* charts */
.oc-chart { width: 100%; }
.oc-chart :deep(svg) { display: block; }

/* histogram footer + scrubber */
.oc-hist-foot { display: flex; gap: 18px; font-family: var(--font-mono); font-size: 11px; color: var(--ink-soft); margin-top: 4px; flex-wrap: wrap; }
.oc-hist-foot strong { color: var(--black); }
.oc-minority-note { color: var(--orange); font-weight: 700; }
.oc-scrub { display: flex; align-items: center; gap: 10px; flex: 1; max-width: 460px; }
.oc-range { flex: 1; accent-color: var(--orange); }

/* ensemble */
.oc-ens-tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 8px; }
.oc-empty { font-family: var(--font-mono); font-size: 11px; color: var(--ink-soft); padding: 18px 0; }

/* interventions */
.oc-iv-form { margin-bottom: 10px; }
.oc-textarea {
  width: 100%; box-sizing: border-box; font-family: var(--font-mono); font-size: 12px;
  border: 2px solid var(--border); padding: 8px; resize: vertical; background: var(--white);
}
.oc-iv-row { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
.oc-iv-mag { display: flex; align-items: center; gap: 8px; flex: 1; font-family: var(--font-mono); font-size: 11px; }
.oc-iv-hint { font-family: var(--font-mono); font-size: 10px; margin-top: 6px; }
.oc-iv-list { max-height: 180px; overflow-y: auto; }
.oc-iv-item { display: flex; gap: 8px; align-items: baseline; padding: 6px 0; border-top: 1px solid var(--gray-light); }
.oc-iv-status { font-family: var(--font-mono); font-size: 9px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border: 1px solid var(--border); flex-shrink: 0; }
.oc-iv-status.applied { background: var(--black); color: var(--white); }
.oc-iv-status.pending { background: var(--white); }
.oc-iv-status.failed { background: var(--orange); color: var(--white); border-color: var(--orange); }
.oc-iv-text { font-size: 12px; }

/* table view */
.oc-tablewrap { overflow-x: auto; }
.oc-table { border-collapse: collapse; width: 100%; font-family: var(--font-mono); font-size: 11px; }
.oc-table th { text-align: left; border-bottom: 2px solid var(--border); padding: 6px 10px; text-transform: uppercase; font-size: 10px; }
.oc-table td { border-bottom: 1px solid var(--gray-light); padding: 5px 10px; font-variant-numeric: tabular-nums; }
.oc-table tr { cursor: pointer; }
.oc-table tr.sel td { background: var(--gray-light); }

/* splash */
.oc-splash { text-align: center; padding: 70px 0; font-family: var(--font-mono); font-size: 13px; color: var(--ink-soft); }
.oc-splash .oc-controls { justify-content: center; margin-top: 18px; }

/* tooltip */
.oc-tooltip {
  position: absolute; z-index: 30; pointer-events: none;
  background: var(--black); color: var(--white);
  font-family: var(--font-mono); font-size: 11px;
  padding: 8px 10px; max-width: 280px;
}
.oc-tooltip :deep(.tt-title) { font-weight: 700; margin-bottom: 4px; }
.oc-tooltip :deep(.tt-row) { display: flex; align-items: center; gap: 6px; line-height: 1.6; }
.oc-tooltip :deep(.tt-row i) { width: 10px; height: 10px; display: inline-block; flex-shrink: 0; }
.oc-tooltip :deep(.tt-iv) { color: var(--orange); margin-top: 4px; }
</style>
