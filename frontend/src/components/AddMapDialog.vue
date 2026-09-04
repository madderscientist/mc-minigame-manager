<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { api, ApiError } from '../api/client'
import type { PaperVersion, ServerSettings } from '../api/types'
import { formatBytes } from '../utils/format'
import MapRequirements from './MapRequirements.vue'
import ServerSettingsFields from './ServerSettingsFields.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; added: [mapId: number] }>()
const mode = ref<'upload' | 'generated'>('upload')
const mapFile = ref<File | null>(null)
const generatedName = ref('')
const mcVersion = ref('')
const versions = ref<PaperVersion[]>([])
const versionsUnavailable = ref(false)
const resourcePack = ref<File | null>(null)
const resourcePackRequired = ref(false)
const resourcePackPrompt = ref('')
const paperBuild = ref('')
const paperUrl = ref('')
const paperSha256 = ref('')
const artifactAdvanced = ref(false)
const settingsOpen = ref(false)
const uploadSettings = ref<ServerSettings>(emptySettings())
const generatedSettings = ref<ServerSettings>(emptySettings())
const progress = ref(0)
const phase = ref<'idle' | 'uploading' | 'validating' | 'creating'>('idle')
const error = ref('')
const abortController = ref<AbortController | null>(null)
const busy = computed(() => phase.value !== 'idle')
const mapName = computed(() => mapFile.value?.name.replace(/\.zip$/i, '').trim() || 'Minecraft 地图')
const maxUploadBytes = 2 * 1024 * 1024 * 1024
const maxResourcePackBytes = 250 * 1024 * 1024

function emptySettings(): ServerSettings {
  return { custom: {} }
}

watch(() => props.open, async (open) => {
  if (!open) return
  mode.value = 'upload'
  mapFile.value = null
  generatedName.value = ''
  mcVersion.value = ''
  resourcePack.value = null
  resourcePackRequired.value = false
  resourcePackPrompt.value = ''
  paperBuild.value = ''
  paperUrl.value = ''
  paperSha256.value = ''
  artifactAdvanced.value = false
  settingsOpen.value = false
  uploadSettings.value = emptySettings()
  generatedSettings.value = emptySettings()
  progress.value = 0
  phase.value = 'idle'
  error.value = ''
  abortController.value = null
  versionsUnavailable.value = false
  try {
    versions.value = await api.paperVersions()
    mcVersion.value = versions.value[0]?.version ?? ''
  } catch {
    versions.value = []
    versionsUnavailable.value = true
  }
})

function confirmUploadNavigation(event: BeforeUnloadEvent) {
  if (!busy.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch(busy, (active) => {
  if (active) window.addEventListener('beforeunload', confirmUploadNavigation)
  else window.removeEventListener('beforeunload', confirmUploadNavigation)
})

onBeforeRouteLeave(() => {
  if (!busy.value) return true
  if (phase.value !== 'uploading') {
    window.alert('服务器正在创建地图模板，请等待操作完成后再离开。')
    return false
  }
  if (!window.confirm('地图仍在上传，离开将取消本次上传。确定继续吗？')) return false
  abortController.value?.abort()
  return true
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', confirmUploadNavigation)
  if (phase.value === 'uploading') abortController.value?.abort()
})

function selectMap(event: Event) {
  mapFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

function selectResourcePack(event: Event) {
  resourcePack.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

function validatePaperArtifact(): boolean {
  if (Boolean(paperUrl.value.trim()) !== Boolean(paperSha256.value.trim())) {
    error.value = 'Paper URL 和 SHA-256 必须同时填写'
    return false
  }
  if (paperSha256.value && !/^[a-fA-F0-9]{64}$/.test(paperSha256.value.trim())) {
    error.value = 'Paper SHA-256 必须是 64 位十六进制'
    return false
  }
  return true
}

async function submitUpload() {
  if (!mapFile.value) return
  if (resourcePack.value && resourcePack.value.size > maxResourcePackBytes) {
    error.value = '玩家资源包不能超过 250 MiB'
    return
  }
  if (mapFile.value.size + (resourcePack.value?.size ?? 0) > maxUploadBytes) {
    error.value = '地图和资源文件总大小不能超过 2 GiB'
    return
  }
  if (!validatePaperArtifact()) return
  error.value = ''
  phase.value = 'uploading'
  abortController.value = new AbortController()
  try {
    const result = await api.uploadMap({
      mapFile: mapFile.value,
      name: mapName.value,
      paperBuild: paperBuild.value.trim() || undefined,
      resourcePack: resourcePack.value ?? undefined,
      resourcePackRequired: resourcePackRequired.value,
      resourcePackPrompt: resourcePackPrompt.value.trim() || undefined,
      paperUrl: paperUrl.value.trim() || undefined,
      paperSha256: paperSha256.value.trim() || undefined,
      serverSettings: uploadSettings.value,
      signal: abortController.value.signal,
    }, (value) => {
      progress.value = value
      if (value >= 1) phase.value = 'validating'
    })
    emit('added', result.map_id)
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.code === 'upload_canceled'
      ? ''
      : reason instanceof ApiError ? reason.message : '地图上传失败'
    phase.value = 'idle'
  } finally {
    abortController.value = null
  }
}

async function submitGenerated() {
  if (!generatedName.value.trim() || !mcVersion.value.trim() || !validatePaperArtifact()) return
  error.value = ''
  phase.value = 'creating'
  try {
    const result = await api.generateMap({
      name: generatedName.value.trim(),
      mc_version: mcVersion.value.trim(),
      paper_build: paperBuild.value.trim() || undefined,
      paper_url: paperUrl.value.trim() || undefined,
      paper_sha256: paperSha256.value.trim() || undefined,
      server_settings: generatedSettings.value,
    })
    emit('added', result.map_id)
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : '地图模板创建失败'
    phase.value = 'idle'
  }
}

async function submit() {
  if (busy.value) return
  if (mode.value === 'upload') await submitUpload()
  else await submitGenerated()
}

function cancel() {
  if (phase.value === 'uploading') abortController.value?.abort()
  else if (!busy.value) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop upload-backdrop">
      <section class="dialog-card upload-dialog" role="dialog" aria-modal="true" aria-labelledby="add-map-title">
        <div class="eyebrow">Map template</div>
        <h2 id="add-map-title">添加地图</h2>
        <div class="mode-tabs" role="tablist" aria-label="地图来源">
          <button type="button" role="tab" :aria-selected="mode === 'upload'" :class="{ active: mode === 'upload' }" :disabled="busy" @click="mode = 'upload'">上传地图</button>
          <button type="button" role="tab" :aria-selected="mode === 'generated'" :class="{ active: mode === 'generated' }" :disabled="busy" @click="mode = 'generated'">自然生成</button>
        </div>

        <div v-if="mode === 'upload'" role="tabpanel" class="mode-panel">
          <MapRequirements compact />
          <div class="upload-grid">
            <label class="drop-zone"><input type="file" accept=".zip,application/zip" @change="selectMap" /><span class="drop-icon">⇧</span><strong>{{ mapFile?.name ?? '选择 map.zip' }}</strong><small>{{ mapFile ? formatBytes(mapFile.size) : '必须包含 level.dat，最大 2 GiB' }}</small></label>
            <div class="form-stack"><div class="selection-summary"><strong>{{ mapName }}</strong><span>地图名称取自 ZIP 文件名</span></div><div class="selection-summary"><strong>Minecraft 版本自动识别</strong><span>后端读取 level.dat</span></div></div>
          </div>
          <section class="resource-pack-picker">
            <div class="resource-pack-heading"><div><strong>玩家资源包 <small>可选，仅一个 ZIP</small></strong><p>单独上传的资源包优先于地图根目录的 resources.zip。</p></div><label class="button ghost small resource-pack-file"><input type="file" accept=".zip,application/zip" @change="selectResourcePack" />{{ resourcePack ? '更换文件' : '选择资源包' }}</label></div>
            <div v-if="resourcePack" class="resource-pack-options"><div class="selected-pack"><span>▣</span><div><strong>{{ resourcePack.name }}</strong><small>{{ formatBytes(resourcePack.size) }} · 上限 250 MiB</small></div><button type="button" aria-label="移除资源包" @click="resourcePack = null">×</button></div><label class="check-field"><input v-model="resourcePackRequired" type="checkbox" /><span><strong>要求玩家接受资源包</strong><small>拒绝后无法进入游戏。</small></span></label><label class="field"><span>下载提示 <small>可选，最多 256 字</small></span><input v-model="resourcePackPrompt" maxlength="256" /></label></div>
          </section>
        </div>

        <div v-else role="tabpanel" class="mode-panel generated-fields">
          <label class="field"><span>模板名称</span><input v-model="generatedName" maxlength="255" placeholder="例如：随机速通世界" /></label>
          <label class="field"><span>Minecraft 版本</span><select v-if="versions.length" v-model="mcVersion"><option v-for="item in versions" :key="item.version" :value="item.version">Minecraft {{ item.version }} · Java {{ item.java_major }}</option></select><input v-else v-model="mcVersion" placeholder="例如 1.21.11" /></label>
          <p v-if="versionsUnavailable" class="field-note">版本目录暂时不可用，请手工填写版本号。</p>
          <div class="selection-summary"><strong>首次启动生成世界</strong><span>每个游戏副本独立生成；种子留空时每局随机。</span></div>
        </div>

        <button type="button" class="advanced-toggle" @click="settingsOpen = !settingsOpen">{{ settingsOpen ? '−' : '＋' }} 服务端设置</button>
        <div v-if="settingsOpen" class="advanced-panel settings-panel"><ServerSettingsFields v-if="mode === 'upload'" v-model="uploadSettings" /><ServerSettingsFields v-else v-model="generatedSettings" world-generation /></div>
        <button type="button" class="advanced-toggle" @click="artifactAdvanced = !artifactAdvanced">{{ artifactAdvanced ? '−' : '＋' }} 高级 Paper 制品设置</button>
        <div v-if="artifactAdvanced" class="advanced-panel"><label class="field"><span>固定 Paper build <small>留空自动选择稳定版</small></span><input v-model="paperBuild" placeholder="例如 497" /></label><label class="field"><span>Paper HTTPS URL</span><input v-model="paperUrl" placeholder="https://…/paper.jar" /></label><label class="field"><span>SHA-256</span><input v-model="paperSha256" maxlength="64" placeholder="64 位校验值" /></label></div>

        <div v-if="busy" class="upload-progress"><div><span>{{ phase === 'uploading' ? '正在上传' : phase === 'validating' ? '服务器正在解压并校验' : '正在创建模板' }}</span><strong>{{ phase === 'uploading' ? `${Math.round(progress * 100)}%` : '请稍候' }}</strong></div><div class="progress-track" :class="{ indeterminate: phase !== 'uploading' }"><span :style="{ width: `${progress * 100}%` }" /></div></div>
        <p v-if="error" class="inline-error">{{ error }}</p>
        <div class="dialog-actions"><button class="button ghost" :disabled="phase === 'validating' || phase === 'creating'" @click="cancel">{{ phase === 'uploading' ? '取消上传' : '取消' }}</button><button class="button primary" :disabled="busy || (mode === 'upload' ? !mapFile : !generatedName.trim() || !mcVersion.trim())" @click="submit">{{ busy ? '处理中…' : mode === 'upload' ? '上传并导入' : '创建模板' }}</button></div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.mode-tabs { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid var(--line); margin: 6px 0 18px; }
.mode-tabs button { min-height: 42px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); font: inherit; font-weight: 700; cursor: pointer; }
.mode-tabs button.active { border-color: var(--accent); color: var(--text); }
.mode-tabs button:disabled { cursor: not-allowed; opacity: .6; }
.mode-panel, .generated-fields { display: grid; gap: 16px; }
.generated-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.generated-fields .selection-summary, .generated-fields .field-note { grid-column: 1 / -1; }
.settings-panel { max-height: 46vh; overflow: auto; }
.field-note { margin: -8px 0 0; color: var(--muted); font-size: .85rem; }
@media (max-width: 640px) { .generated-fields { grid-template-columns: 1fr; } }
</style>
