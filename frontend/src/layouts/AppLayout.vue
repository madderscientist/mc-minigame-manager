<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import ToastStack from '../components/ToastStack.vue'
import { useTaskTracker } from '../composables/useTaskTracker'
import { useAuthStore } from '../stores/auth'
import { useTaskStore } from '../stores/tasks'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const tasks = useTaskStore()
const mobileOpen = ref(false)
useTaskTracker()

const nav = [
  { to: '/', label: '概览', icon: '◫' },
  { to: '/games', label: '游戏', icon: '◆' },
  { to: '/maps', label: '地图', icon: '▦' },
  { to: '/tasks', label: '任务', icon: '↻' },
  { to: '/help', label: '教程', icon: '?' },
]

function lock() {
  auth.lock()
  void router.replace('/unlock')
}
</script>

<template>
  <div class="app-shell">
    <button class="mobile-menu" aria-label="打开菜单" @click="mobileOpen = true">☰</button>
    <div v-if="mobileOpen" class="mobile-scrim" @click="mobileOpen = false" />
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="sidebar-brand">
        <div class="brand-mark"><span>MC</span></div>
        <div><strong>Minigame</strong><small>CONTROL PLANE</small></div>
      </div>
      <nav>
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          :class="{ active: route.path === item.to || (item.to !== '/' && route.path.startsWith(item.to)) }"
          @click="mobileOpen = false"
        >
          <span class="nav-icon">{{ item.icon }}</span>{{ item.label }}
          <span v-if="item.to === '/tasks' && tasks.activeCount" class="nav-count">{{ tasks.activeCount }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
        <div class="connection-state"><span class="pulse-dot" />本机控制平面</div>
        <button class="sidebar-lock" @click="lock">锁定会话 <span>↗</span></button>
      </div>
    </aside>
    <main class="workspace">
      <RouterView :key="route.fullPath" />
    </main>
    <ToastStack />
  </div>
</template>
