import service from './index'

/**
 * BYO-key：LLM/Zep 凭据只存在于浏览器 sessionStorage，绝不发往服务器保存。
 * 每个请求由 api/index.js 的拦截器以 header 携带（X-LLM-*, X-Zep-Api-Key）。
 * sessionStorage（而非 localStorage）意味着关闭标签页后密钥即失效。
 */
export const CREDS_KEY = 'delphi.llm_creds'

const EMPTY = { provider: '', api_key: '', base_url: '', model: '', zep_api_key: '', graph_provider: 'zep' }

/** 读取本地保存的凭据（不存在时返回空结构）。 */
export function loadLocalCreds() {
  try {
    const raw = sessionStorage.getItem(CREDS_KEY)
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : { ...EMPTY }
  } catch (e) {
    return { ...EMPTY }
  }
}

/** 保存凭据到 sessionStorage。 */
export function saveLocalCreds(data) {
  const clean = { ...EMPTY, ...data }
  sessionStorage.setItem(CREDS_KEY, JSON.stringify(clean))
  return clean
}

/** 是否已配置（至少有 base_url + model，密钥对本地 provider 可为空）。 */
export function hasLocalCreds() {
  const c = loadLocalCreds()
  return !!(c.base_url && c.model)
}

/**
 * 测试 LLM 连接（不落盘）。
 * @param {Object} data - { api_key, base_url, model }
 */
export function testLlmSettings(data) {
  return service({ url: '/api/settings/llm/test', method: 'post', data })
}
