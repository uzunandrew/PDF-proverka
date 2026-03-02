"""
Pipeline Manager — оркестрация конвейера аудита.
Запуск, отмена, отслеживание прогресса.
"""
import asyncio
import json
import os
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Optional

from webapp.config import (
    BASE_DIR, PROJECTS_DIR,
    PROCESS_PROJECT_SCRIPT, GENERATE_BATCHES_SCRIPT,
    MERGE_RESULTS_SCRIPT, GENERATE_EXCEL_SCRIPT,
    VERIFY_NORMS_SCRIPT, DEFAULT_TILE_QUALITY,
    MAX_PARALLEL_BATCHES,
)
from webapp.models.audit import AuditJob, AuditStage, JobStatus
from webapp.models.websocket import WSMessage
from webapp.services.process_runner import run_script
from webapp.services import claude_runner
from webapp.ws.manager import ws_manager


class PipelineManager:
    """Управляет запущенными аудитами. Singleton."""

    def __init__(self):
        self.active_jobs: dict[str, AuditJob] = {}      # project_id -> job
        self._tasks: dict[str, asyncio.Task] = {}        # project_id -> asyncio.Task
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}  # project_id -> heartbeat Task

    ZOMBIE_TIMEOUT_SEC = 600  # 10 минут без heartbeat = зомби

    def is_running(self, project_id: str) -> bool:
        return project_id in self.active_jobs

    def get_job(self, project_id: str) -> Optional[AuditJob]:
        return self.active_jobs.get(project_id)

    def cleanup_zombies(self):
        """Очистить зомби-задачи (нет heartbeat более ZOMBIE_TIMEOUT_SEC)."""
        now = datetime.now()
        zombies = []
        for pid, job in list(self.active_jobs.items()):
            if job.status != JobStatus.RUNNING:
                zombies.append(pid)
                continue
            # Определяем последнюю активность
            last_activity = job.last_heartbeat or job.started_at
            if last_activity:
                try:
                    last_time = datetime.fromisoformat(last_activity)
                    elapsed = (now - last_time).total_seconds()
                    if elapsed > self.ZOMBIE_TIMEOUT_SEC:
                        zombies.append(pid)
                except (ValueError, TypeError):
                    zombies.append(pid)
            else:
                zombies.append(pid)

        for pid in zombies:
            print(f"[PipelineManager] Очистка зомби-задачи: {pid}")
            self._cleanup(pid)

    async def cancel(self, project_id: str) -> bool:
        """Отменить запущенный аудит."""
        job = self.active_jobs.get(project_id)
        if not job:
            return False
        job.status = JobStatus.CANCELLED
        task = self._tasks.get(project_id)
        if task:
            task.cancel()
        self._cleanup(project_id)
        await ws_manager.broadcast_to_project(
            project_id,
            WSMessage.log(project_id, "Аудит отменён пользователем", "warn"),
        )
        return True

    def _cleanup(self, project_id: str):
        self._stop_heartbeat(project_id)
        self.active_jobs.pop(project_id, None)
        self._tasks.pop(project_id, None)

    def _reset_job_progress(self, job: AuditJob):
        """Сбросить прогресс и ETA-данные при переходе между этапами пайплайна."""
        job.progress_current = 0
        job.progress_total = 0
        job.batch_durations = []
        job.batch_started_at = None

    def _clean_stage_files(self, project_id: str, files: list[str]):
        """Удалить устаревшие JSON-файлы этапов перед перезапуском."""
        output_dir = PROJECTS_DIR / project_id / "_output"
        for filename in files:
            if "*" in filename:
                # glob-шаблон (например tile_batch_*.json)
                for path in output_dir.glob(filename):
                    path.unlink()
                    print(f"[{project_id}:clean] Удалён {path.name}")
            else:
                path = output_dir / filename
                if path.exists():
                    path.unlink()
                    print(f"[{project_id}:clean] Удалён {filename}")

    # ─── Вспомогательный метод для broadcast лога ───
    async def _log(self, job: AuditJob, message: str, level: str = "info"):
        # Дублируем в серверную консоль для диагностики
        tag = f"[{job.project_id}:{job.stage.value}]"
        if level in ("error", "warn"):
            print(f"{tag} [{level.upper()}] {message}")
        await ws_manager.broadcast_to_project(
            job.project_id,
            WSMessage.log(job.project_id, message, level, job.stage.value),
        )

    async def _progress(self, job: AuditJob, current: int, total: int):
        job.progress_current = current
        job.progress_total = total
        await ws_manager.broadcast_to_project(
            job.project_id,
            WSMessage.progress(job.project_id, current, total, job.stage.value),
        )

    # ─── Heartbeat ─────────────────────────────────────────────
    async def _start_heartbeat(self, job: AuditJob):
        """Запустить heartbeat-цикл для задачи."""
        self._stop_heartbeat(job.project_id)
        task = asyncio.create_task(self._heartbeat_loop(job))
        self._heartbeat_tasks[job.project_id] = task

    def _stop_heartbeat(self, project_id: str):
        """Остановить heartbeat-цикл."""
        task = self._heartbeat_tasks.pop(project_id, None)
        if task and not task.done():
            task.cancel()

    async def _heartbeat_loop(self, job: AuditJob):
        """Отправлять heartbeat каждые 15 секунд."""
        try:
            while True:
                await asyncio.sleep(15)
                if job.status != JobStatus.RUNNING:
                    break

                now = datetime.now()
                job.last_heartbeat = now.isoformat()

                # Вычислить elapsed
                ref_time = job.batch_started_at or job.started_at
                if ref_time:
                    started = datetime.fromisoformat(ref_time)
                    elapsed_sec = (now - started).total_seconds()
                else:
                    elapsed_sec = 0

                # Вычислить ETA
                eta_sec = self._calculate_eta(job)

                await ws_manager.broadcast_to_project(
                    job.project_id,
                    WSMessage.heartbeat(
                        project=job.project_id,
                        stage=job.stage.value,
                        elapsed_sec=elapsed_sec,
                        process_alive=True,
                        batch_current=job.progress_current,
                        batch_total=job.progress_total,
                        eta_sec=eta_sec,
                    ),
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # Heartbeat не должен ронять основной процесс

    def _calculate_eta(self, job: AuditJob) -> Optional[float]:
        """Рассчитать ETA на основе среднего времени пакетов."""
        if not job.batch_durations or job.progress_total <= 0:
            return None
        avg_duration = sum(job.batch_durations) / len(job.batch_durations)
        remaining = job.progress_total - job.progress_current
        if remaining <= 0:
            return 0
        return avg_duration * remaining

    # ─── Определение точки возобновления ───

    def detect_resume_stage(self, project_id: str) -> dict:
        """
        Определить, с какого этапа можно продолжить пайплайн.
        Возвращает: {stage, stage_label, detail, can_resume}
        """
        output_dir = PROJECTS_DIR / project_id / "_output"
        tiles_dir = output_dir / "tiles"

        # Проверяем наличие ключевых файлов
        has_tiles = tiles_dir.is_dir() and any(tiles_dir.glob("page_*//*.png"))
        has_batches = (output_dir / "tile_batches.json").exists()
        has_02 = (output_dir / "02_tiles_analysis.json").exists()
        has_03 = (output_dir / "03_findings.json").exists()
        has_norm_checks = (output_dir / "norm_checks.json").exists()
        has_03a = (output_dir / "03a_norms_verified.json").exists()

        # Подсчёт завершённых батчей
        completed_batches = 0
        total_batches = 0
        if has_batches:
            try:
                with open(output_dir / "tile_batches.json", "r", encoding="utf-8") as f:
                    bd = json.load(f)
                total_batches = bd.get("total_batches", len(bd.get("batches", [])))
                for i in range(1, total_batches + 1):
                    bf = output_dir / f"tile_batch_{i:03d}.json"
                    if bf.exists() and bf.stat().st_size > 100:
                        completed_batches += 1
            except Exception:
                pass

        # Определяем первый незавершённый этап
        if not has_tiles:
            return {
                "stage": "prepare",
                "stage_label": "Подготовка",
                "detail": "Тайлы не созданы",
                "can_resume": True,
            }

        if not has_02:
            if completed_batches > 0 and completed_batches < total_batches:
                return {
                    "stage": "tile_audit",
                    "stage_label": "Анализ тайлов",
                    "detail": f"Пакеты: {completed_batches}/{total_batches}",
                    "start_from": completed_batches + 1,
                    "can_resume": True,
                }
            else:
                return {
                    "stage": "tile_audit",
                    "stage_label": "Анализ тайлов",
                    "detail": "02_tiles_analysis.json не создан",
                    "can_resume": True,
                }

        if not has_03:
            return {
                "stage": "main_audit",
                "stage_label": "Основной аудит",
                "detail": "03_findings.json не создан",
                "can_resume": True,
            }

        if not has_norm_checks:
            return {
                "stage": "norm_verify",
                "stage_label": "Верификация норм",
                "detail": "norm_checks.json не создан",
                "can_resume": True,
            }

        if not has_03a:
            # Проверяем — нужен ли 03a? (может, все нормы актуальны)
            try:
                with open(output_dir / "norm_checks.json", "r", encoding="utf-8") as f:
                    checks = json.load(f)
                needs_fix = any(c.get("needs_revision") for c in checks.get("checks", []))
                if needs_fix:
                    return {
                        "stage": "norm_verify",
                        "stage_label": "Пересмотр замечаний",
                        "detail": "Есть нормы для пересмотра, 03a не создан",
                        "can_resume": True,
                    }
            except Exception:
                pass

        # Всё завершено
        return {
            "stage": "completed",
            "stage_label": "Завершён",
            "detail": "Все этапы выполнены",
            "can_resume": False,
        }

    async def resume_pipeline(self, project_id: str) -> AuditJob:
        """Продолжить пайплайн с места ошибки."""
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        resume_info = self.detect_resume_stage(project_id)
        if not resume_info.get("can_resume"):
            raise RuntimeError("Все этапы уже завершены — нечего возобновлять")

        stage = resume_info["stage"]

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.PREPARE,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job

        task = asyncio.create_task(
            self._run_resumed_pipeline(job, stage, resume_info)
        )
        self._tasks[project_id] = task
        return job

    async def _run_resumed_pipeline(self, job: AuditJob, start_stage: str, resume_info: dict):
        """Запуск пайплайна с указанного этапа."""
        start_time = datetime.now()
        pid = job.project_id
        try:
            stages = ["prepare", "tile_audit", "main_audit", "norm_verify", "excel"]
            start_idx = stages.index(start_stage) if start_stage in stages else 0

            await self._log(
                job,
                f"Возобновление конвейера с этапа: {resume_info.get('stage_label', start_stage)} "
                f"({resume_info.get('detail', '')})",
                "info",
            )

            # ЭТАП 1: Подготовка (если нужно)
            if start_idx <= 0:
                job.stage = AuditStage.PREPARE
                print(f"[{pid}:resume] ═══ ЭТАП 1: Подготовка ═══")
                await self._log(job, "═══ ЭТАП 1: Подготовка (текст + тайлы) ═══")
                exit_code, _, stderr = await run_script(
                    str(PROCESS_PROJECT_SCRIPT),
                    [f"projects/{pid}", "--quality", DEFAULT_TILE_QUALITY],
                    on_output=lambda msg: self._log(job, msg),
                )
                if exit_code != 0:
                    raise RuntimeError(f"Подготовка: {stderr}")

                if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                    return

            # ЭТАП 2: Пакетный анализ тайлов
            if start_idx <= 1:
                tile_start_from = resume_info.get("start_from", 1) if start_idx == 1 else 1
                if start_idx == 1 and tile_start_from > 1:
                    # Частичный resume — не удаляем уже обработанные батчи
                    await self._log(job, f"Продолжение анализа тайлов с пакета {tile_start_from}")
                else:
                    self._clean_stage_files(pid, [
                        "tile_batch_*.json", "02_tiles_analysis.json",
                    ])

                self._reset_job_progress(job)
                print(f"[{pid}:resume] ═══ ЭТАП 2: Тайлы (start_from={tile_start_from}) ═══")
                await self._log(job, f"═══ ЭТАП 2: Пакетный анализ тайлов (с пакета {tile_start_from}) ═══")
                await self._run_tile_audit(job, start_from=tile_start_from)

                if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                    await self._log(job, f"ЭТАП 2 FAILED — остановка", "error")
                    return

                self.active_jobs[pid] = job
                self._tasks[pid] = asyncio.current_task()

            # ЭТАП 3: Основной аудит
            if start_idx <= 2:
                self._clean_stage_files(pid, [
                    "00_init.json", "01_text_analysis.json", "03_findings.json",
                ])
                self._reset_job_progress(job)
                job.stage = AuditStage.MAIN_AUDIT
                job.status = JobStatus.RUNNING
                print(f"[{pid}:resume] ═══ ЭТАП 3: Основной аудит ═══")
                await self._log(job, "═══ ЭТАП 3: Основной аудит Claude ═══")
                await self._run_main_audit(job)

                if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                    await self._log(job, f"ЭТАП 3 FAILED — остановка", "error")
                    return

                self.active_jobs[pid] = job
                self._tasks[pid] = asyncio.current_task()

            # ЭТАП 4: Верификация норм
            if start_idx <= 3:
                self._clean_stage_files(pid, [
                    "03a_norms_verified.json", "norm_checks.json",
                ])
                self._reset_job_progress(job)
                findings_path = PROJECTS_DIR / pid / "_output" / "03_findings.json"
                if findings_path.exists():
                    job.stage = AuditStage.NORM_VERIFY
                    job.status = JobStatus.RUNNING
                    print(f"[{pid}:resume] ═══ ЭТАП 4: Верификация норм ═══")
                    await self._log(job, "═══ ЭТАП 4: Верификация нормативных ссылок ═══")
                    await self._run_norm_verification(job)

                    if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                        return

                    self.active_jobs[pid] = job
                    self._tasks[pid] = asyncio.current_task()
                else:
                    await self._log(job, "03_findings.json не найден — пропуск верификации", "warn")

            # ЭТАП 5: Excel
            self._reset_job_progress(job)
            job.stage = AuditStage.EXCEL
            job.status = JobStatus.RUNNING
            print(f"[{pid}:resume] ═══ ЭТАП 5: Excel ═══")
            await self._log(job, "═══ ЭТАП 5: Генерация Excel ═══")
            project_path = str(PROJECTS_DIR / pid)
            exit_code, _, _ = await run_script(
                str(GENERATE_EXCEL_SCRIPT),
                args=[project_path],
                env_overrides={"AUDIT_NO_OPEN": "1"},
                on_output=lambda msg: self._log(job, msg),
            )

            duration = round((datetime.now() - start_time).total_seconds() / 60, 1)
            job.status = JobStatus.COMPLETED
            print(f"[{pid}:resume] ═══ Конвейер завершён за {duration} мин ═══")
            await self._log(job, f"Конвейер завершён за {duration} мин.", "info")

            await ws_manager.broadcast_to_project(
                pid, WSMessage.complete(pid, duration_minutes=duration),
            )

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(pid)

    # ─── Запуск подготовки ───
    async def start_prepare(self, project_id: str) -> AuditJob:
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.PREPARE,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job
        task = asyncio.create_task(self._run_prepare(job))
        self._tasks[project_id] = task
        return job

    async def _run_prepare(self, job: AuditJob):
        try:
            await self._log(job, "Запуск подготовки проекта (текст + тайлы)...")
            await self._start_heartbeat(job)

            exit_code, stdout, stderr = await run_script(
                str(PROCESS_PROJECT_SCRIPT),
                [f"projects/{job.project_id}", "--quality", DEFAULT_TILE_QUALITY],
                on_output=lambda msg: self._log(job, msg),
            )

            if exit_code == 0:
                await self._log(job, "Подготовка завершена успешно", "info")
                job.status = JobStatus.COMPLETED
            else:
                await self._log(job, f"Ошибка подготовки (код {exit_code})", "error")
                if stderr:
                    await self._log(job, stderr, "error")
                job.status = JobStatus.FAILED
                job.error_message = stderr or f"Exit code: {exit_code}"
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(job.project_id)

    # ─── Запуск пакетного анализа тайлов ───
    async def start_tile_audit(self, project_id: str, start_from: int = 1) -> AuditJob:
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.TILE_AUDIT,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job
        task = asyncio.create_task(self._run_tile_audit(job, start_from))
        self._tasks[project_id] = task
        return job

    async def _run_tile_audit(self, job: AuditJob, start_from: int = 1):
        pid = job.project_id
        try:
            output_dir = PROJECTS_DIR / job.project_id / "_output"
            batches_file = output_dir / "tile_batches.json"

            # Шаг 1: Генерация пакетов (если нет или устарели)
            regenerate = False
            if not batches_file.exists():
                print(f"[{pid}:tile] tile_batches.json не существует → regenerate")
                regenerate = True
            else:
                # Проверяем актуальность по двум критериям:
                # 1) tile_config_source должен совпадать
                # 2) количество тайлов в батчах = реальному количеству на диске
                info_path = PROJECTS_DIR / job.project_id / "project_info.json"
                with open(info_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                current_source = info.get("tile_config_source", "")
                with open(batches_file, "r", encoding="utf-8") as f:
                    bdata = json.load(f)
                old_source = bdata.get("tile_config_source", "")
                old_tile_count = bdata.get("total_tiles", 0)

                # Подсчитать реальные тайлы на диске
                tiles_dir = output_dir / "tiles"
                real_tile_count = 0
                if tiles_dir.is_dir():
                    for page_dir in tiles_dir.iterdir():
                        if page_dir.is_dir() and page_dir.name.startswith("page_"):
                            real_tile_count += sum(1 for f in page_dir.iterdir() if f.suffix == ".png")

                print(f"[{pid}:tile] tile_config_source: файл={old_source}, проект={current_source}")
                print(f"[{pid}:tile] tile_count: батчи={old_tile_count}, диск={real_tile_count}")

                stale_reason = None
                if current_source != old_source:
                    stale_reason = f"tile_config_source изменился ({old_source} → {current_source})"
                elif old_tile_count != real_tile_count:
                    stale_reason = f"количество тайлов изменилось ({old_tile_count} → {real_tile_count})"

                if stale_reason:
                    regenerate = True
                    await self._log(job, f"{stale_reason}, пересоздаём пакеты...")
                    # Удалить старые tile_batch_NNN.json
                    deleted_count = 0
                    for f_old in output_dir.glob("tile_batch_*.json"):
                        f_old.unlink()
                        deleted_count += 1
                    print(f"[{pid}:tile] Удалено {deleted_count} старых tile_batch_*.json")

            if regenerate:
                job.stage = AuditStage.TILE_BATCHES
                await self._log(job, "Генерация пакетов тайлов...")
                exit_code, _, stderr = await run_script(
                    str(GENERATE_BATCHES_SCRIPT),
                    [f"projects/{job.project_id}"],
                    on_output=lambda msg: self._log(job, msg),
                )
                if exit_code != 0:
                    raise RuntimeError(f"generate_tile_batches.py: {stderr}")
                await self._log(job, "Пакеты сгенерированы")

            # Загружаем пакеты
            with open(batches_file, "r", encoding="utf-8") as f:
                batches_data = json.load(f)

            batches = batches_data.get("batches", [])
            total = len(batches)
            job.progress_total = total

            # Свежий запуск (не resume) — удалить старые результаты батчей
            if start_from <= 1:
                deleted_batch_count = 0
                for old_file in output_dir.glob("tile_batch_*.json"):
                    old_file.unlink()
                    deleted_batch_count += 1
                if deleted_batch_count:
                    print(f"[{pid}:tile] Свежий запуск — удалено {deleted_batch_count} старых tile_batch_*.json")
                    await self._log(job, f"Очистка: удалено {deleted_batch_count} старых результатов батчей")

            # Загружаем project_info
            info_path = PROJECTS_DIR / job.project_id / "project_info.json"
            with open(info_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)

            # Шаг 2: Параллельная обработка пакетов
            job.stage = AuditStage.TILE_AUDIT
            parallel = MAX_PARALLEL_BATCHES
            print(f"[{pid}:tile] Запуск пакетного анализа: {total} пакетов, start_from={start_from}, parallel={parallel}")
            await self._log(job, f"Запуск пакетного анализа тайлов: {total} пакетов (x{parallel} параллельно)")
            await self._start_heartbeat(job)

            semaphore = asyncio.Semaphore(parallel)
            completed_count = 0
            error_count = 0

            async def _process_batch(batch):
                nonlocal completed_count, error_count
                batch_id = batch["batch_id"]

                # Пропуск уже обработанных
                if batch_id < start_from:
                    return

                result_file = output_dir / f"tile_batch_{batch_id:03d}.json"
                if result_file.exists() and result_file.stat().st_size > 100:
                    completed_count += 1
                    job.progress_current = completed_count
                    await self._progress(job, completed_count, total)
                    return

                async with semaphore:
                    if job.status == JobStatus.CANCELLED:
                        return
                    # Остановка при слишком большом числе ошибок
                    if error_count >= 5:
                        return

                    tile_count = batch.get("tile_count", len(batch.get("tiles", [])))
                    print(f"[{pid}:tile] Пакет {batch_id}/{total}: {tile_count} тайлов...")
                    await self._log(job, f"Пакет {batch_id}/{total}: {tile_count} тайлов...")

                    batch_start_time = datetime.now()
                    job.batch_started_at = batch_start_time.isoformat()

                    exit_code, output = await claude_runner.run_tile_batch(
                        batch, project_info, job.project_id, total,
                        on_output=lambda msg: self._log(job, msg),
                    )
                    print(f"[{pid}:tile] Пакет {batch_id}/{total}: exit_code={exit_code}")

                    batch_duration = (datetime.now() - batch_start_time).total_seconds()
                    job.batch_durations.append(batch_duration)

                    if exit_code == 0:
                        if result_file.exists():
                            size_kb = round(result_file.stat().st_size / 1024, 1)
                            await self._log(job, f"Пакет {batch_id}/{total}: OK ({size_kb} KB)", "info")
                        else:
                            await self._log(job, f"Пакет {batch_id}/{total}: файл не создан", "warn")
                            if output and output.strip():
                                await self._log(job, f"  Вывод: {output.strip()[:500]}", "warn")
                    else:
                        error_count += 1
                        error_snippet = (output or "").strip()[:500]
                        await self._log(job, f"Пакет {batch_id}/{total}: ОШИБКА (код {exit_code})", "error")
                        if error_snippet:
                            await self._log(job, f"  Детали: {error_snippet}", "error")
                        if error_count >= 5:
                            await self._log(job, f"{error_count} ошибок — пакетный анализ остановлен", "error")

                    completed_count += 1
                    job.progress_current = completed_count
                    await self._progress(job, completed_count, total)

            # Запуск всех батчей параллельно (семафор ограничивает одновременность)
            tasks = [_process_batch(batch) for batch in batches]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Проверка: если ВСЕ батчи провалились — это FAILED, не COMPLETED
            if error_count >= total:
                job.status = JobStatus.FAILED
                job.error_message = f"Все {total} пакетов завершились с ошибкой"
                await self._log(job, f"Все {total} пакетов завершились с ошибкой — этап FAILED", "error")
                return

            # Шаг 3: Слияние результатов
            if job.status != JobStatus.CANCELLED:
                job.stage = AuditStage.MERGE
                await self._log(job, "Слияние результатов пакетного анализа...")
                exit_code, _, stderr = await run_script(
                    str(MERGE_RESULTS_SCRIPT),
                    [f"projects/{job.project_id}"],
                    on_output=lambda msg: self._log(job, msg),
                )
                if exit_code == 0:
                    await self._log(job, "02_tiles_analysis.json создан", "info")
                else:
                    await self._log(job, f"Ошибка слияния: {stderr}", "error")

            if error_count > 0:
                await self._log(job, f"Пакетный анализ завершён с ошибками ({error_count}/{total} пакетов)", "warn")
            job.status = JobStatus.COMPLETED
            await self._log(job, "Пакетный анализ тайлов завершён", "info")

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(job.project_id)

    # ─── Запуск основного аудита ───
    async def start_main_audit(self, project_id: str) -> AuditJob:
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        # Очистка старых результатов — каждый запуск даёт свежие замечания
        self._clean_stage_files(project_id, [
            "00_init.json", "01_text_analysis.json", "03_findings.json",
        ])

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.MAIN_AUDIT,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job
        task = asyncio.create_task(self._run_main_audit(job))
        self._tasks[project_id] = task
        return job

    async def _run_main_audit(self, job: AuditJob):
        try:
            info_path = PROJECTS_DIR / job.project_id / "project_info.json"
            with open(info_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)

            await self._log(job, "Запуск основного аудита Claude...")
            await self._start_heartbeat(job)

            exit_code, output = await claude_runner.run_main_audit(
                project_info, job.project_id,
                on_output=lambda msg: self._log(job, msg),
            )

            if exit_code == 0:
                # Сохраняем вывод как audit_results
                ts = datetime.now().strftime("%Y%m%d_%H%M")
                out_file = PROJECTS_DIR / job.project_id / "_output" / f"audit_results_{ts}.md"
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(output)
                await self._log(job, f"Аудит завершён. Результат: {out_file.name}", "info")
                job.status = JobStatus.COMPLETED
            else:
                await self._log(job, f"Ошибка аудита (код {exit_code})", "error")
                job.status = JobStatus.FAILED
                job.error_message = f"Exit code: {exit_code}"

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(job.project_id)

    # ─── Верификация нормативных ссылок ───
    async def start_norm_verify(self, project_id: str) -> AuditJob:
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        # Очистка старых результатов верификации
        self._clean_stage_files(project_id, [
            "03a_norms_verified.json", "norm_checks.json",
        ])

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.NORM_VERIFY,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job
        task = asyncio.create_task(self._run_norm_verification(job))
        self._tasks[project_id] = task
        return job

    async def _run_norm_verification(self, job: AuditJob):
        """
        Верификация нормативных ссылок:
        1. Извлечь нормы из 03_findings.json (Python)
        2. Проверить через Claude CLI + WebSearch
        3. Если есть устаревшие — пересмотреть замечания через Claude CLI
        """
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR))
            from verify_norms import (
                extract_norms_from_findings,
                format_norms_for_template,
                format_findings_to_fix,
            )

            output_dir = PROJECTS_DIR / job.project_id / "_output"
            findings_path = output_dir / "03_findings.json"
            norm_checks_path = output_dir / "norm_checks.json"
            verified_path = output_dir / "03a_norms_verified.json"

            # Проверка: нужен 03_findings.json
            if not findings_path.exists():
                raise RuntimeError(
                    "Файл 03_findings.json не найден. Сначала выполните основной аудит."
                )

            # ── Шаг 1: Извлечение норм ──
            job.stage = AuditStage.NORM_VERIFY
            await self._log(job, "Шаг 1: Извлечение нормативных ссылок из замечаний...")
            await self._start_heartbeat(job)

            norms_data = extract_norms_from_findings(findings_path)
            total_norms = norms_data["total_unique_norms"]

            if total_norms == 0:
                await self._log(job, "Нормативных ссылок не найдено. Верификация не требуется.", "warn")
                job.status = JobStatus.COMPLETED
                return

            await self._log(job, f"Найдено {total_norms} уникальных нормативных ссылок")
            norms_list_text = format_norms_for_template(norms_data)

            # ── Шаг 2: Проверка через Claude CLI + WebSearch ──
            await self._log(job, f"Шаг 2: Проверка актуальности {total_norms} норм через WebSearch...")
            job.progress_total = total_norms

            exit_code, output = await claude_runner.run_norm_verify(
                norms_list_text, job.project_id,
                on_output=lambda msg: self._log(job, msg),
            )

            if exit_code != 0:
                await self._log(job, f"Ошибка верификации (код {exit_code})", "error")
                raise RuntimeError(f"Claude CLI norm_verify: exit code {exit_code}")

            # Проверяем что файл создан
            if not norm_checks_path.exists():
                await self._log(job, "norm_checks.json не создан — Claude не записал результат", "warn")
                job.status = JobStatus.COMPLETED
                return

            # Читаем результаты
            with open(norm_checks_path, "r", encoding="utf-8") as f:
                checks_data = json.load(f)

            checks = checks_data.get("checks", [])
            needs_fix = [c for c in checks if c.get("needs_revision", False)]

            results = checks_data.get("meta", {}).get("results", {})
            await self._log(
                job,
                f"Результат: {results.get('active', 0)} актуальных, "
                f"{results.get('outdated_edition', 0)} устаревших, "
                f"{results.get('replaced', 0)} заменённых, "
                f"{results.get('cancelled', 0)} отменённых",
                "info",
            )

            # ── Шаг 3: Пересмотр замечаний (если нужен) ──
            if needs_fix:
                job.stage = AuditStage.NORM_FIX
                await self._log(
                    job,
                    f"Шаг 3: Пересмотр {len(needs_fix)} замечаний с устаревшими нормами..."
                )

                findings_to_fix_text = format_findings_to_fix(norm_checks_path, findings_path)

                exit_code, output = await claude_runner.run_norm_fix(
                    findings_to_fix_text, job.project_id,
                    on_output=lambda msg: self._log(job, msg),
                )

                if exit_code == 0 and verified_path.exists():
                    size_kb = round(verified_path.stat().st_size / 1024, 1)
                    await self._log(job, f"03a_norms_verified.json создан ({size_kb} KB)", "info")
                else:
                    await self._log(job, "Предупреждение: 03a_norms_verified.json не создан", "warn")
            else:
                await self._log(job, "Все нормы актуальны — пересмотр не требуется", "info")

            # ── Шаг 4: Обновление централизованной базы норм ──
            await self._update_norms_db(job)

            job.status = JobStatus.COMPLETED
            await self._log(job, "Верификация нормативных ссылок завершена", "info")

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(job.project_id)

    async def _update_norms_db(self, job: AuditJob):
        """Обновить централизованную базу норм из результатов верификации."""
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR))
            from update_norms_db import load_norms_db, save_norms_db, update_from_project

            project_path = PROJECTS_DIR / job.project_id
            db = load_norms_db()
            stats = update_from_project(db, project_path)

            if "error" in stats:
                await self._log(job, f"Обновление базы норм: {stats['error']}", "warn")
                return

            save_norms_db(db)
            total_changes = stats.get("added", 0) + stats.get("updated", 0)
            if total_changes > 0:
                await self._log(
                    job,
                    f"База норм обновлена: +{stats.get('added', 0)} новых, "
                    f"{stats.get('updated', 0)} обновлено "
                    f"(всего в базе: {len(db.get('norms', {}))})",
                    "info",
                )
            else:
                await self._log(job, f"База норм актуальна ({len(db.get('norms', {}))} записей)", "info")

        except Exception as e:
            # Ошибка обновления базы не должна ронять основной процесс
            await self._log(job, f"Предупреждение: не удалось обновить базу норм: {e}", "warn")
            print(f"[{job.project_id}:norms_db] Ошибка: {e}")

    # ─── Полный конвейер ───
    async def start_full_audit(self, project_id: str) -> AuditJob:
        if project_id in self.active_jobs:
            raise RuntimeError(f"Аудит уже запущен для {project_id}")

        job = AuditJob(
            job_id=str(uuid4()),
            project_id=project_id,
            stage=AuditStage.PREPARE,
            status=JobStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.active_jobs[project_id] = job
        task = asyncio.create_task(self._run_full_pipeline(job))
        self._tasks[project_id] = task
        return job

    async def _run_full_pipeline(self, job: AuditJob):
        start_time = datetime.now()
        pid = job.project_id
        try:
            # 1. Подготовка
            job.stage = AuditStage.PREPARE
            print(f"[{pid}] ═══ ЭТАП 1: Подготовка ═══")
            await self._log(job, "═══ ЭТАП 1: Подготовка (текст + тайлы) ═══")
            exit_code, _, stderr = await run_script(
                str(PROCESS_PROJECT_SCRIPT),
                [f"projects/{job.project_id}", "--quality", DEFAULT_TILE_QUALITY],
                on_output=lambda msg: self._log(job, msg),
            )
            if exit_code != 0:
                print(f"[{pid}] ЭТАП 1 ОШИБКА: код {exit_code}, stderr: {stderr[:300] if stderr else 'N/A'}")
                raise RuntimeError(f"Подготовка: {stderr}")
            print(f"[{pid}] ЭТАП 1 OK")

            if job.status == JobStatus.CANCELLED:
                return

            # 2. Пакетный анализ тайлов
            # Очистка: удалить старые результаты тайлового анализа
            self._clean_stage_files(pid, [
                "tile_batch_*.json", "02_tiles_analysis.json",
            ])
            self._reset_job_progress(job)
            print(f"[{pid}] ═══ ЭТАП 2: Пакетный анализ тайлов ═══")
            await self._log(job, "═══ ЭТАП 2: Пакетный анализ тайлов ═══")
            await self._run_tile_audit(job, start_from=1)
            print(f"[{pid}] ЭТАП 2 завершён, status={job.status.value}")

            if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                print(f"[{pid}] ЭТАП 2 завершён с ошибкой — конвейер остановлен")
                await self._log(job, f"Этап 2 FAILED: {job.error_message or 'все пакеты ошибочны'} — остановка", "error")
                return

            # Reset status after tile audit
            self.active_jobs[job.project_id] = job
            self._tasks[job.project_id] = asyncio.current_task()

            # 3. Основной аудит
            # Очистка: удалить старые результаты текстового анализа и замечаний
            self._clean_stage_files(pid, [
                "00_init.json", "01_text_analysis.json", "03_findings.json",
            ])
            self._reset_job_progress(job)
            job.stage = AuditStage.MAIN_AUDIT
            job.status = JobStatus.RUNNING
            print(f"[{pid}] ═══ ЭТАП 3: Основной аудит Claude ═══")
            await self._log(job, "═══ ЭТАП 3: Основной аудит Claude ═══")
            await self._run_main_audit(job)
            print(f"[{pid}] ЭТАП 3 завершён, status={job.status.value}")

            if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                print(f"[{pid}] ЭТАП 3 завершён с ошибкой — конвейер остановлен")
                await self._log(job, f"Этап 3 FAILED: {job.error_message or 'ошибка аудита'} — остановка", "error")
                return

            # Reset
            self.active_jobs[job.project_id] = job
            self._tasks[job.project_id] = asyncio.current_task()

            # 4. Верификация нормативных ссылок
            # Очистка: удалить старые результаты верификации
            self._clean_stage_files(pid, [
                "03a_norms_verified.json", "norm_checks.json",
            ])
            self._reset_job_progress(job)
            findings_path = PROJECTS_DIR / job.project_id / "_output" / "03_findings.json"
            if findings_path.exists():
                job.stage = AuditStage.NORM_VERIFY
                job.status = JobStatus.RUNNING
                print(f"[{pid}] ═══ ЭТАП 4: Верификация норм ═══")
                await self._log(job, "═══ ЭТАП 4: Верификация нормативных ссылок ═══")
                await self._run_norm_verification(job)
                print(f"[{pid}] ЭТАП 4 завершён, status={job.status.value}")

                if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                    print(f"[{pid}] ЭТАП 4 завершён с ошибкой — конвейер остановлен")
                    return

                # Reset
                self.active_jobs[job.project_id] = job
                self._tasks[job.project_id] = asyncio.current_task()
            else:
                print(f"[{pid}] ЭТАП 4 пропущен: 03_findings.json не найден")
                await self._log(job, "03_findings.json не найден — пропуск верификации норм", "warn")

            # 5. Excel (только для этого проекта, без автооткрытия)
            self._reset_job_progress(job)
            job.stage = AuditStage.EXCEL
            job.status = JobStatus.RUNNING
            print(f"[{pid}] ═══ ЭТАП 5: Excel ═══")
            await self._log(job, "═══ ЭТАП 5: Генерация Excel ═══")
            project_path = str(PROJECTS_DIR / job.project_id)
            exit_code, _, _ = await run_script(
                str(GENERATE_EXCEL_SCRIPT),
                args=[project_path],
                env_overrides={"AUDIT_NO_OPEN": "1"},
                on_output=lambda msg: self._log(job, msg),
            )
            print(f"[{pid}] ЭТАП 5 завершён, код={exit_code}")

            duration = round((datetime.now() - start_time).total_seconds() / 60, 1)
            job.status = JobStatus.COMPLETED
            print(f"[{pid}] ═══ Полный конвейер завершён за {duration} мин ═══")
            await self._log(job, f"Полный конвейер завершён за {duration} мин.", "info")

            # Отправляем событие complete
            await ws_manager.broadcast_to_project(
                job.project_id,
                WSMessage.complete(job.project_id, duration_minutes=duration),
            )

        except asyncio.CancelledError:
            print(f"[{pid}] Конвейер ОТМЕНЁН")
            job.status = JobStatus.CANCELLED
        except Exception as e:
            print(f"[{pid}] Конвейер ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await self._log(job, f"Исключение: {e}", "error")
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cleanup(job.project_id)


    # ─── Запуск ВСЕХ проектов последовательно ───
    async def start_all_projects(self, project_ids: list[str] | None = None) -> dict:
        """Запуск полного конвейера для всех проектов последовательно.

        Если project_ids не указан — берёт все проекты из PROJECTS_DIR.
        Возвращает dict с результатами: {project_id: "completed"|"failed"|"skipped"}.
        """
        from webapp.services.project_service import list_projects

        print("[ALL] ═══ start_all_projects() ВЫЗВАН ═══")

        try:
            # Определяем список проектов
            if project_ids:
                all_ids = project_ids
            else:
                projects = list_projects()
                all_ids = [p.project_id for p in projects if p.has_pdf]

            if not all_ids:
                print("[ALL] Нет проектов для обработки")
                return {"error": "Нет проектов для обработки"}

            total = len(all_ids)
            results = {}
            print(f"[ALL] Найдено {total} проектов: {all_ids}")

            # Создаём мета-задачу для отслеживания
            meta_job = AuditJob(
                job_id=str(uuid4()),
                project_id="__ALL__",
                stage=AuditStage.PREPARE,
                status=JobStatus.RUNNING,
                started_at=datetime.now().isoformat(),
                progress_total=total,
            )
            self.active_jobs["__ALL__"] = meta_job
            await self._start_heartbeat(meta_job)

            await ws_manager.broadcast_global(
                WSMessage.log("__ALL__", f"═══ Запуск конвейера для {total} проектов ═══", "info")
            )

            start_time = datetime.now()

            for idx, project_id in enumerate(all_ids, 1):
                if meta_job.status == JobStatus.CANCELLED:
                    results[project_id] = "cancelled"
                    continue

                meta_job.progress_current = idx - 1
                meta_job.stage = AuditStage.PREPARE

                print(f"[ALL] ▶ Проект {idx}/{total}: {project_id}")
                await ws_manager.broadcast_global(
                    WSMessage.log("__ALL__", f"▶ Проект {idx}/{total}: {project_id}", "info")
                )

                # Проверяем что проект не занят
                if self.is_running(project_id):
                    print(f"[ALL]   ⏭ Пропуск {project_id}: уже выполняется")
                    await ws_manager.broadcast_global(
                        WSMessage.log("__ALL__", f"  ⏭ Пропуск: уже выполняется", "warn")
                    )
                    results[project_id] = "skipped"
                    continue

                try:
                    # Запускаем полный конвейер и ЖДЁМ завершения
                    job = AuditJob(
                        job_id=str(uuid4()),
                        project_id=project_id,
                        stage=AuditStage.PREPARE,
                        status=JobStatus.RUNNING,
                        started_at=datetime.now().isoformat(),
                    )
                    self.active_jobs[project_id] = job
                    self._tasks[project_id] = asyncio.current_task()

                    await self._run_full_pipeline(job)

                    if job.status == JobStatus.COMPLETED:
                        results[project_id] = "completed"
                        print(f"[ALL]   ✓ {project_id}: завершён")
                        await ws_manager.broadcast_global(
                            WSMessage.log("__ALL__", f"  ✓ {project_id}: завершён", "info")
                        )
                    else:
                        results[project_id] = f"failed: {job.error_message or job.status.value}"
                        print(f"[ALL]   ✗ {project_id}: {job.status.value} — {job.error_message}")
                        await ws_manager.broadcast_global(
                            WSMessage.log("__ALL__", f"  ✗ {project_id}: {job.status.value}", "error")
                        )

                except Exception as e:
                    results[project_id] = f"error: {e}"
                    print(f"[ALL]   ✗ {project_id}: ИСКЛЮЧЕНИЕ: {e}")
                    import traceback
                    traceback.print_exc()
                    await ws_manager.broadcast_global(
                        WSMessage.log("__ALL__", f"  ✗ {project_id}: исключение: {e}", "error")
                    )
                finally:
                    # cleanup одного проекта (без удаления __ALL__)
                    self._stop_heartbeat(project_id)
                    self.active_jobs.pop(project_id, None)
                    self._tasks.pop(project_id, None)

            # Итог
            meta_job.progress_current = total
            duration = round((datetime.now() - start_time).total_seconds() / 60, 1)

            completed = sum(1 for v in results.values() if v == "completed")
            failed = sum(1 for v in results.values() if v.startswith(("failed", "error")))

            print(f"[ALL] ═══ Конвейер завершён: {completed}/{total} OK, {failed} ошибок, {duration} мин ═══")
            await ws_manager.broadcast_global(
                WSMessage.log(
                    "__ALL__",
                    f"═══ Конвейер завершён: {completed}/{total} OK, {failed} ошибок, {duration} мин ═══",
                    "info",
                )
            )

            meta_job.status = JobStatus.COMPLETED
            meta_job.completed_at = datetime.now().isoformat()
            self._cleanup("__ALL__")

            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "duration_minutes": duration,
                "details": results,
            }

        except Exception as e:
            print(f"[ALL] КРИТИЧЕСКАЯ ОШИБКА в start_all_projects: {e}")
            import traceback
            traceback.print_exc()
            # Очистка мета-задачи при краше
            self._cleanup("__ALL__")
            raise


# Глобальный экземпляр
pipeline_manager = PipelineManager()
