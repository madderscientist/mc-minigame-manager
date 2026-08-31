<script setup lang="ts">
defineProps<{ label: string; tone?: string }>()
</script>

<template>
  <span class="status-badge" :class="`status-${tone ?? 'neutral'}`">
    <span class="status-dot" />{{ label }}
  </span>
</template>
