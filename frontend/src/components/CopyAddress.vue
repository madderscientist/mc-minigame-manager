<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

const props = defineProps<{ address: string }>()
const copied = ref(false)
let resetTimer: number | undefined

async function copyAddress() {
  await navigator.clipboard.writeText(props.address)
  copied.value = true
  window.clearTimeout(resetTimer)
  resetTimer = window.setTimeout(() => { copied.value = false }, 1600)
}

onBeforeUnmount(() => window.clearTimeout(resetTimer))
</script>

<template>
  <span class="copy-address" role="button" tabindex="0" :title="`复制 ${address}`" @click.prevent.stop="copyAddress" @keydown.enter.prevent.stop="copyAddress" @keydown.space.prevent.stop="copyAddress">
    <code>{{ address }}</code><span>{{ copied ? '已复制' : '复制' }}</span>
  </span>
</template>