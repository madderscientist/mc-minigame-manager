<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const token = ref('')

async function submit() {
  if (!token.value.trim()) return
  try {
    await auth.unlock(token.value)
    await router.replace('/')
  } catch {
    // The store renders a stable error message.
  }
}
</script>

<template>
  <main class="unlock-page">
    <section class="unlock-panel">
      <div class="brand-mark large"><span>MC</span></div>
      <div class="eyebrow">Minecraft Operations</div>
      <h1>小游戏管理台</h1>
      <p>输入管理 Token 以连接本机控制平面。凭据仅保留在当前标签页。</p>
      <form @submit.prevent="submit">
        <label class="field">
          <span>管理 Token</span>
          <input v-model="token" type="password" autocomplete="current-password" autofocus placeholder="Bearer token" />
        </label>
        <p v-if="auth.error" class="inline-error">{{ auth.error }}</p>
        <button class="button primary wide" :disabled="!token.trim() || auth.checking">
          {{ auth.checking ? '正在验证…' : '解锁管理台' }}
        </button>
      </form>
      <div class="unlock-note"><span class="pulse-dot" /> FastAPI · Podman · FRP</div>
    </section>
    <aside class="unlock-art" aria-hidden="true">
      <div class="voxel voxel-one" />
      <div class="voxel voxel-two" />
      <div class="grid-glow" />
      <div class="art-caption"><strong>Control the world.</strong><span>Not the other way around.</span></div>
    </aside>
  </main>
</template>
