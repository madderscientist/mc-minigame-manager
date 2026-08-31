import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { Task, TaskAccepted } from '../api/types'

const ACTIVE_TASKS_KEY = 'mc-manager-active-tasks'

function initialTaskIds(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(ACTIVE_TASKS_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
}

export const useTaskStore = defineStore('tasks', () => {
  const activeTaskIds = ref<string[]>(initialTaskIds())
  const latest = ref<Record<string, Task>>({})
  const activeCount = computed(() => activeTaskIds.value.length)

  function persist() {
    sessionStorage.setItem(ACTIVE_TASKS_KEY, JSON.stringify(activeTaskIds.value))
  }

  function track(accepted: TaskAccepted) {
    if (!activeTaskIds.value.includes(accepted.task_id)) {
      activeTaskIds.value.push(accepted.task_id)
      persist()
    }
  }

  function forget(taskId: string) {
    if (!activeTaskIds.value.includes(taskId)) return
    activeTaskIds.value = activeTaskIds.value.filter((id) => id !== taskId)
    persist()
  }

  function update(task: Task) {
    latest.value[task.task_id] = task
    if (['succeeded', 'failed', 'canceled'].includes(task.status)) {
      forget(task.task_id)
    }
  }

  return { activeTaskIds, latest, activeCount, track, update, forget }
})
