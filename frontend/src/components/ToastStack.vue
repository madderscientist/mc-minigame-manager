<script setup lang="ts">
import { useToastStore } from '../stores/toasts'

const toasts = useToastStore()
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <div v-for="toast in toasts.items" :key="toast.id" class="toast" :class="toast.tone">
      <strong>{{ toast.title }}</strong>
      <p v-if="toast.message">{{ toast.message }}</p>
      <button aria-label="关闭" @click="toasts.remove(toast.id)">×</button>
    </div>
  </div>
</template>
