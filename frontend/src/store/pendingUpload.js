/**
 * 临时存储待上传的文件和需求
 * 用于首页点击启动引擎后立即跳转，在Process页面再进行API调用
 *
 * 种子来源（seedSource）：
 *   "uploaded"  - 用户上传文档（files 非空）
 *   "assistant" - 助手起草并经用户确认的“世界状态”文档（seedText 非空）
 *   "sample"    - 示例文档（seedText 非空）
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  seedText: '',
  seedSource: 'uploaded',
  isPending: false
})

export function setPendingUpload(files, requirement) {
  state.files = files
  state.simulationRequirement = requirement
  state.seedText = ''
  state.seedSource = 'uploaded'
  state.isPending = true
}

/**
 * 存储一份由助手起草（或示例）的种子文档，替代上传文件。
 * @param {String} seedText - “世界状态”文档正文
 * @param {String} requirement - 模拟需求
 * @param {String} seedSource - "assistant" | "sample"
 */
export function setPendingSeedText(seedText, requirement, seedSource = 'assistant') {
  state.files = []
  state.simulationRequirement = requirement
  state.seedText = seedText
  state.seedSource = seedSource
  state.isPending = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    simulationRequirement: state.simulationRequirement,
    seedText: state.seedText,
    seedSource: state.seedSource,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.seedText = ''
  state.seedSource = 'uploaded'
  state.isPending = false
}

export default state
