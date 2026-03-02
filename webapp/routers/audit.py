"""
REST API для запуска и управления аудитом.
"""
import asyncio
import traceback
from fastapi import APIRouter, HTTPException, Query
from webapp.services.pipeline_service import pipeline_manager
from webapp.services import project_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


async def _safe_task(coro, name: str = "task"):
    """Обёртка для asyncio.create_task — логирует ошибки в stdout."""
    try:
        return await coro
    except asyncio.CancelledError:
        print(f"[AUDIT] {name}: отменено")
        raise
    except Exception as e:
        print(f"[AUDIT] {name}: ИСКЛЮЧЕНИЕ: {e}")
        traceback.print_exc()
        raise


# ─── Статичные роуты (ПЕРЕД динамическими /{project_id}/...) ───

@router.post("/all/full")
async def start_all_projects():
    """Запустить полный конвейер для ВСЕХ проектов последовательно."""
    if pipeline_manager.is_running("__ALL__"):
        raise HTTPException(409, "Массовый аудит уже запущен")

    asyncio.create_task(
        _safe_task(pipeline_manager.start_all_projects(), "start_all_projects")
    )
    return {"status": "started", "message": "Полный конвейер запущен для всех проектов"}


@router.get("/live-status")
async def get_all_live_status():
    """Быстрый polling: live-статус всех запущенных задач + обновлённые batches."""
    # Ленивая очистка зомби-задач при каждом polling
    pipeline_manager.cleanup_zombies()

    running = {}
    for pid, job in pipeline_manager.active_jobs.items():
        running[pid] = {
            "stage": job.stage.value,
            "status": job.status.value,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "started_at": job.started_at,
            # Heartbeat & ETA
            "last_heartbeat": job.last_heartbeat,
            "batch_started_at": job.batch_started_at,
            "eta_sec": pipeline_manager._calculate_eta(job),
        }

    # Также отдаём актуальные completed_batches для всех проектов
    from webapp.config import PROJECTS_DIR
    import json
    batches_info = {}
    if PROJECTS_DIR.exists():
        for entry in PROJECTS_DIR.iterdir():
            if not entry.is_dir():
                continue
            batches_file = entry / "_output" / "tile_batches.json"
            if not batches_file.exists():
                continue
            try:
                with open(batches_file, "r", encoding="utf-8") as f:
                    bd = json.load(f)
                total = bd.get("total_batches", len(bd.get("batches", [])))
                completed = 0
                for i in range(1, total + 1):
                    bf = entry / "_output" / f"tile_batch_{i:03d}.json"
                    if bf.exists() and bf.stat().st_size > 100:
                        completed += 1
                batches_info[entry.name] = {"total": total, "completed": completed}
            except Exception:
                pass

    return {"running": running, "batches": batches_info}


# ─── Динамические роуты /{project_id}/... ───

@router.post("/{project_id}/prepare")
async def prepare_project(project_id: str):
    """Запустить подготовку проекта (текст + тайлы)."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.start_prepare(project_id)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/{project_id}/tile-audit")
async def start_tile_audit(
    project_id: str,
    start_from: int = Query(1, description="Начать с пакета N"),
):
    """Запустить пакетный анализ тайлов."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.start_tile_audit(project_id, start_from=start_from)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/{project_id}/main-audit")
async def start_main_audit(project_id: str):
    """Запустить основной аудит (Claude CLI)."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.start_main_audit(project_id)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/{project_id}/full")
async def start_full_audit(project_id: str):
    """Запустить полный конвейер (подготовка -> тайлы -> аудит -> верификация норм -> Excel)."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.start_full_audit(project_id)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/{project_id}/resume-info")
async def get_resume_info(project_id: str):
    """Определить, с какого этапа можно продолжить пайплайн."""
    _check_project(project_id)
    info = pipeline_manager.detect_resume_stage(project_id)
    return info


@router.post("/{project_id}/resume")
async def resume_pipeline(project_id: str):
    """Продолжить пайплайн с места ошибки/остановки."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.resume_pipeline(project_id)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/{project_id}/verify-norms")
async def start_norm_verification(project_id: str):
    """Запустить верификацию нормативных ссылок через WebSearch."""
    _check_project(project_id)
    try:
        job = await pipeline_manager.start_norm_verify(project_id)
        return {"status": "started", "job": job.model_dump()}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/{project_id}/status")
async def get_audit_status(project_id: str):
    """Получить текущий статус аудита."""
    job = pipeline_manager.get_job(project_id)
    status = project_service.get_project_status(project_id)
    return {
        "project_id": project_id,
        "is_running": pipeline_manager.is_running(project_id),
        "current_job": job.model_dump() if job else None,
        "pipeline": status.pipeline.model_dump() if status else None,
    }


@router.delete("/{project_id}/cancel")
async def cancel_audit(project_id: str):
    """Отменить запущенный аудит."""
    success = await pipeline_manager.cancel(project_id)
    if not success:
        raise HTTPException(404, f"Нет запущенного аудита для '{project_id}'")
    return {"status": "cancelled"}


def _check_project(project_id: str):
    """Проверка существования проекта."""
    status = project_service.get_project_status(project_id)
    if not status:
        raise HTTPException(404, f"Проект '{project_id}' не найден")
    if not status.has_pdf:
        raise HTTPException(400, f"В проекте '{project_id}' отсутствует PDF файл")
