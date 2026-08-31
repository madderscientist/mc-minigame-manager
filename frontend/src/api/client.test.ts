import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, tokenSession } from './client'

function response(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('API client', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.restoreAllMocks()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('adds the session bearer token', async () => {
    tokenSession.set('session-secret')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response([]))
    await api.maps()
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer session-secret')
  })

  it('clears an invalid token and dispatches an event', async () => {
    tokenSession.set('invalid')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ error: { code: 'unauthorized', message: '无效', details: {} } }, 401),
    )
    const listener = vi.fn()
    window.addEventListener('mc-manager:unauthorized', listener)
    await expect(api.maps()).rejects.toMatchObject({ status: 401, code: 'unauthorized' })
    expect(tokenSession.get()).toBe('')
    expect(listener).toHaveBeenCalledOnce()
  })

  it('retries database busy once', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ error: { code: 'database_busy', message: '忙', details: {} } }, 503, { 'Retry-After': '0' }))
      .mockResolvedValueOnce(response([]))
    await api.games()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('returns structured API errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ error: { code: 'game_busy', message: '游戏繁忙', details: {} } }, 409),
    )
    await expect(api.start(3)).rejects.toMatchObject({
      status: 409,
      code: 'game_busy',
      message: '游戏繁忙',
    })
  })

  it('reuses the idempotency key after an unknown network result', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce(response({ task_id: 'task-1', game_id: 3, port: 30000, status: 'pending' }, 202))

    await expect(api.start(3)).rejects.toMatchObject({ status: 0, code: 'network_error' })
    await api.start(3)

    const firstHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
    expect(secondHeaders.get('Idempotency-Key')).toBe(firstHeaders.get('Idempotency-Key'))
  })

  it('reuses the idempotency key after a server error', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ error: { code: 'http_502', message: '网关错误', details: {} } }, 502))
      .mockResolvedValueOnce(response({ task_id: 'task-1', game_id: 3, port: 30000, status: 'pending' }, 202))

    await expect(api.start(3)).rejects.toMatchObject({ status: 502 })
    await api.start(3)

    const firstHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
    expect(secondHeaders.get('Idempotency-Key')).toBe(firstHeaders.get('Idempotency-Key'))
  })

  it('does not clear a newer concurrent idempotency key', async () => {
    let resolveFirst!: (value: Response) => void
    let rejectSecond!: (reason: Error) => void
    const firstResponse = new Promise<Response>((resolve) => { resolveFirst = resolve })
    const secondResponse = new Promise<Response>((_resolve, reject) => { rejectSecond = reject })
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockReturnValueOnce(firstResponse)
      .mockReturnValueOnce(secondResponse)
      .mockResolvedValueOnce(response({ task_id: 'task-2', game_id: 2, status: 'pending' }, 202))

    const first = api.createGame(1)
    const second = api.createGame(2)
    const firstHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
    resolveFirst(response({ task_id: 'task-1', game_id: 1, status: 'pending' }, 202))
    await first
    rejectSecond(new TypeError('network failed'))
    await expect(second).rejects.toMatchObject({ code: 'network_error' })
    await api.createGame(2)

    const thirdHeaders = new Headers(fetchMock.mock.calls[2]?.[1]?.headers)
    expect(firstHeaders.get('Idempotency-Key')).not.toBe(secondHeaders.get('Idempotency-Key'))
    expect(thirdHeaders.get('Idempotency-Key')).toBe(secondHeaders.get('Idempotency-Key'))
  })

  it('reuses the upload idempotency key after an unknown network result', async () => {
    class FakeXmlHttpRequest {
      static instances: FakeXmlHttpRequest[] = []
      upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null }
      headers: Record<string, string> = {}
      status = 0
      responseText = ''
      body: Document | XMLHttpRequestBodyInit | null = null
      onload: (() => void) | null = null
      onerror: (() => void) | null = null

      constructor() { FakeXmlHttpRequest.instances.push(this) }
      open() {}
      setRequestHeader(name: string, value: string) { this.headers[name] = value }
      send(body: Document | XMLHttpRequestBodyInit | null = null) {
        this.body = body
        if (FakeXmlHttpRequest.instances.length === 1) {
          this.onerror?.()
          return
        }
        if (FakeXmlHttpRequest.instances.length === 2) {
          this.status = 502
          this.responseText = JSON.stringify({ error: { code: 'http_502', message: '网关错误', details: {} } })
          this.onload?.()
          return
        }
        this.status = 201
        this.responseText = JSON.stringify({ map_id: 9, name: 'Map', mc_version: '1.20.4', resources: [] })
        this.onload?.()
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXmlHttpRequest)
    const input = {
      mapFile: new File(['map'], 'map.zip', { lastModified: 1 }),
      resourcePack: new File(['pack'], 'visuals.zip', { lastModified: 2 }),
      resourcePackRequired: true,
      resourcePackPrompt: '请下载材质包',
      name: 'Map', mcVersion: '1.20.4', paperBuild: '497', javaMajor: 17, resources: [],
    }

    await expect(api.uploadMap(input, vi.fn())).rejects.toMatchObject({ code: 'network_error' })
    await expect(api.uploadMap(input, vi.fn())).rejects.toMatchObject({ status: 502 })
    await expect(api.uploadMap(input, vi.fn())).resolves.toMatchObject({ map_id: 9 })
    expect(FakeXmlHttpRequest.instances[1]?.headers['Idempotency-Key']).toBe(
      FakeXmlHttpRequest.instances[0]?.headers['Idempotency-Key'],
    )
    expect(FakeXmlHttpRequest.instances[2]?.headers['Idempotency-Key']).toBe(
      FakeXmlHttpRequest.instances[0]?.headers['Idempotency-Key'],
    )
    const form = FakeXmlHttpRequest.instances[2]?.body as FormData
    expect((form.get('resource_pack') as File).name).toBe('visuals.zip')
    expect(form.get('resource_pack_required')).toBe('true')
    expect(form.get('resource_pack_prompt')).toBe('请下载材质包')
  })
})
