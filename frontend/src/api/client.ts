import type {
  ApiErrorBody,
  Backup,
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
    error.status === 0
    || error.status === 408
    || error.status === 425
    || error.status === 429
    || error.status >= 500
    || error.code === 'import_in_progress'
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
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
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
    const error = new ApiError(
      response.status,
      body?.error.code ?? `http_${response.status}`,
      body?.error.message ?? '请求失败，请稍后重试',
      body?.error.details ?? {},
      Number(response.headers.get('Retry-After')) || null,
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
  stop: (gameId: number) =>
    command<TaskAccepted>('/api/stop', { game_id: gameId }, `stop:${gameId}`),
  backups: (gameId: number) => request<Backup[]>(`/api/games/${gameId}/backups`),
  load: (gameId: number, backupId: string) =>
    command<TaskAccepted>('/api/load', { game_id: gameId, backup_id: backupId }, `load:${gameId}`),
  deleteBackup: (gameId: number, backupId: string) =>
    request<void>(`/api/games/${gameId}/backups/${backupId}`, { method: 'DELETE' }),
  task: (taskId: string) => request<Task>(`/api/tasks/${taskId}`),
  status: () => request<Status>('/api/status'),
  uploadMap: (input: MapUploadInput, onProgress: (value: number) => void) =>
    uploadMap(input, onProgress),
}

function uploadMap(input: MapUploadInput, onProgress: (value: number) => void): Promise<MapUploadResult> {
  const fingerprint = JSON.stringify({
    map: [input.mapFile.name, input.mapFile.size, input.mapFile.lastModified],
    resources: input.resources.map((file) => [file.name, file.size, file.lastModified]),
    resourcePack: input.resourcePack
      ? [input.resourcePack.name, input.resourcePack.size, input.resourcePack.lastModified]
      : null,
    resourcePackRequired: input.resourcePackRequired ?? false,
    resourcePackPrompt: input.resourcePackPrompt ?? '',
    name: input.name,
    mcVersion: input.mcVersion,
    paperBuild: input.paperBuild,
    javaMajor: input.javaMajor,
    paperUrl: input.paperUrl ?? '',
    paperSha256: input.paperSha256 ?? '',
  })
  const storageKey = 'mc-manager-idempotency:upload-map'
  const saved = readSavedIdempotency(storageKey)
  const idempotencyKey = saved?.fingerprint === fingerprint ? saved.key : createIdempotencyKey()
  sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: idempotencyKey }))
  const form = new FormData()
  form.append('map', input.mapFile, 'map.zip')
  form.append('name', input.name)
  form.append('mc_version', input.mcVersion)
  form.append('paper_build', input.paperBuild)
  form.append('java_major', String(input.javaMajor))
  if (input.paperUrl) form.append('paper_url', input.paperUrl)
  if (input.paperSha256) form.append('paper_sha256', input.paperSha256)
  if (input.resourcePack) {
    form.append('resource_pack', input.resourcePack, input.resourcePack.name)
    form.append('resource_pack_required', String(input.resourcePackRequired ?? false))
    if (input.resourcePackPrompt) {
      form.append('resource_pack_prompt', input.resourcePackPrompt)
    }
  }
  input.resources.forEach((file, index) => form.append(`res${index + 1}`, file, file.name))

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/maps')
    const token = tokenSession.get()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('Idempotency-Key', idempotencyKey)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status === 401) {
        tokenSession.clear()
        window.dispatchEvent(new CustomEvent('mc-manager:unauthorized'))
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        try {
          const body = JSON.parse(xhr.responseText) as ApiErrorBody
          const error = new ApiError(
            xhr.status,
            body.error.code,
            body.error.message,
            body.error.details,
          )
          if (!shouldRetainIdempotencyKey(error)) {
            clearSavedIdempotency(storageKey, idempotencyKey)
          }
          reject(error)
        } catch {
          const error = new ApiError(
            xhr.status,
            `http_${xhr.status}`,
            '上传失败，请检查文件和版本信息',
          )
          if (!shouldRetainIdempotencyKey(error)) {
            clearSavedIdempotency(storageKey, idempotencyKey)
          }
          reject(error)
        }
        return
      }
      clearSavedIdempotency(storageKey, idempotencyKey)
      resolve(JSON.parse(xhr.responseText) as MapUploadResult)
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', '网络中断，地图是否导入成功未知'))
    xhr.send(form)
  })
}
