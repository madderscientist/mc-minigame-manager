<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import { copyText } from '../utils/clipboard'

const props = defineProps<{ address: string }>()
const state = ref<'idle' | 'copied' | 'failed'>('idle')
let resetTimer: number | undefined

async function copyAddress() {
  try {
    await copyText(props.address)
    state.value = 'copied'
  } catch {
    state.value = 'failed'
  }
  window.clearTimeout(resetTimer)
  resetTimer = window.setTimeout(() => { state.value = 'idle' }, 1600)
}

onBeforeUnmount(() => window.clearTimeout(resetTimer))
</script>

<template>
  <span class="copy-address" role="button" tabindex="0" :title="`复制 ${address}`" @click.prevent.stop="copyAddress" @keydown.enter.prevent.stop="copyAddress" @keydown.space.prevent.stop="copyAddress">
    <code>{{ address }}</code><span>{{ state === 'copied' ? '已复制' : state === 'failed' ? '复制失败' : '复制' }}</span>
  </span>
</template>