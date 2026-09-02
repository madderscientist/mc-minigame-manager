<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '../api/client'
import CopyAddress from '../components/CopyAddress.vue'
import EmptyState from '../components/EmptyState.vue'
import QueryError from '../components/QueryError.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TaskProgress from '../components/TaskProgress.vue'
import { formatDate, runtimeLabels, toneForStatus } from '../utils/format'

const statusQuery = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 4000 })
const status = computed(() => statusQuery.data.value)
const freePorts = computed(() => status.value?.ports.filter((port) => port.state === 'free').length ?? 0)
const activeTasks = computed(() => status.value?.tasks.filter((task) => ['pending', 'running'].includes(task.status)) ?? [])
const failedTasks = computed(() => status.value?.tasks.filter((task) => task.status === 'failed').slice(0, 3) ?? [])
const runningGames = computed(() => status.value?.running_games ?? [])
</script>

<template>
  <div class="page dashboard-page">
    <header class="page-header hero-header">
      <div>
        <div class="eyebrow">Operations overview</div>
        <h1>控制中心</h1>
        <p>查看小游戏运行、端口和后台任务的实时状态。</p>
      </div>
      <div class="header-actions">
        <RouterLink class="button ghost" to="/maps">管理地图</RouterLink>
        <RouterLink class="button primary" to="/games">打开游戏列表</RouterLink>
      </div>
    </header>

    <QueryError v-if="statusQuery.isError.value" :error="statusQuery.error.value" @retry="statusQuery.refetch()" />
    <template v-else>
    <section class="metric-grid">
      <article class="metric-card accent-card">
        <span class="metric-kicker">RUNNING</span><strong>{{ runningGames.length }}</strong><p>正在运行的游戏</p>
        <div class="metric-spark"><i /><i /><i /><i /><i /></div>
      </article>
      <article class="metric-card">
        <span class="metric-kicker">AVAILABLE PORTS</span><strong>{{ freePorts }}</strong><p>可立即分配的端口</p>
        <small>端口池共 {{ status?.ports.length ?? 0 }} 个</small>
      </article>
      <article class="metric-card">
        <span class="metric-kicker">IN PROGRESS</span><strong>{{ activeTasks.length }}</strong><p>排队或执行中的任务</p>
        <small>{{ activeTasks.length ? '后台正在处理变更' : '队列当前为空' }}</small>
      </article>
      <article class="metric-card" :class="{ 'danger-card': failedTasks.length }">
        <span class="metric-kicker">ATTENTION</span><strong>{{ failedTasks.length }}</strong><p>最近失败任务</p>
        <small>{{ failedTasks.length ? '需要管理员检查' : '最近没有失败任务' }}</small>
      </article>
    </section>

    <div class="dashboard-columns">
      <section class="panel">
        <div class="panel-heading"><div><span class="section-index">01</span><h2>运行中的游戏</h2></div><RouterLink to="/games">查看全部 →</RouterLink></div>
        <div v-if="runningGames.length" class="running-list">
          <RouterLink v-for="item in runningGames" :key="item.game_id" :to="`/games/${item.game_id}`" class="running-row">
            <div class="game-avatar">{{ item.game_name.slice(0, 2).toUpperCase() }}</div>
            <div class="running-name"><strong>{{ item.game_name }}</strong><span>MC {{ item.mc_version }} · #{{ item.game_id }} · {{ formatDate(item.last_played_at) }}</span></div>
            <StatusBadge :label="runtimeLabels[item.observed_state]" :tone="toneForStatus(item.observed_state)" />
            <CopyAddress v-if="item.public_address" :address="item.public_address" /><code v-else>:{{ item.port }}</code><span class="row-arrow">›</span>
          </RouterLink>
        </div>
        <EmptyState v-else title="没有正在运行的游戏" description="从已创建的游戏中选择一个启动。" icon="◈">
          <RouterLink class="button primary small" to="/games">浏览游戏</RouterLink>
        </EmptyState>
      </section>

      <section class="panel task-panel">
        <div class="panel-heading"><div><span class="section-index">02</span><h2>当前任务</h2></div><RouterLink to="/tasks">任务记录 →</RouterLink></div>
        <div v-if="activeTasks.length" class="task-stack">
          <TaskProgress v-for="task in activeTasks.slice(0, 4)" :key="task.task_id" :task="task" compact />
        </div>
        <EmptyState v-else title="后台队列空闲" description="创建、启停和恢复操作会显示在这里。" icon="✓" />
      </section>
    </div>

    <section v-if="failedTasks.length" class="panel failure-panel">
      <div class="panel-heading"><div><span class="section-index">03</span><h2>需要处理</h2></div></div>
      <TaskProgress v-for="task in failedTasks" :key="task.task_id" :task="task" compact />
    </section>
    </template>
  </div>
</template>
