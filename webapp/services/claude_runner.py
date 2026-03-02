"""
Claude CLI runner.
Формирование задач из шаблонов и запуск Claude CLI.
"""
import json
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable

from webapp.config import (
    BASE_DIR, PROJECTS_DIR,
    AUDIT_TASK_TEMPLATE, TILE_BATCH_TASK_TEMPLATE,
    NORM_VERIFY_TASK_TEMPLATE, NORM_FIX_TASK_TEMPLATE,
    CLAUDE_CLI, TILE_AUDIT_TOOLS, MAIN_AUDIT_TOOLS, NORM_VERIFY_TOOLS,
    CLAUDE_BATCH_TIMEOUT, CLAUDE_AUDIT_TIMEOUT,
    CLAUDE_NORM_VERIFY_TIMEOUT, CLAUDE_NORM_FIX_TIMEOUT,
)
from webapp.services.process_runner import run_command


def load_template(template_path: Path) -> str:
    """Загрузить шаблон задачи."""
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def prepare_tile_batch_task(
    batch_data: dict,
    project_info: dict,
    project_id: str,
    total_batches: int,
) -> str:
    """
    Подготовить задачу для одного пакета тайлов.
    Подставляет плейсхолдеры из шаблона tile_batch_task.md.
    """
    template = load_template(TILE_BATCH_TASK_TEMPLATE)

    batch_id = batch_data["batch_id"]
    tiles = batch_data.get("tiles", [])
    pages = batch_data.get("pages_included", [])

    # Формируем список тайлов
    tile_lines = []
    for tile in tiles:
        tile_path = str(PROJECTS_DIR / project_id / "_output" / "tiles" / tile["file"])
        tile_lines.append(
            f"- `{tile_path}` (стр. {tile.get('page', '?')}, "
            f"r{tile.get('row', '?')}c{tile.get('col', '?')})"
        )

    project_path = str(PROJECTS_DIR / project_id)
    output_path = str(PROJECTS_DIR / project_id / "_output")

    # MD-файл (структурированный текст)
    md_file = project_info.get("md_file")
    if md_file:
        md_file_path = str(PROJECTS_DIR / project_id / md_file)
    else:
        md_file_path = "(нет)"

    task = (
        template
        .replace("{BATCH_ID}", str(batch_id))
        .replace("{BATCH_ID_PADDED}", f"{batch_id:03d}")
        .replace("{TOTAL_BATCHES}", str(total_batches))
        .replace("{PROJECT_ID}", project_id)
        .replace("{PROJECT_NAME}", project_info.get("name", project_id))
        .replace("{PROJECT_PATH}", project_path)
        .replace("{OUTPUT_PATH}", output_path)
        .replace("{TILE_COUNT}", str(len(tiles)))
        .replace("{PAGES_LIST}", ", ".join(str(p) for p in pages))
        .replace("{TILE_LIST}", "\n".join(tile_lines))
        .replace("{BASE_DIR}", str(BASE_DIR))
        .replace("{MD_FILE_PATH}", md_file_path)
    )

    return task


def prepare_main_audit_task(
    project_info: dict,
    project_id: str,
) -> str:
    """
    Подготовить задачу для основного аудита.
    Подставляет пути проекта в audit_task.md.
    """
    template = load_template(AUDIT_TASK_TEMPLATE)

    output_dir = str(PROJECTS_DIR / project_id / "_output")
    project_path = str(PROJECTS_DIR / project_id)

    # MD-файл (структурированный текст)
    md_file = project_info.get("md_file")
    if md_file:
        md_file_path = str(PROJECTS_DIR / project_id / md_file)
    else:
        md_file_path = "(нет)"

    task = (
        template
        .replace("project/document_pdf_extracted.txt", f"{output_dir}\\extracted_text.txt")
        .replace("project/tiles", f"{output_dir}\\tiles")
        .replace("133/23-ГК-ЭМ1", project_info.get("name", project_id))
        .replace("{MD_FILE_PATH}", md_file_path)
    )

    return task


async def run_tile_batch(
    batch_data: dict,
    project_info: dict,
    project_id: str,
    total_batches: int,
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    """
    Запустить Claude CLI для одного пакета тайлов.

    Returns:
        (exit_code, output_text)
    """
    task_text = prepare_tile_batch_task(
        batch_data, project_info, project_id, total_batches
    )

    cmd = [
        CLAUDE_CLI,
        "-p",
        "--allowedTools", TILE_AUDIT_TOOLS,
        "--output-format", "text",
    ]

    exit_code, stdout, stderr = await run_command(
        cmd,
        input_text=task_text,
        on_output=on_output,
        env_overrides={"CLAUDECODE": None},  # Снимаем для вложенных сессий
        timeout=CLAUDE_BATCH_TIMEOUT,
    )

    # Объединяем stdout + stderr для полной диагностики
    combined = stdout
    if stderr and stderr.strip():
        combined += f"\n[STDERR]: {stderr.strip()}"
    return exit_code, combined


async def run_main_audit(
    project_info: dict,
    project_id: str,
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    """
    Запустить Claude CLI для основного аудита.

    Returns:
        (exit_code, output_text)
    """
    task_text = prepare_main_audit_task(project_info, project_id)

    cmd = [
        CLAUDE_CLI,
        "-p",
        "--allowedTools", MAIN_AUDIT_TOOLS,
        "--output-format", "text",
    ]

    exit_code, stdout, stderr = await run_command(
        cmd,
        input_text=task_text,
        on_output=on_output,
        env_overrides={"CLAUDECODE": None},
        timeout=CLAUDE_AUDIT_TIMEOUT,
    )

    combined = stdout
    if stderr and stderr.strip():
        combined += f"\n[STDERR]: {stderr.strip()}"
    return exit_code, combined


# ─── Верификация нормативных ссылок ───

def prepare_norm_verify_task(
    norms_list_text: str,
    project_id: str,
) -> str:
    """Подготовить задачу для верификации нормативных ссылок."""
    template = load_template(NORM_VERIFY_TASK_TEMPLATE)

    project_path = str(PROJECTS_DIR / project_id)

    task = (
        template
        .replace("{PROJECT_ID}", project_id)
        .replace("{PROJECT_PATH}", project_path)
        .replace("{BASE_DIR}", str(BASE_DIR))
        .replace("{NORMS_LIST}", norms_list_text)
    )
    return task


def prepare_norm_fix_task(
    findings_to_fix_text: str,
    project_id: str,
) -> str:
    """Подготовить задачу для пересмотра замечаний с устаревшими нормами."""
    template = load_template(NORM_FIX_TASK_TEMPLATE)

    project_path = str(PROJECTS_DIR / project_id)

    task = (
        template
        .replace("{PROJECT_ID}", project_id)
        .replace("{PROJECT_PATH}", project_path)
        .replace("{BASE_DIR}", str(BASE_DIR))
        .replace("{FINDINGS_TO_FIX}", findings_to_fix_text)
    )
    return task


async def run_norm_verify(
    norms_list_text: str,
    project_id: str,
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    """Запустить Claude CLI для верификации нормативных ссылок через WebSearch."""
    task_text = prepare_norm_verify_task(norms_list_text, project_id)

    cmd = [
        CLAUDE_CLI,
        "-p",
        "--allowedTools", NORM_VERIFY_TOOLS,
        "--output-format", "text",
    ]

    exit_code, stdout, stderr = await run_command(
        cmd,
        input_text=task_text,
        on_output=on_output,
        env_overrides={"CLAUDECODE": None},
        timeout=CLAUDE_NORM_VERIFY_TIMEOUT,
    )

    return exit_code, stdout


async def run_norm_fix(
    findings_to_fix_text: str,
    project_id: str,
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    """Запустить Claude CLI для пересмотра замечаний с учётом актуальных норм."""
    task_text = prepare_norm_fix_task(findings_to_fix_text, project_id)

    cmd = [
        CLAUDE_CLI,
        "-p",
        "--allowedTools", NORM_VERIFY_TOOLS,
        "--output-format", "text",
    ]

    exit_code, stdout, stderr = await run_command(
        cmd,
        input_text=task_text,
        on_output=on_output,
        env_overrides={"CLAUDECODE": None},
        timeout=CLAUDE_NORM_FIX_TIMEOUT,
    )

    return exit_code, stdout
