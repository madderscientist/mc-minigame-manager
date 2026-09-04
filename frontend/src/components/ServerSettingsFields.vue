<script setup lang="ts">
import type { ServerSettings } from '../api/types'

const props = defineProps<{ worldGeneration?: boolean }>()
const model = defineModel<ServerSettings>({ required: true })

type SettingKey = Exclude<keyof ServerSettings, 'custom'>

function update(key: SettingKey, value: unknown) {
  model.value = { ...model.value, [key]: value === '' ? null : value }
}

function updateNumber(key: SettingKey, event: Event) {
  const value = (event.target as HTMLInputElement).value
  update(key, value === '' ? null : Number(value))
}

function updateBoolean(key: SettingKey, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  update(key, value === '' ? null : value === 'true')
}

function addCustom() {
  let index = 1
  let key = 'custom-property'
  while (key in model.value.custom) key = `custom-property-${++index}`
  model.value = { ...model.value, custom: { ...model.value.custom, [key]: '' } }
}

function renameCustom(oldKey: string, event: Event) {
  const input = event.target as HTMLInputElement
  const newKey = input.value.trim()
  if (oldKey === newKey) return
  const custom = { ...model.value.custom }
  if (!newKey || newKey in custom) {
    input.value = oldKey
    return
  }
  const value = custom[oldKey] ?? ''
  delete custom[oldKey]
  custom[newKey] = value
  model.value = { ...model.value, custom }
}

function updateCustom(key: string, value: string) {
  model.value = { ...model.value, custom: { ...model.value.custom, [key]: value } }
}

function removeCustom(key: string) {
  const custom = { ...model.value.custom }
  delete custom[key]
  model.value = { ...model.value, custom }
}
</script>

<template>
  <div class="settings-fields">
    <div class="settings-grid">
      <label class="field"><span>出生点保护半径</span><input :value="model.spawn_protection ?? ''" type="number" min="0" max="10000" placeholder="服务端默认" @input="updateNumber('spawn_protection', $event)" /></label>
      <label class="field"><span>游戏模式</span><select :value="model.gamemode ?? ''" @change="update('gamemode', ($event.target as HTMLSelectElement).value)"><option value="">服务端默认</option><option value="survival">生存</option><option value="creative">创造</option><option value="adventure">冒险</option><option value="spectator">旁观</option></select></label>
      <label class="field"><span>难度</span><select :value="model.difficulty ?? ''" @change="update('difficulty', ($event.target as HTMLSelectElement).value)"><option value="">服务端默认</option><option value="peaceful">和平</option><option value="easy">简单</option><option value="normal">普通</option><option value="hard">困难</option></select></label>
      <label class="field"><span>最大玩家数</span><input :value="model.max_players ?? ''" type="number" min="1" max="10000" placeholder="服务端默认" @input="updateNumber('max_players', $event)" /></label>
      <label class="field"><span>PVP</span><select :value="model.pvp == null ? '' : String(model.pvp)" @change="updateBoolean('pvp', $event)"><option value="">服务端默认</option><option value="true">开启</option><option value="false">关闭</option></select></label>
      <label class="field"><span>允许飞行</span><select :value="model.allow_flight == null ? '' : String(model.allow_flight)" @change="updateBoolean('allow_flight', $event)"><option value="">服务端默认</option><option value="true">开启</option><option value="false">关闭</option></select></label>
      <label class="field"><span>硬核模式</span><select :value="model.hardcore == null ? '' : String(model.hardcore)" @change="updateBoolean('hardcore', $event)"><option value="">服务端默认</option><option value="true">开启</option><option value="false">关闭</option></select></label>
      <label class="field"><span>白名单</span><select :value="model.white_list == null ? '' : String(model.white_list)" @change="updateBoolean('white_list', $event)"><option value="">服务端默认</option><option value="true">开启</option><option value="false">关闭</option></select></label>
      <label class="field"><span>视距</span><input :value="model.view_distance ?? ''" type="number" min="3" max="32" placeholder="服务端默认" @input="updateNumber('view_distance', $event)" /></label>
      <label class="field"><span>模拟距离</span><input :value="model.simulation_distance ?? ''" type="number" min="3" max="32" placeholder="服务端默认" @input="updateNumber('simulation_distance', $event)" /></label>
    </div>

    <div v-if="props.worldGeneration" class="world-settings">
      <label class="field"><span>世界种子 <small>留空时每个游戏随机生成</small></span><input :value="model.level_seed ?? ''" maxlength="128" placeholder="随机" @input="update('level_seed', ($event.target as HTMLInputElement).value)" /></label>
      <label class="field"><span>世界类型</span><input :value="model.level_type ?? ''" maxlength="128" placeholder="minecraft:normal" @input="update('level_type', ($event.target as HTMLInputElement).value)" /></label>
      <label class="field"><span>生成结构</span><select :value="model.generate_structures == null ? '' : String(model.generate_structures)" @change="updateBoolean('generate_structures', $event)"><option value="">服务端默认</option><option value="true">生成</option><option value="false">不生成</option></select></label>
    </div>

    <div class="custom-settings">
      <div class="custom-heading"><div><strong>高级自定义属性</strong><small>仅限 server.properties；系统托管键不可覆盖。</small></div><button type="button" class="button ghost small" @click="addCustom">添加属性</button></div>
      <div v-for="(value, key) in model.custom" :key="key" class="custom-row">
        <input :value="key" aria-label="属性名" placeholder="属性名" @change="renameCustom(key, $event)" />
        <input :value="value" aria-label="属性值" placeholder="属性值" @input="updateCustom(key, ($event.target as HTMLInputElement).value)" />
        <button type="button" class="icon-button" title="移除属性" aria-label="移除属性" @click="removeCustom(key)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-fields { display: grid; gap: 16px; }
.settings-grid, .world-settings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.world-settings { padding-top: 14px; border-top: 1px solid var(--line); }
.custom-settings { display: grid; gap: 10px; padding-top: 14px; border-top: 1px solid var(--line); }
.custom-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.custom-heading div { display: grid; gap: 2px; }
.custom-heading small { color: var(--muted); }
.custom-row { display: grid; grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr) 36px; gap: 8px; }
.icon-button { width: 36px; height: 36px; border: 1px solid var(--line); background: transparent; color: var(--muted); cursor: pointer; }
@media (max-width: 640px) { .settings-grid, .world-settings { grid-template-columns: 1fr; } .custom-row { grid-template-columns: 1fr 1fr 36px; } }
</style>
