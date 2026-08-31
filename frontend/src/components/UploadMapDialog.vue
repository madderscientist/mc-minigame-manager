<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { api, ApiError } from '../api/client'
import { formatBytes } from '../utils/format'
import MapRequirements from './MapRequirements.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; uploaded: [mapId: number] }>()
const mapFile = ref<File | null>(null)
const resources = ref<File[]>([])
const resourcePack = ref<File | null>(null)
const resourcePackRequired = ref(false)
const resourcePackPrompt = ref('')
const name = ref('')
const mcVersion = ref('1.20.4')
const paperBuild = ref('')
const javaMajor = ref(17)
const paperUrl = ref('')
const paperSha256 = ref('')
const advanced = ref(false)
const progress = ref(0)
const phase = ref<'idle' | 'uploading' | 'validating'>('idle')
const error = ref('')
const busy = computed(() => phase.value !== 'idle')
const maxUploadBytes = 2 * 1024 * 1024 * 1024
const maxResourcePackBytes = 250 * 1024 * 1024

watch(() => props.open, (open) => {
  if (!open) return
  mapFile.value = null; resources.value = []; resourcePack.value = null; resourcePackRequired.value = false; resourcePackPrompt.value = ''; name.value = ''; paperBuild.value = ''; paperUrl.value = ''; paperSha256.value = ''; progress.value = 0; phase.value = 'idle'; error.value = ''
})

function selectMap(event: Event) {
  mapFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
function selectResources(event: Event) {
  resources.value = [...((event.target as HTMLInputElement).files ?? [])]
}
function selectResourcePack(event: Event) {
  resourcePack.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
function removeResource(index: number) { resources.value.splice(index, 1) }

async function submit() {
  if (!mapFile.value || !name.value.trim() || !mcVersion.value.trim() || !paperBuild.value.trim()) return
  if (resourcePack.value && resourcePack.value.size > maxResourcePackBytes) { error.value = '玩家资源包不能超过 250 MiB'; return }
  const totalBytes = mapFile.value.size + (resourcePack.value?.size ?? 0) + resources.value.reduce((total, file) => total + file.size, 0)
  if (totalBytes > maxUploadBytes) { error.value = '地图和资源文件总大小不能超过 2 GiB'; return }
  if (Boolean(paperUrl.value) !== Boolean(paperSha256.value)) { error.value = 'Paper URL 和 SHA-256 必须同时填写'; return }
  if (paperSha256.value && !/^[a-fA-F0-9]{64}$/.test(paperSha256.value)) { error.value = 'Paper SHA-256 必须是 64 位十六进制'; return }
  error.value = ''; phase.value = 'uploading'
  try {
    const result = await api.uploadMap({ mapFile: mapFile.value, name: name.value.trim(), mcVersion: mcVersion.value.trim(), paperBuild: paperBuild.value.trim(), javaMajor: javaMajor.value, resources: resources.value, resourcePack: resourcePack.value ?? undefined, resourcePackRequired: resourcePackRequired.value, resourcePackPrompt: resourcePackPrompt.value.trim() || undefined, paperUrl: paperUrl.value || undefined, paperSha256: paperSha256.value || undefined }, (value) => { progress.value = value; if (value >= 1) phase.value = 'validating' })
    emit('uploaded', result.map_id)
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : '地图上传失败'
    phase.value = 'idle'
  }
}
</script>

<template>
  <Teleport to="body"><div v-if="open" class="dialog-backdrop upload-backdrop" @click.self="!busy && emit('close')"><section class="dialog-card upload-dialog">
    <div class="eyebrow">Import map</div><h2>上传仓库地图</h2><p>地图成功导入后保持不可变，可用于创建多个独立游戏。</p>
    <MapRequirements compact />
    <div class="upload-grid">
      <label class="drop-zone"><input type="file" accept=".zip,application/zip" @change="selectMap" /><span class="drop-icon">⇧</span><strong>{{ mapFile?.name ?? '选择 map.zip' }}</strong><small>{{ mapFile ? formatBytes(mapFile.size) : '必须包含 level.dat，最大 2 GiB' }}</small></label>
      <div class="form-stack">
        <label class="field"><span>地图名称</span><input v-model="name" maxlength="255" placeholder="例如：SkyWars 经典岛屿" /></label>
        <div class="field-row"><label class="field"><span>Minecraft 版本</span><input v-model="mcVersion" placeholder="1.20.4" /></label><label class="field"><span>Paper build</span><input v-model="paperBuild" placeholder="497" /></label></div>
        <label class="field"><span>Java 主版本</span><select v-model="javaMajor"><option v-for="version in [8,11,16,17,21]" :key="version" :value="version">Java {{ version }}</option></select></label>
      </div>
    </div>
    <section class="resource-pack-picker">
      <div class="resource-pack-heading"><div><strong>玩家资源包 <small>可选，仅一个 ZIP</small></strong><p>玩家连接 Paper 时会收到原生下载提示，资源包必须在 ZIP 根目录包含 <code>pack.mcmeta</code>。</p></div><label class="button ghost small resource-pack-file"><input type="file" accept=".zip,application/zip" @change="selectResourcePack" />{{ resourcePack ? '更换文件' : '选择资源包' }}</label></div>
      <div v-if="resourcePack" class="resource-pack-options">
        <div class="selected-pack"><span>▣</span><div><strong>{{ resourcePack.name }}</strong><small>{{ formatBytes(resourcePack.size) }} · 上限 250 MiB</small></div><button type="button" aria-label="移除资源包" @click="resourcePack=null">×</button></div>
        <label class="check-field"><input v-model="resourcePackRequired" type="checkbox" /><span><strong>要求玩家接受资源包</strong><small>拒绝后无法进入游戏；关闭时玩家仍可选择拒绝。</small></span></label>
        <label class="field"><span>下载提示 <small>可选，最多 256 字</small></span><input v-model="resourcePackPrompt" maxlength="256" placeholder="例如：需要此材质包才能正常游玩" /></label>
      </div>
    </section>
    <label class="field resource-picker"><span>附加资源 ZIP <small>可选，可多选</small></span><input type="file" multiple accept=".zip,application/zip" @change="selectResources" /></label>
    <div v-if="resources.length" class="file-chips"><button v-for="(file,index) in resources" :key="`${file.name}-${index}`" @click="removeResource(index)">{{ file.name }} · {{ formatBytes(file.size) }} <b>×</b></button></div>
    <button class="advanced-toggle" @click="advanced=!advanced">{{ advanced?'−':'＋' }} 高级 Paper 制品设置</button>
    <div v-if="advanced" class="advanced-panel"><label class="field"><span>Paper HTTPS URL</span><input v-model="paperUrl" placeholder="https://…/paper.jar" /></label><label class="field"><span>SHA-256</span><input v-model="paperSha256" maxlength="64" placeholder="64 位校验值" /></label></div>
    <div v-if="busy" class="upload-progress"><div><span>{{ phase==='uploading'?'正在上传':'服务器正在解压并校验' }}</span><strong>{{ phase==='uploading'?`${Math.round(progress*100)}%`:'请稍候' }}</strong></div><div class="progress-track" :class="{indeterminate:phase==='validating'}"><span :style="{width:`${progress*100}%`} " /></div></div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div class="dialog-actions"><button class="button ghost" :disabled="busy" @click="emit('close')">取消</button><button class="button primary" :disabled="!mapFile||!name.trim()||!mcVersion.trim()||!paperBuild.trim()||busy" @click="submit">{{ busy?'正在导入…':'上传并导入' }}</button></div>
  </section></div></Teleport>
</template>
