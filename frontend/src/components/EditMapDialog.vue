<script setup lang="ts">
import { ref, watch } from 'vue'

import { api, ApiError } from '../api/client'
import type { MapRecord, ServerSettings, UpdateMapInput } from '../api/types'
import ServerSettingsFields from './ServerSettingsFields.vue'

const props = defineProps<{ open: boolean; map: MapRecord }>()
const emit = defineEmits<{ close: []; saved: [map: MapRecord] }>()
const name = ref('')
const settings = ref<ServerSettings>({ custom: {} })
const required = ref(false)
const prompt = ref('')
const busy = ref(false)
const error = ref('')

watch(() => props.open, (open) => {
  if (!open) return
  name.value = props.map.name
  settings.value = { ...props.map.server_settings, custom: { ...props.map.server_settings.custom } }
  required.value = props.map.resource_pack?.required ?? false
  prompt.value = props.map.resource_pack?.prompt ?? ''
  error.value = ''
}, { immediate: true })

async function save() {
  if (busy.value || !name.value.trim()) return
  busy.value = true
  error.value = ''
  const input: UpdateMapInput = { name: name.value.trim() }
  if (JSON.stringify(settings.value) !== JSON.stringify(props.map.server_settings)) {
    input.server_settings = settings.value
  }
  const pack = props.map.resource_pack
  if (pack) {
    if (required.value !== pack.required) input.resource_pack_required = required.value
    const text = prompt.value.trim() || null
    if (text !== pack.prompt) input.resource_pack_prompt = text
  }
  try {
    emit('saved', await api.updateMap(props.map.map_id, input))
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : '地图保存失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop upload-backdrop">
      <form class="dialog-card upload-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-map-title" @submit.prevent="save">
        <h2 id="edit-map-title">编辑地图</h2>
        <fieldset :disabled="busy" class="edit-fields">
          <label class="field"><span>地图名称</span><input v-model="name" required maxlength="255" /></label>
          <template v-if="map.resource_pack">
            <h3>玩家资源包</h3>
            <label class="check-field"><input v-model="required" type="checkbox" /><span>要求玩家接受资源包</span></label>
            <label class="field"><span>下载提示</span><input v-model="prompt" maxlength="256" /></label>
          </template>
          <h3>默认服务端设置</h3>
          <ServerSettingsFields v-model="settings" :world-generation="map.source_type === 'generated'" />
        </fieldset>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
        <div class="dialog-actions">
          <button type="button" class="button ghost" :disabled="busy" @click="emit('close')">取消</button>
          <button type="submit" class="button primary" :disabled="busy || !name.trim()">{{ busy ? '保存中…' : '保存' }}</button>
        </div>
      </form>
    </div>
  </Teleport>
</template>

<style scoped>
.edit-fields { border: 0; padding: 0; margin: 0; display: grid; gap: 16px; min-width: 0; }
.edit-fields h3 { margin: 8px 0 0; font-size: 1rem; }
</style>