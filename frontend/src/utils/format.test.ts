import { describe, expect, it } from 'vitest'

import { formatBytes, taskStepLabel, toneForStatus } from './format'

describe('display formatting', () => {
  it('renders task retries as an understandable stage', () => {
    expect(taskStepLabel('retry:waiting_for_paper')).toBe('正在恢复任务 · 正在等待 Paper 启动')
  })

  it('formats storage sizes', () => {
    expect(formatBytes(1024)).toBe('1.00 KB')
    expect(formatBytes(10 * 1024 * 1024)).toBe('10.0 MB')
  })

  it('uses danger tone for unknown runtime state', () => {
    expect(toneForStatus('unknown')).toBe('danger')
  })
})
