<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { api, ApiError } from '../api/client'
import type { MapRecord, ServerSettings } from '../api/types'
import { useTaskStore } from '../stores/tasks'
import ServerSettingsFields from './ServerSettingsFields.vue'

const props = defineProps<{ open: boolean; maps: MapRecord[]; initialMapId?: number }>()
const emit = defineEmits<{ close: []; created: [gameId: number] }>()
const tasks = useTaskStore()
const mapId = ref<number | null>(null)
const name = ref('')
const busy = ref(false)
const error = ref('')
const settingsOpen = ref(false)
const serverSettings = ref<ServerSettings>({ custom: {} })
const selectedMap = computed(() => props.maps.find((map) => map.map_id === mapId.value))

function copyMapSettings() {
  serverSettings.value = structuredClone(selectedMap.value?.server_settings ?? { custom: {} })
}

watch(() => props.open, (open) => {
  if (!open) return
  mapId.value = props.initialMapId ?? props.maps[0]?.map_id ?? null
  name.value = ''
  error.value = ''
  settingsOpen.value = false
  copyMapSettings()
})

watch(mapId, copyMapSettings)

async function submit() {
  if (!mapId.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const accepted = await api.createGame(
      mapId.value,
      name.value.trim() || undefined,
      serverSettings.value,
    )
    tasks.track(accepted)
    emit('created', accepted.game_id)
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : '创建游戏失败'
  } finally {
    busy.value = false
  }
}
function close() { if (!busy.value) emit('close') }
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop" @click.self="close">
      <section class="dialog-card create-dialog" role="dialog" aria-modal="true">
        <div class="eyebrow">Create game</div><h2>从地图创建游戏</h2>
        <p>只创建持久游戏副本，不会启动 Paper 或占用端口。</p>
        <label class="field"><span>来源地图</span>
          <select v-model="mapId">
            <option v-for="map in maps" :key="map.map_id" :value="map.map_id">{{ map.name }} · #{{ map.map_id }} · MC {{ map.mc_version }}</option>
          </select>
        </label>
        <div v-if="selectedMap" class="selection-summary"><strong>{{ selectedMap.name }}</strong><span>Paper {{ selectedMap.paper_build }} · Java {{ selectedMap.java_major }}</span></div>
        <label class="field"><span>游戏名称 <small>可选</small></span><input v-model="name" maxlength="255" :placeholder="selectedMap?.name ?? '本局名称'" /></label>
        <button type="button" class="advanced-toggle" @click="settingsOpen = !settingsOpen">{{ settingsOpen ? '−' : '＋' }} 游戏规则与服务端设置</button>
        <div v-if="settingsOpen" class="advanced-panel game-settings"><ServerSettingsFields v-model="serverSettings" :world-generation="selectedMap?.source_type === 'generated'" /></div>
        <p v-if="error" class="inline-error">{{ error }}</p>
        <div class="dialog-actions"><button class="button ghost" :disabled="busy" @click="close">取消</button><button class="button primary" :disabled="!mapId || busy" @click="submit">{{ busy ? '创建中…' : '创建游戏' }}</button></div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.game-settings { max-height: 46vh; overflow: auto; }
</style>
