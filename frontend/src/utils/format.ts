import type { RuntimeState, TaskStatus, TaskType } from '../api/types'

export const runtimeLabels: Record<RuntimeState, string> = {
  preparing: '准备中',
  starting: '启动中',
  ready: '运行中',
  stopping: '停止中',
  backing_up: '备份中',
  stopped: '已停止',
  failed: '运行失败',
  unknown: '状态未知',
}

export const taskStatusLabels: Record<TaskStatus, string> = {
  pending: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  canceled: '已取消',
}

export const taskTypeLabels: Record<TaskType, string> = {
  create_game: '创建游戏',
  delete_game: '删除游戏',
  start: '启动游戏',
  stop: '停止游戏',
  load_backup: '恢复备份',
}

export const stepLabels: Record<string, string> = {
  queued: '等待执行',
  copying_map: '正在复制地图',
  deleting_game: '正在删除游戏',
  preparing_runtime: '正在准备运行环境',
  waiting_for_paper: '正在等待 Paper 启动',
  stopping_paper: '正在停止服务器',
  backing_up: '正在创建停止备份',
  stopping_for_restore: '正在为恢复停止服务器',
  protecting_current_state: '正在保护当前状态',
  restoring_backup: '正在恢复备份',
  completed: '已完成',
  failed: '执行失败',
}

export function taskStepLabel(step: string): string {
  if (step.startsWith('retry:')) return `正在恢复任务 · ${taskStepLabel(step.slice(6))}`
  return stepLabels[step] ?? step
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = -1
  do {
    size /= 1024
    unit += 1
  } while (size >= 1024 && unit < units.length - 1)
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unit]}`
}

export function toneForStatus(status: string | null): string {
  if (status === 'ready' || status === 'succeeded' || status === 'free') return 'success'
  if (status === 'failed' || status === 'unknown') return 'danger'
  if (status === 'pending' || status === 'preparing' || status === 'reserved') return 'muted'
  if (status === 'stopped' || status === 'canceled') return 'neutral'
  return 'warning'
}
