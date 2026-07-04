import service from './index'

/**
 * 获取当前 LLM 连接设置（api_key 已掩码）
 */
export function getLlmSettings() {
  return service({ url: '/api/settings/llm', method: 'get' })
}

/**
 * 保存并应用 LLM 连接设置
 * @param {Object} data - { provider, api_key, base_url, model }
 */
export function saveLlmSettings(data) {
  return service({ url: '/api/settings/llm', method: 'post', data })
}

/**
 * 测试 LLM 连接（不落盘）
 * @param {Object} data - { api_key, base_url, model }
 */
export function testLlmSettings(data) {
  return service({ url: '/api/settings/llm/test', method: 'post', data })
}
