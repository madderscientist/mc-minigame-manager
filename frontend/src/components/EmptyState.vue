<script setup lang="ts">
defineProps<{ title: string; description: string; icon?: string }>()
</script>

<template>
  <div class="empty-state">
    <div class="empty-icon">{{ icon ?? '◇' }}</div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <slot />
  </div>
</template>
