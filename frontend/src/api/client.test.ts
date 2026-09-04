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

  it('creates a generated map with managed settings and idempotency', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ map_id: 8, name: 'Fresh world', mc_version: '1.21.11' }, 201),
    )

    await api.generateMap({
      name: 'Fresh world',
      mc_version: '1.21.11',
      server_settings: { level_seed: '42', spawn_protection: 0, custom: {} },
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/maps/generated')
    const init = fetchMock.mock.calls[0]?.[1]
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(String(init?.body))).toMatchObject({
      mc_version: '1.21.11',
      server_settings: { level_seed: '42', spawn_protection: 0 },
    })
  })

  it('sends the final server settings snapshot when creating a game', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ task_id: 'task-1', game_id: 2, map_id: 1, status: 'pending' }, 202),
    )

    await api.createGame(1, 'Round', { gamemode: 'adventure', custom: {} })

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      map_id: 1,
      name: 'Round',
      server_settings: { gamemode: 'adventure', custom: {} },
    })
  })

  it('preserves a zero Retry-After value', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response(
        { error: { code: 'rate_limited', message: '稍后重试', details: {} } },
        429,
        { 'Retry-After': '0' },
      ),
    )

    await expect(api.maps()).rejects.toMatchObject({ retryAfter: 0 })
  })

  it('explains an HTML 413 response from the HTTPS proxy', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({
        upload_id: 'd791ecac-ba64-4dbf-9fe3-5bfa4bbc2011',
        chunk_size: 8,
        completed: false,
      }, 201))
      .mockResolvedValueOnce(new Response(
        '<html><title>413 Request Entity Too Large</title></html>',
        { status: 413, headers: { 'Content-Type': 'text/html' } },
      ))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await expect(api.uploadMap({
      mapFile: new File(['map'], 'map.zip'),
      name: 'Map',
    }, vi.fn())).rejects.toMatchObject({
      status: 413,
      message: expect.stringContaining('HTTPS 代理拒绝了上传分片'),
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
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({
        upload_id: 'd791ecac-ba64-4dbf-9fe3-5bfa4bbc2011',
        chunk_size: 8,
        completed: false,
      }, 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce(response({
        upload_id: 'd791ecac-ba64-4dbf-9fe3-5bfa4bbc2011',
        chunk_size: 8,
        completed: true,
      }, 201))
      .mockResolvedValueOnce(response({ map_id: 9, name: 'Map', mc_version: '1.20.4' }))
    const input = {
      mapFile: new File(['map'], 'map.zip', { lastModified: 1 }),
      resourcePack: new File(['pack'], 'visuals.zip', { lastModified: 2 }),
      resourcePackRequired: true,
      resourcePackPrompt: '请下载材质包',
      name: 'Map',
    }

    await expect(api.uploadMap(input, vi.fn())).rejects.toMatchObject({ code: 'network_error' })
    await expect(api.uploadMap(input, vi.fn())).resolves.toMatchObject({ map_id: 9 })
    expect(String(fetchMock.mock.calls[4]?.[0])).toBe(String(fetchMock.mock.calls[0]?.[0]))
    const firstCompleteHeaders = new Headers(fetchMock.mock.calls[3]?.[1]?.headers)
    const secondCompleteHeaders = new Headers(fetchMock.mock.calls[5]?.[1]?.headers)
    expect(secondCompleteHeaders.get('Idempotency-Key')).toBe(
      firstCompleteHeaders.get('Idempotency-Key'),
    )
    const metadata = JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))
    expect(metadata.resource_pack_filename).toBe('visuals.zip')
    expect(metadata.resource_pack_required).toBe(true)
    expect(metadata.resource_pack_prompt).toBe('请下载材质包')
    expect(metadata.server_settings).toBe('{}')
  })

  it('does not reuse an upload session for different file content', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ upload_id: 'first', chunk_size: 8, completed: false }, 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce(response({ upload_id: 'second', chunk_size: 8, completed: false }, 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(response({ map_id: 10, name: 'Map', mc_version: '1.20.4' }))
    const first = {
      mapFile: new File(['a'], 'map.zip', { lastModified: 1 }),
      name: 'Map',
    }
    const changed = {
      mapFile: new File(['b'], 'map.zip', { lastModified: 1 }),
      name: 'Map',
    }

    await expect(api.uploadMap(first, vi.fn())).rejects.toMatchObject({ code: 'network_error' })
    await expect(api.uploadMap(changed, vi.fn())).resolves.toMatchObject({ map_id: 10 })

    expect(String(fetchMock.mock.calls[0]?.[0])).not.toBe(String(fetchMock.mock.calls[3]?.[0]))
    const firstHeaders = new Headers(fetchMock.mock.calls[2]?.[1]?.headers)
    const changedHeaders = new Headers(fetchMock.mock.calls[5]?.[1]?.headers)
    expect(firstHeaders.get('Idempotency-Key')).not.toBe(changedHeaders.get('Idempotency-Key'))
  })

  it('uploads at most four chunks concurrently', async () => {
    let active = 0
    let maximum = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path, init) => {
      if (init?.method === 'POST' && !String(path).endsWith('/complete')) {
        return response({ upload_id: 'id', chunk_size: 1, completed: false }, 201)
      }
      if (init?.method === 'PUT') {
        active += 1
        maximum = Math.max(maximum, active)
        await new Promise((resolve) => window.setTimeout(resolve, 0))
        active -= 1
        return new Response(null, { status: 204 })
      }
      return response({ map_id: 9, name: 'Map', mc_version: '1.20.4' })
    })

    await api.uploadMap({
      mapFile: new File(['abcdefgh'], 'map.zip', { lastModified: 1 }),
      name: 'Map',
    }, vi.fn())

    expect(maximum).toBe(4)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(8)
  })

  it('aborts and settles remaining workers after a permanent chunk failure', async () => {
    let putCount = 0
    let abortedWorkers = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_path, init) => {
      if (init?.method === 'POST') {
        return response({ upload_id: 'id', chunk_size: 1, completed: false }, 201)
      }
      if (init?.method === 'DELETE') return new Response(null, { status: 204 })
      putCount += 1
      if (putCount === 1) {
        return response({
          error: { code: 'chunk_checksum_mismatch', message: '校验失败', details: {} },
        }, 422)
      }
      return new Promise<Response>((_resolve, reject) => {
        if (init?.signal?.aborted) {
          abortedWorkers += 1
          reject(new DOMException('aborted', 'AbortError'))
          return
        }
        init?.signal?.addEventListener('abort', () => {
          abortedWorkers += 1
          reject(new DOMException('aborted', 'AbortError'))
        }, { once: true })
      })
    })

    await expect(api.uploadMap({
      mapFile: new File(['abcdefgh'], 'map.zip', { lastModified: 1 }),
      name: 'Map',
    }, vi.fn())).rejects.toMatchObject({ code: 'chunk_checksum_mismatch' })

    expect(putCount).toBeLessThanOrEqual(4)
    expect(abortedWorkers).toBe(putCount - 1)
  })
})
