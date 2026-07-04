<template>
  <div class="seed-overlay" @click.self="onCancel">
    <div class="seed-modal">
      <div class="seed-header">
        <div>
          <div class="seed-title">{{ $t('seedAssistant.title') }}</div>
          <div class="seed-subtitle">{{ $t('seedAssistant.subtitle') }}</div>
        </div>
        <button class="seed-close" @click="onCancel">×</button>
      </div>

      <!-- Phase: chat -->
      <div v-if="phase === 'chat'" class="seed-body">
        <div class="chat-scroll" ref="scrollEl">
          <div class="msg assistant">
            <div class="bubble">{{ $t('seedAssistant.intro') }}</div>
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="msg"
            :class="m.role"
          >
            <div class="bubble">{{ m.content }}</div>
          </div>
          <div v-if="busy" class="msg assistant">
            <div class="bubble busy">{{ drafting ? $t('seedAssistant.drafting') : $t('seedAssistant.thinking') }}</div>
          </div>
        </div>

        <div v-if="errorMsg" class="seed-error">{{ errorMsg }}</div>

        <div class="chat-input-row">
          <textarea
            v-model="draftInput"
            class="chat-input"
            :placeholder="$t('seedAssistant.inputPlaceholder')"
            rows="2"
            :disabled="busy"
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <div class="chat-actions">
            <button class="btn ghost" :disabled="busy || !draftInput.trim()" @click="send">
              {{ $t('seedAssistant.send') }}
            </button>
            <button class="btn primary" :disabled="busy || messages.length === 0" @click="doDraft">
              {{ $t('seedAssistant.draftIt') }} →
            </button>
          </div>
        </div>
      </div>

      <!-- Phase: review -->
      <div v-else class="seed-body">
        <div class="review-heading">{{ $t('seedAssistant.draftHeading') }}</div>
        <div class="review-hint">{{ $t('seedAssistant.draftHint') }}</div>
        <textarea v-model="draftDoc" class="review-doc" spellcheck="false"></textarea>
        <div v-if="errorMsg" class="seed-error">{{ errorMsg }}</div>
        <div class="review-actions">
          <button class="btn ghost" :disabled="busy" @click="phase = 'chat'">← {{ $t('seedAssistant.back') }}</button>
          <div class="spacer"></div>
          <button class="btn ghost" :disabled="busy" @click="doDraft">
            {{ busy ? $t('seedAssistant.drafting') : $t('seedAssistant.regenerate') }}
          </button>
          <button class="btn primary" :disabled="busy || !draftDoc.trim()" @click="accept">
            {{ $t('seedAssistant.useThis') }} →
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { seedChat, seedDraft } from '../api/graph'

const props = defineProps({
  requirement: { type: String, default: '' },
  scenarioId: { type: String, default: '' }
})
const emit = defineEmits(['accept', 'close'])

const phase = ref('chat')          // 'chat' | 'review'
const messages = ref([])            // [{role, content}]
const draftInput = ref('')
const draftDoc = ref('')
const busy = ref(false)
const drafting = ref(false)
const errorMsg = ref('')
const scrollEl = ref(null)

const payload = () => ({
  messages: messages.value,
  scenario_id: props.scenarioId || undefined,
  requirement: props.requirement || ''
})

const scrollDown = async () => {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

const send = async () => {
  const text = draftInput.value.trim()
  if (!text || busy.value) return
  errorMsg.value = ''
  messages.value.push({ role: 'user', content: text })
  draftInput.value = ''
  busy.value = true
  await scrollDown()
  try {
    const res = await seedChat(payload())
    if (res.success) {
      messages.value.push({ role: 'assistant', content: res.data.reply })
    } else {
      errorMsg.value = res.error || 'Assistant error'
    }
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    busy.value = false
    await scrollDown()
  }
}

const doDraft = async () => {
  if (busy.value) return
  errorMsg.value = ''
  busy.value = true
  drafting.value = true
  try {
    const res = await seedDraft(payload())
    if (res.success) {
      draftDoc.value = res.data.draft
      phase.value = 'review'
    } else {
      errorMsg.value = res.error || 'Draft failed'
    }
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    busy.value = false
    drafting.value = false
  }
}

const accept = () => {
  if (!draftDoc.value.trim()) return
  emit('accept', { seedText: draftDoc.value })
}

const onCancel = () => emit('close')
</script>

<style scoped>
.seed-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.seed-modal {
  width: min(760px, 100%);
  max-height: 88vh;
  background: #fff;
  border: 1px solid #e5e5e5;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}
.seed-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 20px 24px;
  border-bottom: 1px solid #eee;
}
.seed-title { font-size: 18px; font-weight: 700; }
.seed-subtitle { font-size: 13px; color: #666; margin-top: 4px; max-width: 560px; line-height: 1.4; }
.seed-close {
  border: none; background: none; font-size: 26px; line-height: 1;
  cursor: pointer; color: #999; padding: 0;
}
.seed-close:hover { color: #000; }

.seed-body { display: flex; flex-direction: column; min-height: 0; flex: 1; padding: 16px 24px 20px; }

.chat-scroll { flex: 1; overflow-y: auto; min-height: 240px; max-height: 46vh; padding-right: 6px; }
.msg { display: flex; margin-bottom: 12px; }
.msg.user { justify-content: flex-end; }
.bubble {
  max-width: 82%; padding: 10px 14px; border-radius: 12px;
  font-size: 14px; line-height: 1.5; white-space: pre-wrap;
}
.msg.assistant .bubble { background: #f5f5f5; color: #111; border-top-left-radius: 2px; }
.msg.user .bubble { background: #FF4500; color: #fff; border-top-right-radius: 2px; }
.bubble.busy { color: #999; font-style: italic; }

.chat-input-row { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.chat-input {
  width: 100%; resize: vertical; border: 1px solid #ddd; border-radius: 6px;
  padding: 10px 12px; font-size: 14px; font-family: inherit;
}
.chat-input:focus { outline: none; border-color: #FF4500; }
.chat-actions { display: flex; justify-content: flex-end; gap: 10px; }

.review-heading { font-size: 15px; font-weight: 700; }
.review-hint { font-size: 12.5px; color: #666; margin: 4px 0 10px; }
.review-doc {
  flex: 1; min-height: 320px; max-height: 52vh; width: 100%; resize: vertical;
  border: 1px solid #ddd; border-radius: 6px; padding: 14px;
  font-family: 'JetBrains Mono', monospace; font-size: 13px; line-height: 1.55;
}
.review-doc:focus { outline: none; border-color: #FF4500; }
.review-actions { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.review-actions .spacer { flex: 1; }

.btn {
  padding: 9px 16px; border-radius: 6px; font-size: 13.5px; font-weight: 600;
  cursor: pointer; font-family: inherit; border: 1px solid transparent;
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.ghost { background: #fff; border-color: #ddd; color: #333; }
.btn.ghost:hover:not(:disabled) { border-color: #999; }
.btn.primary { background: #FF4500; color: #fff; }
.btn.primary:hover:not(:disabled) { background: #e63e00; }

.seed-error {
  background: #fff2ee; border: 1px solid #ffccbc; color: #b33; padding: 8px 12px;
  border-radius: 6px; font-size: 13px; margin-top: 10px;
}
</style>
