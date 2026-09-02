<script setup lang="ts">
import { computed } from 'vue'

import { ApiError } from '../api/client'
import EmptyState from './EmptyState.vue'

const props = withDefaults(defineProps<{
  error: unknown
  title?: string
}>(), {
  title: '数据加载失败',
})
const emit = defineEmits<{ retry: [] }>()
const message = computed(() => props.error instanceof ApiError
  ? props.error.message
  : '暂时无法连接后端，请检查服务状态后重试。')
</script>

<template>
  <section class="panel">
    <EmptyState :title="title" :description="message" icon="!">
      <button class="button primary small" @click="emit('retry')">重新加载</button>
    </EmptyState>
  </section>
</template>
