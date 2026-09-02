<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { api, ApiError } from '../api/client'
import { formatBytes } from '../utils/format'
import { extractMinecraftVersion, requiredJavaMajor } from '../utils/java'
import MapRequirements from './MapRequirements.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; uploaded: [mapId: number] }>()
const mapFile = ref<File | null>(null)
const resourcePack = ref<File | null>(null)
const resourcePackRequired = ref(false)
const resourcePackPrompt = ref('')
const name = ref('')
const mcVersion = ref('')
const lastInferredVersion = ref('')
const paperBuild = ref('')
const paperUrl = ref('')
const paperSha256 = ref('')
const advanced = ref(false)
const progress = ref(0)
const phase = ref<'idle' | 'uploading' | 'validating'>('idle')
const error = ref('')
const busy = computed(() => phase.value !== 'idle')
const validMcVersion = computed(() => requiredJavaMajor(mcVersion.value) !== null)
const maxUploadBytes = 2 * 1024 * 1024 * 1024
const maxResourcePackBytes = 250 * 1024 * 1024

watch(() => props.open, (open) => {
  if (!open) return
  mapFile.value = null; resourcePack.value = null; resourcePackRequired.value = false; resourcePackPrompt.value = ''; name.value = ''; mcVersion.value = ''; lastInferredVersion.value = ''; paperBuild.value = ''; paperUrl.value = ''; paperSha256.value = ''; advanced.value = false; progress.value = 0; phase.value = 'idle'; error.value = ''
})

function confirmUploadNavigation(event: BeforeUnloadEvent) {
  if (!busy.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch(busy, (uploading) => {
  if (uploading) window.addEventListener('beforeunload', confirmUploadNavigation)
  else window.removeEventListener('beforeunload', confirmUploadNavigation)
})

onBeforeUnmount(() => window.removeEventListener('beforeunload', confirmUploadNavigation))

watch(name, (value) => {
  const inferred = extractMinecraftVersion(value)
  if (inferred && (!mcVersion.value.trim() || mcVersion.value === lastInferredVersion.value)) {
    mcVersion.value = inferred
    lastInferredVersion.value = inferred
  }
})

function selectMap(event: Event) {
  const previousDefault = mapFile.value?.name.replace(/\.zip$/i, '') ?? ''
  const selected = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selected && (!name.value.trim() || name.value === previousDefault)) {
    name.value = selected.name.replace(/\.zip$/i, '')
  }
  const inferred = selected ? extractMinecraftVersion(selected.name) : null
  if (inferred && (!mcVersion.value.trim() || mcVersion.value === lastInferredVersion.value)) {
    mcVersion.value = inferred
    lastInferredVersion.value = inferred
  }
  mapFile.value = selected
}
function selectResourcePack(event: Event) {
  resourcePack.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function submit() {
  if (!mapFile.value || !name.value.trim() || !mcVersion.value.trim() || !validMcVersion.value) return
  if (resourcePack.value && resourcePack.value.size > maxResourcePackBytes) { error.value = '玩家资源包不能超过 250 MiB'; return }
  const totalBytes = mapFile.value.size + (resourcePack.value?.size ?? 0)
  if (totalBytes > maxUploadBytes) { error.value = '地图和资源文件总大小不能超过 2 GiB'; return }
  if (Boolean(paperUrl.value) !== Boolean(paperSha256.value)) { error.value = 'Paper URL 和 SHA-256 必须同时填写'; return }
  if (paperSha256.value && !/^[a-fA-F0-9]{64}$/.test(paperSha256.value)) { error.value = 'Paper SHA-256 必须是 64 位十六进制'; return }
  error.value = ''; phase.value = 'uploading'
  try {
    const result = await api.uploadMap({ mapFile: mapFile.value, name: name.value.trim(), mcVersion: mcVersion.value.trim(), paperBuild: paperBuild.value.trim() || undefined, resourcePack: resourcePack.value ?? undefined, resourcePackRequired: resourcePackRequired.value, resourcePackPrompt: resourcePackPrompt.value.trim() || undefined, paperUrl: paperUrl.value || undefined, paperSha256: paperSha256.value || undefined }, (value) => { progress.value = value; if (value >= 1) phase.value = 'validating' })
    emit('uploaded', result.map_id)
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : '地图上传失败'
    phase.value = 'idle'
  }
}
</script>

<template>
  <Teleport to="body"><div v-if="open" class="dialog-backdrop upload-backdrop"><section class="dialog-card upload-dialog">
    <div class="eyebrow">Import map</div><h2>上传仓库地图</h2><p>地图成功导入后保持不可变，可用于创建多个独立游戏。</p>
    <MapRequirements compact />
    <div class="upload-grid">
      <label class="drop-zone"><input type="file" accept=".zip,application/zip" @change="selectMap" /><span class="drop-icon">⇧</span><strong>{{ mapFile?.name ?? '选择 map.zip' }}</strong><small>{{ mapFile ? formatBytes(mapFile.size) : '必须包含 level.dat，最大 2 GiB' }}</small></label>
      <div class="form-stack">
        <label class="field"><span>地图名称</span><input v-model="name" maxlength="255" placeholder="例如：SkyWars 经典岛屿" /></label>
        <label class="field"><span>Minecraft 版本 <small>可从文件名或地图名称自动识别</small></span><input v-model="mcVersion" placeholder="例如 1.20.4" /></label>
      </div>
    </div>
    <section class="resource-pack-picker">
      <div class="resource-pack-heading"><div><strong>玩家资源包 <small>可选，仅一个 ZIP</small></strong><p>未选择时自动使用地图根目录的 <code>resources.zip</code>；单独上传的资源包优先。</p></div><label class="button ghost small resource-pack-file"><input type="file" accept=".zip,application/zip" @change="selectResourcePack" />{{ resourcePack ? '更换文件' : '选择资源包' }}</label></div>
      <div v-if="resourcePack" class="resource-pack-options">
        <div class="selected-pack"><span>▣</span><div><strong>{{ resourcePack.name }}</strong><small>{{ formatBytes(resourcePack.size) }} · 上限 250 MiB</small></div><button type="button" aria-label="移除资源包" @click="resourcePack=null">×</button></div>
        <label class="check-field"><input v-model="resourcePackRequired" type="checkbox" /><span><strong>要求玩家接受资源包</strong><small>拒绝后无法进入游戏；关闭时玩家仍可选择拒绝。</small></span></label>
        <label class="field"><span>下载提示 <small>可选，最多 256 字</small></span><input v-model="resourcePackPrompt" maxlength="256" placeholder="例如：需要此材质包才能正常游玩" /></label>
      </div>
    </section>
    <button class="advanced-toggle" @click="advanced=!advanced">{{ advanced?'−':'＋' }} 高级 Paper 制品设置</button>
    <div v-if="advanced" class="advanced-panel"><label class="field"><span>固定 Paper build <small>可选，留空使用最新稳定版/已有的最新版</small></span><input v-model="paperBuild" placeholder="例如 497" /></label><label class="field"><span>Paper HTTPS URL</span><input v-model="paperUrl" placeholder="https://…/paper.jar" /></label><label class="field"><span>SHA-256</span><input v-model="paperSha256" maxlength="64" placeholder="64 位校验值" /></label></div>
    <div v-if="busy" class="upload-progress"><div><span>{{ phase==='uploading'?'正在上传':'服务器正在解压并校验' }}</span><strong>{{ phase==='uploading'?`${Math.round(progress*100)}%`:'请稍候' }}</strong></div><div class="progress-track" :class="{indeterminate:phase==='validating'}"><span :style="{width:`${progress*100}%`} " /></div></div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div class="dialog-actions"><button class="button ghost" :disabled="busy" @click="emit('close')">取消</button><button class="button primary" :disabled="!mapFile||!name.trim()||!mcVersion.trim()||!validMcVersion||busy" @click="submit">{{ busy?'正在导入…':'上传并导入' }}</button></div>
  </section></div></Teleport>
</template>
