import type {
  ApiErrorBody,
  ChunkedUploadCreated,
  Game,
  MapRecord,
  MapUploadInput,
  MapUploadResult,
  Status,
  Task,
  TaskAccepted,
} from './types'

const TOKEN_KEY = 'mc-manager-token'

interface SavedIdempotency {
  fingerprint: string
  key: string
  uploadId?: string
  chunkSize?: number
  chunks?: Record<string, string>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly retryAfter: number | null

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    retryAfter: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.retryAfter = retryAfter
  }
}

export const tokenSession = {
  get: () => sessionStorage.getItem(TOKEN_KEY) ?? '',
  set: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
}

function createIdempotencyKey(): string {
  return crypto.randomUUID()
}

function shouldRetainIdempotencyKey(error: ApiError): boolean {
  return (
    (error.status === 0 && error.code !== 'upload_canceled')
    || error.status === 408
    || error.status === 425
    || error.status === 429
    || error.status >= 500
    || error.code === 'import_in_progress'
    || error.code === 'upload_capacity_reached'
    || error.code === 'upload_completing'
  )
}

function readSavedIdempotency(storageKey: string): SavedIdempotency | null {
  try {
    return JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') as SavedIdempotency | null
  } catch {
    return null
  }
}

function clearSavedIdempotency(storageKey: string, key: string) {
  if (readSavedIdempotency(storageKey)?.key === key) sessionStorage.removeItem(storageKey)
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { retryBusy?: boolean } = { retryBusy: true },
): Promise<T> {
  const token = tokenSession.get()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') {
      throw new ApiError(0, 'upload_canceled', '上传已取消')
    }
    throw new ApiError(0, 'network_error', '网络中断，操作结果未知；重试将复用同一请求标识')
  }
  if (response.status === 401) {
    tokenSession.clear()
    window.dispatchEvent(new CustomEvent('mc-manager:unauthorized'))
  }
  if (!response.ok) {
    let body: ApiErrorBody | null = null
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      // Keep a stable fallback for proxy and unhandled backend errors.
    }
    const fallbackMessage = response.status === 413
      ? 'HTTPS 代理拒绝了上传分片（413），请检查代理请求大小限制'
      : response.status === 502 || response.status === 504
        ? 'HTTPS 代理连接后端超时，请检查代理超时设置后重试'
        : '请求失败，请稍后重试'
    const error = new ApiError(
      response.status,
      body?.error?.code ?? `http_${response.status}`,
      body?.error?.message ?? fallbackMessage,
      body?.error?.details ?? {},
      parseRetryAfter(response.headers.get('Retry-After')),
    )
    if (response.status === 503 && options.retryBusy !== false) {
      await new Promise((resolve) => setTimeout(resolve, (error.retryAfter ?? 1) * 1000))
      return request<T>(path, init, { retryBusy: false })
    }
    throw error
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function parseRetryAfter(value: string | null): number | null {
  if (value === null) return null
  const seconds = Number(value)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds
  const date = Date.parse(value)
  return Number.isNaN(date) ? null : Math.max(0, (date - Date.now()) / 1000)
}

async function idempotentRequest<T>(
  scope: string,
  payload: unknown,
  makeRequest: (key: string) => Promise<T>,
): Promise<T> {
  const storageKey = `mc-manager-idempotency:${scope}`
  const fingerprint = JSON.stringify(payload)
  const saved = readSavedIdempotency(storageKey)
  const key = saved?.fingerprint === fingerprint ? saved.key : createIdempotencyKey()
  sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key }))
  try {
    const result = await makeRequest(key)
    clearSavedIdempotency(storageKey, key)
    return result
  } catch (reason) {
    if (reason instanceof ApiError && !shouldRetainIdempotencyKey(reason)) {
      clearSavedIdempotency(storageKey, key)
    }
    throw reason
  }
}

function command<T>(path: string, body: unknown, scope: string) {
  return idempotentRequest(scope, body, (key) => request<T>(path, {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(body),
  }))
}

export const api = {
  health: () => request<{ status: string }>('/healthz'),
  maps: () => request<MapRecord[]>('/api/maps'),
  map: (mapId: number) => request<MapRecord>(`/api/maps/${mapId}`),
  deleteMap: (mapId: number) => request<void>(`/api/maps/${mapId}`, { method: 'DELETE' }),
  games: () => request<Game[]>('/api/games'),
  game: (gameId: number) => request<Game>(`/api/games/${gameId}`),
  createGame: (mapId: number, name?: string) => {
    const body = { map_id: mapId, name: name || null }
    return command<TaskAccepted>('/api/games', body, 'create-game')
  },
  deleteGame: (gameId: number) =>
    idempotentRequest(`delete-game:${gameId}`, { game_id: gameId }, (key) => request<TaskAccepted>(`/api/games/${gameId}`, {
      method: 'DELETE',
      headers: { 'Idempotency-Key': key },
    })),
  start: (gameId: number, port?: number) => {
    const body = { game_id: gameId, port: port || null }
    return command<TaskAccepted>('/api/start', body, `start:${gameId}`)
  },
  stop: (gameId: number, backup = true) =>
    command<TaskAccepted>('/api/stop', { game_id: gameId, backup }, `stop:${gameId}:${backup}`),
  load: (gameId: number, backupId: string) =>
    command<TaskAccepted>('/api/load', { game_id: gameId, backup_id: backupId }, `load:${gameId}`),
  deleteBackup: (gameId: number, backupId: string) =>
    request<void>(`/api/games/${gameId}/backups/${backupId}`, { method: 'DELETE' }),
  task: (taskId: string) => request<Task>(`/api/tasks/${taskId}`),
  status: () => request<Status>('/api/status'),
  uploadMap: (input: MapUploadInput, onProgress: (value: number) => void) =>
    uploadMap(input, onProgress),
}

async function uploadMap(input: MapUploadInput, onProgress: (value: number) => void): Promise<MapUploadResult> {
  const fingerprint = JSON.stringify({
    map: [input.mapFile.name, input.mapFile.size, input.mapFile.lastModified],
    resourcePack: input.resourcePack
      ? [input.resourcePack.name, input.resourcePack.size, input.resourcePack.lastModified]
      : null,
    resourcePackRequired: input.resourcePackRequired ?? false,
    resourcePackPrompt: input.resourcePackPrompt ?? '',
    name: input.name,
    paperBuild: input.paperBuild ?? '',
    paperUrl: input.paperUrl ?? '',
    paperSha256: input.paperSha256 ?? '',
  })
  const storageKey = 'mc-manager-idempotency:upload-map'
  const candidate = readSavedIdempotency(storageKey)
  const files = [
    { kind: 'map', file: input.mapFile },
    ...(input.resourcePack ? [{ kind: 'resource_pack', file: input.resourcePack }] : []),
  ]
  const saved = candidate?.fingerprint === fingerprint && await savedChunksMatch(candidate, files)
    ? candidate
    : null
  if (candidate && !saved) sessionStorage.removeItem(storageKey)
  const idempotencyKey = saved?.fingerprint === fingerprint ? saved.key : createIdempotencyKey()
  const uploadId = saved?.fingerprint === fingerprint && saved.uploadId
    ? saved.uploadId
    : crypto.randomUUID()
  sessionStorage.setItem(
    storageKey,
    JSON.stringify({ fingerprint, key: idempotencyKey, uploadId, chunks: saved?.chunks ?? {} }),
  )
  const controller = new AbortController()
  const abortFromCaller = () => controller.abort()
  if (input.signal?.aborted) controller.abort()
  else input.signal?.addEventListener('abort', abortFromCaller, { once: true })
  const metadata = {
    map_size: input.mapFile.size,
    resource_pack_size: input.resourcePack?.size ?? 0,
    resource_pack_filename: input.resourcePack?.name ?? '',
    resource_pack_required: input.resourcePackRequired ?? false,
    resource_pack_prompt: input.resourcePackPrompt ?? '',
    name: input.name,
    paper_build: input.paperBuild ?? '',
    paper_url: input.paperUrl ?? '',
    paper_sha256: input.paperSha256 ?? '',
  }
  try {
    const created = await request<ChunkedUploadCreated>(`/api/uploads/${uploadId}`, {
      method: 'POST',
      body: JSON.stringify(metadata),
      signal: controller.signal,
    })
    if (created.completed && !Object.keys(saved?.chunks ?? {}).length) {
      clearSavedIdempotency(storageKey, idempotencyKey)
      return uploadMap(input, onProgress)
    }
    const chunks = created.completed ? [] : files.flatMap(({ kind, file }) =>
      Array.from({ length: Math.ceil(file.size / created.chunk_size) }, (_, index) => ({
        kind,
        index,
        blob: file.slice(index * created.chunk_size, (index + 1) * created.chunk_size),
      })),
    )
    const totalBytes = files.reduce((total, item) => total + item.file.size, 0)
    let uploadedBytes = 0
    let cursor = 0
    async function worker() {
      while (cursor < chunks.length) {
        const chunk = chunks[cursor++]
        if (!chunk) return
        const data = await chunk.blob.arrayBuffer()
        const checksum = await sha256Hex(data)
        await retryChunk(() => request<void>(
          `/api/uploads/${uploadId}/${chunk.kind}/${chunk.index}`,
          {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/octet-stream',
              'X-Chunk-SHA256': checksum,
            },
            body: data,
            signal: controller.signal,
          },
          { retryBusy: false },
        ))
        saveChunkChecksum(
          storageKey,
          idempotencyKey,
          uploadId,
          created.chunk_size,
          `${chunk.kind}:${chunk.index}`,
          checksum,
        )
        uploadedBytes += chunk.blob.size
        onProgress(uploadedBytes / totalBytes)
      }
    }
    const workers = Array.from({ length: Math.min(4, chunks.length) }, worker)
    try {
      await Promise.all(workers)
    } catch (reason) {
      controller.abort()
      await Promise.allSettled(workers)
      throw reason
    }
    if (created.completed) onProgress(1)
    const result = await request<MapUploadResult>(`/api/uploads/${uploadId}/complete`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      signal: controller.signal,
    })
    clearSavedIdempotency(storageKey, idempotencyKey)
    return result
  } catch (reason) {
    if (input.signal?.aborted) {
      try { await request<void>(`/api/uploads/${uploadId}`, { method: 'DELETE' }) } catch {}
      clearSavedIdempotency(storageKey, idempotencyKey)
      throw new ApiError(0, 'upload_canceled', '上传已取消')
    }
    if (reason instanceof ApiError && !shouldRetainIdempotencyKey(reason)) {
      try { await request<void>(`/api/uploads/${uploadId}`, { method: 'DELETE' }) } catch {}
      clearSavedIdempotency(storageKey, idempotencyKey)
    }
    throw reason
  } finally {
    input.signal?.removeEventListener('abort', abortFromCaller)
  }
}

function saveChunkChecksum(
  storageKey: string,
  key: string,
  uploadId: string,
  chunkSize: number,
  chunk: string,
  checksum: string,
) {
  const saved = readSavedIdempotency(storageKey)
  if (saved?.key !== key || saved.uploadId !== uploadId) return
  sessionStorage.setItem(storageKey, JSON.stringify({
    ...saved,
    chunkSize,
    chunks: { ...saved.chunks, [chunk]: checksum },
  }))
}

async function savedChunksMatch(
  saved: SavedIdempotency,
  files: Array<{ kind: string; file: File }>,
): Promise<boolean> {
  const checksums = Object.entries(saved.chunks ?? {})
  if (!checksums.length) return true
  if (!saved.chunkSize) return false
  const byKind = new Map(files.map((item) => [item.kind, item.file]))
  for (const [key, expected] of checksums) {
    const separator = key.lastIndexOf(':')
    const kind = key.slice(0, separator)
    const index = Number(key.slice(separator + 1))
    const file = byKind.get(kind)
    if (!file || !Number.isSafeInteger(index) || index < 0) return false
    const start = index * saved.chunkSize
    if (start >= file.size) return false
    const data = await file.slice(start, start + saved.chunkSize).arrayBuffer()
    if (await sha256Hex(data) !== expected) return false
  }
  return true
}

async function retryChunk(operation: () => Promise<void>) {
  let lastError: unknown
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await operation()
      return
    } catch (error) {
      lastError = error
      if (!(error instanceof ApiError) || !shouldRetainIdempotencyKey(error)) throw error
      if (attempt < 2) {
        const delay = error.retryAfter === null ? 250 * (attempt + 1) : error.retryAfter * 1000
        await new Promise((resolve) => window.setTimeout(resolve, delay))
      }
    }
  }
  throw lastError
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data)
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}
