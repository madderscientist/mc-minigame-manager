export type ResourceState = 'preparing' | 'ready' | 'failed'
export type RuntimeState =
  | 'preparing'
  | 'starting'
  | 'ready'
  | 'stopping'
  | 'backing_up'
  | 'stopped'
  | 'failed'
  | 'unknown'
export type TaskType = 'create_game' | 'delete_game' | 'start' | 'stop' | 'load_backup'
export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled'
export type PortState = 'free' | 'reserved' | 'active' | 'releasing'

export interface Backup {
  backup_id: string
  reason: string
  clean_shutdown: boolean
  size_bytes: number
  sha256: string
  created_at: string
}

export interface ResourcePack {
  filename: string
  sha1: string
  sha256: string
  size_bytes: number
  pack_format: number
  required: boolean
  prompt: string | null
  url: string
}

export interface MapRecord {
  map_id: number
  state: ResourceState
  name: string
  mc_version: string
  data_version: number | null
  paper_build: string
  java_major: number
  created_at: string
  resource_pack: ResourcePack | null
}

export interface Game {
  game_id: number
  map_id: number
  map_name: string
  mc_version: string
  paper_build: string
  java_major: number
  state: ResourceState
  name: string
  created_at: string
  last_played_at: string | null
  runtime_state: RuntimeState | null
  port: number | null
  public_address: string | null
  backups: Backup[]
}

export interface Task {
  task_id: string
  type: TaskType
  status: TaskStatus
  step: string
  map_id: number | null
  game_id: number | null
  backup_id: string | null
  requested_port: number | null
  progress: number
  result: Record<string, unknown> | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface RunningGame {
  game_id: number
  game_name: string
  mc_version: string
  last_played_at: string | null
  observed_state: RuntimeState
  port: number
  public_address: string | null
  last_error: string | null
}

export interface Port {
  port: number
  state: PortState
  game_id: number | null
}

export interface Status {
  running_games: RunningGame[]
  tasks: Task[]
  ports: Port[]
}

export interface ApiErrorBody {
  error?: {
    code: string
    message: string
    details: Record<string, unknown>
  }
}

export interface TaskAccepted {
  task_id: string
  game_id: number
  status: TaskStatus
  map_id?: number
  port?: number
  backup_id?: string
}

export interface MapUploadInput {
  mapFile: File
  name: string
  paperBuild?: string
  resourcePack?: File
  resourcePackRequired?: boolean
  resourcePackPrompt?: string
  paperUrl?: string
  paperSha256?: string
  signal?: AbortSignal
}

export interface ChunkedUploadCreated {
  upload_id: string
  chunk_size: number
  completed: boolean
}

export interface MapUploadResult {
  map_id: number
  name: string
  mc_version: string
}
