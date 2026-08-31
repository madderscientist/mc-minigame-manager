import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { Task } from '../api/types'
import { useTaskStore } from './tasks'

const task: Task = {
  task_id: 'task-1', type: 'start', status: 'running', step: 'waiting_for_paper',
  map_id: 1, game_id: 2, backup_id: null, requested_port: null, progress: 0.65,
  result: null, error_code: null, error_message: null,
  created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z', finished_at: null,
}

describe('task tracking', () => {
  beforeEach(() => { sessionStorage.clear(); setActivePinia(createPinia()) })

  it('persists active tasks and removes terminal tasks', () => {
    const store = useTaskStore()
    store.track({ task_id: 'task-1', game_id: 2, status: 'pending' })
    expect(store.activeCount).toBe(1)
    store.update(task)
    expect(store.activeCount).toBe(1)
    store.update({ ...task, status: 'succeeded', progress: 1 })
    expect(store.activeCount).toBe(0)
    expect(sessionStorage.getItem('mc-manager-active-tasks')).toBe('[]')
  })

  it('forgets a missing tracked task', () => {
    const store = useTaskStore()
    store.track({ task_id: 'missing-task', game_id: 2, status: 'pending' })
    store.forget('missing-task')
    expect(store.activeTaskIds).toEqual([])
    expect(sessionStorage.getItem('mc-manager-active-tasks')).toBe('[]')
  })
})
