<script setup lang="ts">
import { computed } from 'vue'

import type { ServerSettings } from '../api/types'

const props = defineProps<{ settings: ServerSettings }>()
const labels: Record<string, string> = {
  spawn_protection: '出生点保护',
  gamemode: '游戏模式',
  difficulty: '难度',
  hardcore: '硬核',
  pvp: 'PVP',
  allow_flight: '允许飞行',
  max_players: '最大玩家数',
  white_list: '白名单',
  view_distance: '视距',
  simulation_distance: '模拟距离',
  level_seed: '世界种子',
  level_type: '世界类型',
  generate_structures: '生成结构',
}

const entries = computed(() => {
  const structured = Object.entries(props.settings)
    .filter(([key, value]) => key !== 'custom' && value != null && value !== '')
    .map(([key, value]) => ({ key, label: labels[key] ?? key, value: displayValue(value) }))
  const custom = Object.entries(props.settings.custom ?? {})
    .map(([key, value]) => ({ key: `custom:${key}`, label: key, value }))
  return [...structured, ...custom]
})

function displayValue(value: unknown): string {
  if (value === true) return '开启'
  if (value === false) return '关闭'
  return String(value)
}
</script>

<template>
  <dl v-if="entries.length" class="settings-summary">
    <div v-for="entry in entries" :key="entry.key"><dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd></div>
  </dl>
  <p v-else class="empty-settings">使用 Paper 默认设置</p>
</template>

<style scoped>
.settings-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0; background: var(--line); border: 1px solid var(--line); }
.settings-summary div { min-width: 0; padding: 10px 12px; background: var(--surface); }
.settings-summary dt { color: var(--muted); font-size: .72rem; }
.settings-summary dd { margin: 4px 0 0; overflow-wrap: anywhere; font-weight: 700; }
.empty-settings { margin: 0; color: var(--muted); }
@media (max-width: 520px) { .settings-summary { grid-template-columns: 1fr; } }
</style>
