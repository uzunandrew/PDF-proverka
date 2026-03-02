"""Pydantic-модели для аудита."""
from pydantic import BaseModel
from typing import Optional
from enum import Enum
from datetime import datetime


class AuditStage(str, Enum):
    PREPARE = "prepare"
    TILE_BATCHES = "tile_batches"
    TILE_AUDIT = "tile_audit"
    MAIN_AUDIT = "main_audit"
    MERGE = "merge"
    NORM_VERIFY = "norm_verify"
    NORM_FIX = "norm_fix"
    EXCEL = "excel"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditJob(BaseModel):
    """Текущая задача аудита."""
    job_id: str
    project_id: str
    stage: AuditStage = AuditStage.PREPARE
    status: JobStatus = JobStatus.QUEUED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress_current: int = 0
    progress_total: int = 0
    error_message: Optional[str] = None
    # Heartbeat & ETA
    last_heartbeat: Optional[str] = None       # ISO timestamp последнего heartbeat
    batch_started_at: Optional[str] = None      # когда начался текущий пакет
    batch_durations: list[float] = []            # длительности завершённых пакетов (сек)


class BatchStatus(BaseModel):
    """Статус одного пакета тайлов."""
    batch_id: int
    tile_count: int = 0
    pages_included: list[int] = []
    status: str = "pending"  # pending / running / done / error
    result_size_kb: float = 0.0
    duration_minutes: float = 0.0


class AuditStatusResponse(BaseModel):
    """Ответ на запрос статуса аудита."""
    project_id: str
    is_running: bool = False
    current_job: Optional[AuditJob] = None
    batches: list[BatchStatus] = []
