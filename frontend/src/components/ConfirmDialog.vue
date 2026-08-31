<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  confirmText?: string
  danger?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ close: []; confirm: [] }>()
const input = ref('')
const canConfirm = computed(() => !props.confirmText || input.value === props.confirmText)
watch(() => props.open, (value) => { if (value) input.value = '' })
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop" @click.self="emit('close')">
      <section class="dialog-card" role="dialog" aria-modal="true" :aria-label="title">
        <div class="dialog-icon" :class="danger ? 'danger' : ''">{{ danger ? '!' : '→' }}</div>
        <h2>{{ title }}</h2>
        <p>{{ description }}</p>
        <label v-if="confirmText" class="field">
          <span>请输入 <strong>{{ confirmText }}</strong> 确认</span>
          <input v-model="input" autocomplete="off" />
        </label>
        <div class="dialog-actions">
          <button class="button ghost" :disabled="busy" @click="emit('close')">取消</button>
          <button
            class="button"
            :class="danger ? 'danger' : 'primary'"
            :disabled="!canConfirm || busy"
            @click="emit('confirm')"
          >
            {{ busy ? '处理中…' : (confirmLabel ?? '确认') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
