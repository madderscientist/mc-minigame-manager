import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../api/client'
import GameLogsPanel from './GameLogsPanel.vue'

describe('GameLogsPanel', () => {
  let wrapper: VueWrapper | undefined
  let queryClient: QueryClient

  beforeEach(() => {
    vi.useFakeTimers()
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  })
  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    queryClient.clear()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  async function render() {
    wrapper = mount(GameLogsPanel, {
      props: { gameId: 7 },
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    })
    await flushPromises()
    return wrapper
  }

  it('renders log text safely and shows truncation', async () => {
    vi.spyOn(api, 'gameLogs').mockResolvedValue({
      game_id: 7, lines: ['玩家加入游戏', '<script>alert(1)</script>'], available: true, truncated: true,
    })
    const panel = await render()
    expect(api.gameLogs).toHaveBeenCalledWith(7)
    expect(panel.find('pre').text()).toBe('玩家加入游戏\n<script>alert(1)</script>')
    expect(panel.find('script').exists()).toBe(false)
    expect(panel.text()).toContain('较早内容已截断')
  })

  it.each([false, true])('shows the empty state (available=%s)', async (available) => {
    vi.spyOn(api, 'gameLogs').mockResolvedValue({ game_id: 7, lines: [], available, truncated: false })
    const panel = await render()
    expect(panel.text()).toContain(available ? '日志暂无内容' : '尚未生成日志')
    expect(panel.find('pre').exists()).toBe(false)
  })

  it('polls while mounted, pauses, allows manual refresh, and stops after leaving', async () => {
    const fetchLogs = vi.spyOn(api, 'gameLogs').mockResolvedValue({
      game_id: 7, lines: ['Ready'], available: true, truncated: false,
    })
    const panel = await render()
    expect(fetchLogs).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchLogs).toHaveBeenCalledTimes(2)
    await panel.findAll('input')[0]!.setValue(false)
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchLogs).toHaveBeenCalledTimes(2)
    await panel.find('button').trigger('click')
    await flushPromises()
    expect(fetchLogs).toHaveBeenCalledTimes(3)
    await panel.findAll('input')[0]!.setValue(true)
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchLogs).toHaveBeenCalledTimes(4)
    panel.unmount()
    wrapper = undefined
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchLogs).toHaveBeenCalledTimes(4)
  })

  it('shows refresh errors without hiding previously loaded logs', async () => {
    vi.spyOn(api, 'gameLogs')
      .mockResolvedValueOnce({ game_id: 7, lines: ['Last output'], available: true, truncated: false })
      .mockRejectedValueOnce(new ApiError(500, 'game_logs_unreadable', '无法读取游戏日志'))
      .mockResolvedValueOnce({ game_id: 7, lines: ['Recovered'], available: true, truncated: false })
    const panel = await render()
    await panel.find('button').trigger('click')
    await flushPromises()
    expect(panel.text()).toContain('日志加载失败')
    expect(panel.text()).toContain('上次成功读取的内容')
    expect(panel.find('pre').text()).toBe('Last output')
    await panel.findAll('button').find((button) => button.text() === '重新加载')!.trigger('click')
    await flushPromises()
    expect(panel.find('pre').text()).toBe('Recovered')
    expect(panel.text()).not.toContain('日志加载失败')
  })

  it('follows new output only when enabled', async () => {
    const fetchLogs = vi.spyOn(api, 'gameLogs').mockResolvedValue({
      game_id: 7, lines: ['First'], available: true, truncated: false,
    })
    const panel = await render()
    const output = panel.find('pre').element
    Object.defineProperty(output, 'scrollHeight', { value: 800 })
    fetchLogs.mockResolvedValue({ game_id: 7, lines: ['Second'], available: true, truncated: false })
    await panel.find('button').trigger('click')
    await flushPromises()
    expect(output.scrollTop).toBe(800)
    await panel.findAll('input')[1]!.setValue(false)
    output.scrollTop = 100
    fetchLogs.mockResolvedValue({ game_id: 7, lines: ['Third'], available: true, truncated: false })
    await panel.find('button').trigger('click')
    await flushPromises()
    expect(output.scrollTop).toBe(100)
  })
})