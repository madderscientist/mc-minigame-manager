<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  gameName?: string
  unknownState?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ close: []; confirm: [backup: boolean] }>()
const backup = ref(true)

watch(() => props.open, (open) => { if (open) backup.value = true })
function close() { if (!props.busy) emit('close') }
function confirm() { if (!props.busy) emit('confirm', backup.value) }
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop" @click.self="close">
      <section class="dialog-card" role="dialog" aria-modal="true" aria-label="停止游戏">
        <div class="dialog-icon danger">!</div>
        <h2>停止{{ gameName ? ` ${gameName}` : '游戏' }}</h2>
        <p v-if="unknownState">运行状态未知。系统将尝试终止对应容器，并根据你的选择处理备份。</p>
        <p v-else>Paper 将优雅停止并释放端口。</p>
        <label class="check-field">
          <input v-model="backup" type="checkbox" />
          <span><strong>停止后创建备份</strong><small>取消勾选将直接停止，不创建新的恢复点。</small></span>
        </label>
        <div class="dialog-actions">
          <button class="button ghost" :disabled="busy" @click="close">取消</button>
          <button class="button danger" :disabled="busy" @click="confirm">
            {{ busy ? '提交中…' : (backup ? '停止并备份' : '仅停止') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>