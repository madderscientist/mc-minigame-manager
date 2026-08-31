<script setup lang="ts">
import type { Task } from '../api/types'
import { taskStatusLabels, taskStepLabel, taskTypeLabels, toneForStatus } from '../utils/format'
import StatusBadge from './StatusBadge.vue'

defineProps<{ task: Task; compact?: boolean }>()
</script>

<template>
  <article class="task-progress" :class="{ compact }">
    <div class="task-progress-head">
      <div>
        <strong>{{ taskTypeLabels[task.type] }}</strong>
        <p>{{ taskStepLabel(task.step) }}</p>
      </div>
      <StatusBadge :label="taskStatusLabels[task.status]" :tone="toneForStatus(task.status)" />
    </div>
    <div class="progress-track" :class="{ indeterminate: task.status === 'pending' }">
      <span :style="{ width: `${Math.round(task.progress * 100)}%` }" />
    </div>
    <p v-if="task.error_message" class="inline-error">{{ task.error_message }}</p>
  </article>
</template>
