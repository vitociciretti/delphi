import axios from 'axios'
import i18n from '../i18n'

// 创建axios实例
// In production the SPA is served same-origin behind nginx, so VITE_API_BASE_URL
// is set to "" (empty) → axios uses relative /api/... paths. In dev it's undefined
// → fall back to the local backend. (Note: honor an explicit empty string, hence
// the undefined check rather than `||`.)
const API_BASE = import.meta.env.VITE_API_BASE_URL
const service = axios.create({
  baseURL: API_BASE !== undefined ? API_BASE : 'http://localhost:5001',
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  withCredentials: true, // 发送/接收匿名工作区 cookie（delphi_ws）
  headers: {
    'Content-Type': 'application/json'
  }
})

// BYO-key：每个请求携带用户在浏览器 sessionStorage 中的凭据。
// header 名称需与后端 utils/llm_creds.py 保持一致。服务器不保存任何密钥。
const CREDS_KEY = 'delphi.llm_creds'

function attachCredHeaders(config) {
  try {
    const raw = sessionStorage.getItem(CREDS_KEY)
    if (!raw) return
    const c = JSON.parse(raw)
    if (c.api_key) config.headers['X-LLM-Api-Key'] = c.api_key
    if (c.base_url) config.headers['X-LLM-Base-Url'] = c.base_url
    if (c.model) config.headers['X-LLM-Model'] = c.model
    if (c.provider) config.headers['X-LLM-Provider'] = c.provider
    if (c.zep_api_key) config.headers['X-Zep-Api-Key'] = c.zep_api_key
    if (c.graph_provider) config.headers['X-Graph-Provider'] = c.graph_provider
  } catch (e) {
    // malformed creds — ignore, request will fail closed server-side
  }
}

// 请求拦截器
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    attachCredHeaders(config)
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)

    // 优先使用后端返回的错误信息（即使是 4xx/5xx，也常带有 { error } 说明）
    const backendError = error.response?.data?.error
    if (backendError) {
      return Promise.reject(new Error(backendError))
    }

    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      return Promise.reject(new Error('Request timed out.'))
    }

    // 处理网络错误
    if (error.message === 'Network Error') {
      return Promise.reject(new Error('Cannot reach the backend server.'))
    }

    return Promise.reject(error)
  }
)

// 带重试的请求函数
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
