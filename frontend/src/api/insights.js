/**
 * Insights API — opinion distributions, interventions, ensembles, live stream.
 */
import api from './index'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'

export function getEngines() {
  return api.get('/api/insights/engines')
}

export function getOpinionTimeline(simulationId, force = false) {
  return api.get(`/api/insights/simulation/${simulationId}/opinion/timeline`, {
    params: force ? { force: 1 } : {},
  })
}

export function intervene(simulationId, payload) {
  return api.post(`/api/insights/simulation/${simulationId}/intervene`, payload)
}

export function getInterventions(simulationId) {
  return api.get(`/api/insights/simulation/${simulationId}/interventions`)
}

export function createEnsemble(simulationId, payload = {}) {
  return api.post(`/api/insights/simulation/${simulationId}/ensemble`, payload)
}

export function getEnsembleOutcomes(simulationId) {
  return api.get(`/api/insights/simulation/${simulationId}/ensemble/outcomes`)
}

export function getMarketTimeline(simulationId) {
  return api.get(`/api/insights/simulation/${simulationId}/market/timeline`)
}

export function startMarketRun(simulationId, payload = {}) {
  return api.post(`/api/insights/simulation/${simulationId}/market/start`, payload)
}

export function createDemo(kind = 'social', seed = 20) {
  return api.post('/api/insights/demo', { kind, seed })
}

/**
 * Open the SSE live stream for a simulation.
 * handlers: { onStatus(data), onOpinion(data), onDone(data) }
 * Returns the EventSource (call .close() to stop).
 */
export function openStream(simulationId, handlers = {}) {
  const source = new EventSource(
    `${API_BASE}/api/insights/simulation/${simulationId}/stream`
  )
  if (handlers.onStatus) {
    source.addEventListener('status', (e) => handlers.onStatus(JSON.parse(e.data)))
  }
  if (handlers.onOpinion) {
    source.addEventListener('opinion', (e) => handlers.onOpinion(JSON.parse(e.data)))
  }
  source.addEventListener('done', (e) => {
    if (handlers.onDone) handlers.onDone(JSON.parse(e.data))
    source.close()
  })
  source.onerror = () => {
    if (handlers.onError) handlers.onError()
  }
  return source
}
