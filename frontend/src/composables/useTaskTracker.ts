import { useQueryClient } from '@tanstack/vue-query'
import { onBeforeUnmount, onMounted } from 'vue'

import { api, ApiError } from '../api/client'
import type { Task } from '../api/types'
import { useTaskStore } from '../stores/tasks'
import { useToastStore } from '../stores/toasts'
import { taskTypeLabels } from '../utils/format'

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled'])

export function useTaskTracker() {
  const tasks = useTaskStore()
  const toasts = useToastStore()
  const queryClient = useQueryClient()
  let timer: number | undefined
  let polling = false

  async function poll() {
    if (polling || !tasks.activeTaskIds.length) return
    polling = true
    try {
      const activeIds = [...tasks.activeTaskIds]
      const activeSet = new Set(activeIds)
      const status = await api.status()
      queryClient.setQueryData(['status'], status)
      const snapshots = new Map(status.tasks.map((task) => [task.task_id, task]))
      const missingIds = activeIds.filter((taskId) => !snapshots.has(taskId))
      const missingResults = await Promise.allSettled(missingIds.map(api.task))
      for (const [index, result] of missingResults.entries()) {
        if (result.status === 'fulfilled') {
          snapshots.set(result.value.task_id, result.value)
        } else if (result.reason instanceof ApiError && result.reason.status === 404) {
          tasks.forget(missingIds[index]!)
        }
      }

      const completed: Task[] = []
      for (const task of snapshots.values()) {
        if (!activeSet.has(task.task_id)) continue
        tasks.update(task)
        if (TERMINAL_STATUSES.has(task.status)) {
          completed.push(task)
          if (task.status === 'succeeded') {
            toasts.push(`${taskTypeLabels[task.type]}完成`, '', 'success')
          } else if (task.status === 'failed') {
            toasts.push(`${taskTypeLabels[task.type]}失败`, task.error_message ?? '', 'danger')
          }
        }
      }

      if (completed.length) {
        const gameIds = new Set(
          completed.flatMap((task) => task.game_id === null ? [] : [task.game_id]),
        )
        const refreshes = [queryClient.invalidateQueries({ queryKey: ['games'] })]
        if (completed.some((task) => ['create_game', 'delete_game'].includes(task.type))) {
          refreshes.push(queryClient.invalidateQueries({ queryKey: ['maps'] }))
        }
        for (const gameId of gameIds) {
          refreshes.push(queryClient.invalidateQueries({ queryKey: ['game', gameId] }))
          if (completed.some((task) => task.game_id === gameId && ['stop', 'load_backup'].includes(task.type))) {
            refreshes.push(queryClient.invalidateQueries({ queryKey: ['backups', gameId] }))
          }
        }
        await Promise.all(refreshes)
      }
    } catch {
      // A temporary status failure must not drop tracked tasks.
    } finally {
      polling = false
    }
  }

  onMounted(() => {
    void poll()
    timer = window.setInterval(poll, 1200)
  })
  onBeforeUnmount(() => window.clearInterval(timer))
  return { poll }
}
