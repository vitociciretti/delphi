<template>
  <div class="set-overlay" @click.self="$emit('close')">
    <div class="set-modal">
      <div class="set-header">
        <div>
          <div class="set-title">Model &amp; API key</div>
          <div class="set-sub">Bring your own LLM. Any OpenAI-compatible provider works — free local, free tier, or a paid key.</div>
        </div>
        <button class="set-close" @click="$emit('close')">×</button>
      </div>

      <div class="set-body">
        <!-- Provider -->
        <label class="field-label">Provider</label>
        <select class="field-input" v-model="providerId" @change="onProviderChange">
          <optgroup v-for="grp in groups" :key="grp.label" :label="grp.label">
            <option v-for="p in grp.items" :key="p.id" :value="p.id">{{ p.label }}</option>
          </optgroup>
        </select>

        <div v-if="current" class="provider-note">
          <span class="cost-tag" :class="current.cost">{{ costLabel(current.cost) }}</span>
          <span>{{ current.note }}</span>
          <a v-if="current.link" :href="current.link" target="_blank" class="note-link">{{ current.linkLabel || 'Get key ↗' }}</a>
        </div>

        <!-- Base URL -->
        <label class="field-label">Base URL</label>
        <input class="field-input mono" v-model="baseUrl" placeholder="https://api.provider.com/v1" spellcheck="false" />

        <!-- Model -->
        <label class="field-label">Model</label>
        <input class="field-input mono" v-model="model" :placeholder="current?.modelHint || 'model-name'" spellcheck="false" />

        <!-- API key -->
        <label class="field-label">
          API key
          <span v-if="current && !current.needsKey" class="opt-tag">not required for local</span>
        </label>
        <input
          class="field-input mono"
          type="password"
          v-model="apiKey"
          :placeholder="keyPlaceholder"
          spellcheck="false"
          autocomplete="off"
        />

        <div v-if="testResult" class="test-result" :class="{ ok: testResult.ok, err: !testResult.ok }">
          <span v-if="testResult.ok">✓ Connected — model replied “{{ testResult.reply }}”</span>
          <span v-else>✕ {{ testResult.error }}</span>
        </div>
        <div v-if="saved" class="test-result ok">✓ Saved. The whole pipeline will use this now.</div>
      </div>

      <div class="set-footer">
        <button class="btn ghost" :disabled="busy" @click="doTest">
          {{ testing ? 'Testing…' : 'Test connection' }}
        </button>
        <div class="spacer"></div>
        <button class="btn ghost" :disabled="busy" @click="$emit('close')">Cancel</button>
        <button class="btn primary" :disabled="busy || !canSave" @click="doSave">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getLlmSettings, saveLlmSettings, testLlmSettings } from '../api/settings'

const emit = defineEmits(['close', 'saved'])

// Provider presets, grouped by cost.
const PROVIDERS = [
  // Free · Local
  { id: 'ollama', label: 'Ollama (local)', cost: 'free-local', base_url: 'http://localhost:11434/v1', model: 'llama3.1', modelHint: 'llama3.1 / qwen2.5 / mistral', needsKey: false,
    note: 'Runs on your Mac, $0 and private. Install Ollama then `ollama pull llama3.1`.', link: 'https://ollama.com', linkLabel: 'Install Ollama ↗' },
  { id: 'lmstudio', label: 'LM Studio (local)', cost: 'free-local', base_url: 'http://localhost:1234/v1', model: 'local-model', modelHint: 'the model id shown in LM Studio', needsKey: false,
    note: 'Local server, $0 and private. Start the LM Studio server, then load a model.', link: 'https://lmstudio.ai', linkLabel: 'Get LM Studio ↗' },
  // Free · API tier
  { id: 'groq', label: 'Groq (free tier)', cost: 'free-tier', base_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile', modelHint: 'llama-3.3-70b-versatile', needsKey: true,
    note: 'Very fast, generous free tier. Rate-limited, so best for the chat + small runs.', link: 'https://console.groq.com/keys', linkLabel: 'Free key ↗' },
  { id: 'gemini', label: 'Google Gemini (free tier)', cost: 'free-tier', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/', model: 'gemini-2.0-flash', modelHint: 'gemini-2.0-flash', needsKey: true,
    note: 'Free tier via Google AI Studio. Rate-limited on the free plan.', link: 'https://aistudio.google.com/apikey', linkLabel: 'Free key ↗' },
  { id: 'openrouter', label: 'OpenRouter (free models)', cost: 'free-tier', base_url: 'https://openrouter.ai/api/v1', model: 'meta-llama/llama-3.3-70b-instruct:free', modelHint: 'any model id ending in :free', needsKey: true,
    note: 'Free with any model id ending in “:free”. Rate-limited.', link: 'https://openrouter.ai/keys', linkLabel: 'Free key ↗' },
  // Paid · bring key
  { id: 'openai', label: 'OpenAI', cost: 'paid', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', modelHint: 'gpt-4o-mini / gpt-4o', needsKey: true,
    note: 'gpt-4o-mini is cheap and solid; gpt-4o for best quality.', link: 'https://platform.openai.com/api-keys', linkLabel: 'Get key ↗' },
  { id: 'anthropic', label: 'Anthropic (Claude)', cost: 'paid', base_url: 'https://api.anthropic.com/v1/', model: 'claude-3-5-sonnet-latest', modelHint: 'claude-3-5-sonnet-latest / claude-3-5-haiku-latest', needsKey: true,
    note: 'Claude via its OpenAI-compatible endpoint. Edit the model id if needed.', link: 'https://console.anthropic.com/settings/keys', linkLabel: 'Get key ↗' },
  { id: 'qwen', label: 'Alibaba Qwen (DashScope)', cost: 'paid', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', modelHint: 'qwen-plus / qwen-max', needsKey: true,
    note: 'The engine’s original default. Cheap and capable.', link: 'https://bailian.console.aliyun.com/', linkLabel: 'Get key ↗' },
  { id: 'deepseek', label: 'DeepSeek', cost: 'paid', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', modelHint: 'deepseek-chat', needsKey: true,
    note: 'Very low cost per token.', link: 'https://platform.deepseek.com/api_keys', linkLabel: 'Get key ↗' },
  // Custom
  { id: 'custom', label: 'Custom (any OpenAI-compatible)', cost: 'custom', base_url: '', model: '', modelHint: 'model-name', needsKey: false,
    note: 'Point at any OpenAI-compatible endpoint.', link: '' },
]

const groups = [
  { label: 'Free · Local', items: PROVIDERS.filter(p => p.cost === 'free-local') },
  { label: 'Free · API tier', items: PROVIDERS.filter(p => p.cost === 'free-tier') },
  { label: 'Paid · bring your key', items: PROVIDERS.filter(p => p.cost === 'paid') },
  { label: 'Custom', items: PROVIDERS.filter(p => p.cost === 'custom') },
]

const providerId = ref('ollama')
const baseUrl = ref('')
const model = ref('')
const apiKey = ref('')
const hasSavedKey = ref(false)

const testing = ref(false)
const saving = ref(false)
const testResult = ref(null)
const saved = ref(false)

const current = computed(() => PROVIDERS.find(p => p.id === providerId.value))
const busy = computed(() => testing.value || saving.value)
const canSave = computed(() => baseUrl.value.trim() && model.value.trim())
const keyPlaceholder = computed(() => {
  if (hasSavedKey.value && !apiKey.value) return '•••• (saved — leave blank to keep)'
  if (current.value && !current.value.needsKey) return 'not required'
  return 'sk-…'
})

const costLabel = (c) => ({ 'free-local': 'FREE · LOCAL', 'free-tier': 'FREE · API', 'paid': 'PAID', 'custom': 'CUSTOM' }[c] || c)

const onProviderChange = () => {
  testResult.value = null
  saved.value = false
  if (current.value) {
    baseUrl.value = current.value.base_url
    model.value = current.value.model
    apiKey.value = ''
  }
}

const doTest = async () => {
  testing.value = true
  testResult.value = null
  saved.value = false
  try {
    const res = await testLlmSettings({ api_key: apiKey.value, base_url: baseUrl.value, model: model.value })
    testResult.value = res.success ? res.data : { ok: false, error: res.error }
  } catch (e) {
    testResult.value = { ok: false, error: e.message }
  } finally {
    testing.value = false
  }
}

const doSave = async () => {
  saving.value = true
  saved.value = false
  try {
    const res = await saveLlmSettings({
      provider: providerId.value,
      api_key: apiKey.value,
      base_url: baseUrl.value,
      model: model.value,
    })
    if (res.success) {
      saved.value = true
      hasSavedKey.value = res.data.has_key
      apiKey.value = ''
      emit('saved', res.data)
    } else {
      testResult.value = { ok: false, error: res.error }
    }
  } catch (e) {
    testResult.value = { ok: false, error: e.message }
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getLlmSettings()
    if (res.success) {
      const d = res.data
      hasSavedKey.value = d.has_key
      if (d.provider && PROVIDERS.find(p => p.id === d.provider)) {
        providerId.value = d.provider
      } else {
        // Infer provider from a saved base_url, else Custom.
        const match = PROVIDERS.find(p => p.base_url && d.base_url && d.base_url.startsWith(p.base_url.replace(/\/$/, '')))
        providerId.value = match ? match.id : 'custom'
      }
      baseUrl.value = d.base_url || (current.value?.base_url ?? '')
      model.value = d.model || (current.value?.model ?? '')
    }
  } catch (e) {
    // fall back to defaults
    onProviderChange()
  }
})
</script>

<style scoped>
.set-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  display: flex; align-items: center; justify-content: center; z-index: 1100; padding: 24px;
}
.set-modal {
  width: min(560px, 100%); max-height: 90vh; background: #fff; border: 1px solid #e5e5e5;
  border-radius: 6px; display: flex; flex-direction: column; overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}
.set-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding: 20px 24px; border-bottom: 1px solid #eee; }
.set-title { font-size: 18px; font-weight: 700; }
.set-sub { font-size: 12.5px; color: #666; margin-top: 4px; line-height: 1.45; max-width: 440px; }
.set-close { border: none; background: none; font-size: 26px; line-height: 1; cursor: pointer; color: #999; }
.set-close:hover { color: #000; }

.set-body { padding: 18px 24px; overflow-y: auto; }
.field-label { display: block; font-size: 12px; font-weight: 600; color: #444; margin: 14px 0 6px; }
.field-label:first-child { margin-top: 0; }
.field-input {
  width: 100%; border: 1px solid #ddd; border-radius: 6px; padding: 9px 11px; font-size: 13.5px;
  font-family: inherit; background: #fff;
}
.field-input.mono { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; }
.field-input:focus { outline: none; border-color: #FF4500; }

.provider-note {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 10px; font-size: 12px; color: #666; line-height: 1.4;
}
.cost-tag {
  font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 700;
  padding: 2px 6px; border-radius: 3px; letter-spacing: 0.3px; white-space: nowrap;
}
.cost-tag.free-local { color: #0a7d34; background: rgba(10,125,52,0.1); }
.cost-tag.free-tier { color: #1a6ec4; background: rgba(26,110,196,0.1); }
.cost-tag.paid { color: #b26a00; background: rgba(178,106,0,0.1); }
.cost-tag.custom { color: #666; background: #f0f0f0; }
.note-link { color: #FF4500; text-decoration: none; white-space: nowrap; }
.note-link:hover { text-decoration: underline; }
.opt-tag { font-weight: 400; color: #999; font-size: 11px; }

.test-result { margin-top: 12px; font-size: 12.5px; padding: 8px 11px; border-radius: 6px; line-height: 1.4; }
.test-result.ok { color: #0a7d34; background: rgba(10,125,52,0.08); border: 1px solid rgba(10,125,52,0.25); }
.test-result.err { color: #b33; background: #fff2ee; border: 1px solid #ffccbc; word-break: break-word; }

.set-footer { display: flex; align-items: center; gap: 10px; padding: 16px 24px; border-top: 1px solid #eee; }
.set-footer .spacer { flex: 1; }
.btn { padding: 9px 16px; border-radius: 6px; font-size: 13.5px; font-weight: 600; cursor: pointer; font-family: inherit; border: 1px solid transparent; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.ghost { background: #fff; border-color: #ddd; color: #333; }
.btn.ghost:hover:not(:disabled) { border-color: #999; }
.btn.primary { background: #FF4500; color: #fff; }
.btn.primary:hover:not(:disabled) { background: #e63e00; }
</style>
