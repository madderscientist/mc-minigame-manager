from enum import StrEnum


class ResourceState(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"


class TaskType(StrEnum):
    CREATE_GAME = "create_game"
    DELETE_GAME = "delete_game"
    START = "start"
    STOP = "stop"
    LOAD_BACKUP = "load_backup"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class DesiredState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class ObservedState(StrEnum):
    PREPARING = "preparing"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    BACKING_UP = "backing_up"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PortState(StrEnum):
    FREE = "free"
    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASING = "releasing"
