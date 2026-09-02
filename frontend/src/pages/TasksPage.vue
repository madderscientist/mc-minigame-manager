<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '../api/client'
import EmptyState from '../components/EmptyState.vue'
import QueryError from '../components/QueryError.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatDate, taskStatusLabels, taskStepLabel, taskTypeLabels, toneForStatus } from '../utils/format'

const statusQuery=useQuery({queryKey:['status'],queryFn:api.status,refetchInterval:3500})
const filter=ref<'all'|'active'|'failed'|'done'>('all')
const tasks=computed(()=>(statusQuery.data.value?.tasks??[]).filter((task)=>filter.value==='all'||(filter.value==='active'&&['pending','running'].includes(task.status))||(filter.value==='failed'&&task.status==='failed')||(filter.value==='done'&&['succeeded','canceled'].includes(task.status))))
</script>

<template>
  <div class="page">
    <header class="page-header"><div><div class="eyebrow">Background activity</div><h1>任务</h1><p>查看最近的创建、启停、备份恢复和删除任务。</p></div><div class="live-indicator"><span class="pulse-dot"/>自动刷新</div></header>
    <div class="toolbar"><div class="segmented"><button :class="{active:filter==='all'}" @click="filter='all'">全部</button><button :class="{active:filter==='active'}" @click="filter='active'">进行中</button><button :class="{active:filter==='failed'}" @click="filter='failed'">失败</button><button :class="{active:filter==='done'}" @click="filter='done'">已完成</button></div><span class="result-count">最近 {{ tasks.length }} 条</span></div>
    <QueryError v-if="statusQuery.isError.value" :error="statusQuery.error.value" @retry="statusQuery.refetch()" />
    <section v-else class="panel table-panel">
      <div v-if="tasks.length" class="data-table task-table"><div class="table-row table-head"><span>任务</span><span>资源</span><span>阶段</span><span>进度</span><span>状态</span><span>时间</span></div>
        <article v-for="task in tasks" :key="task.task_id" class="table-row"><div class="entity-name"><div class="task-icon">↻</div><div><strong>{{ taskTypeLabels[task.type] }}</strong><small>{{ task.task_id.slice(0,8) }}</small></div></div><div><RouterLink v-if="task.game_id" :to="`/games/${task.game_id}`">Game #{{ task.game_id }}</RouterLink><RouterLink v-else-if="task.map_id" :to="`/maps/${task.map_id}`">Map #{{ task.map_id }}</RouterLink><span v-else>系统</span></div><span>{{ taskStepLabel(task.step) }}</span><div class="mini-progress"><span :style="{width:`${task.progress*100}%`}"/><b>{{ Math.round(task.progress*100) }}%</b></div><StatusBadge :label="taskStatusLabels[task.status]" :tone="toneForStatus(task.status)"/><span>{{ formatDate(task.created_at) }}</span><p v-if="task.error_message" class="task-row-error">{{ task.error_message }}</p></article>
      </div><EmptyState v-else title="没有符合条件的任务" description="后台任务会按时间倒序显示在这里。" icon="↻"/>
    </section>
  </div>
</template>
