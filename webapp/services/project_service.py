"""
Сервис для работы с проектами.
Сканирование, чтение project_info.json, определение статуса конвейера.
"""
import json
import os
from pathlib import Path
from typing import Optional
from datetime import datetime

from webapp.config import PROJECTS_DIR, SEVERITY_CONFIG
from webapp.models.project import (
    ProjectInfo, ProjectStatus, PipelineStatus, TextExtractionQuality,
)


def list_projects() -> list[ProjectStatus]:
    """Получить список всех проектов с их статусом."""
    projects = []
    if not PROJECTS_DIR.exists():
        return projects

    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        info_path = entry / "project_info.json"
        if not info_path.exists():
            # Проект без конфигурации — показываем как неподготовленный
            pdf_files = list(entry.glob("*.pdf"))
            if not pdf_files:
                continue  # Пустая папка — пропускаем
            projects.append(ProjectStatus(
                project_id=entry.name,
                name=entry.name,
                description="(не подготовлен — нет project_info.json)",
                has_pdf=True,
                pdf_size_mb=round(pdf_files[0].stat().st_size / 1024 / 1024, 1),
            ))
            continue

        status = get_project_status(entry.name)
        if status:
            projects.append(status)

    return projects


def get_project_status(project_id: str) -> Optional[ProjectStatus]:
    """Получить полный статус одного проекта."""
    proj_dir = PROJECTS_DIR / project_id
    if not proj_dir.exists():
        return None

    info_path = proj_dir / "project_info.json"
    if not info_path.exists():
        return None

    info = _load_json(info_path)
    if not info:
        return None

    output_dir = proj_dir / "_output"
    pdf_file = info.get("pdf_file", "document.pdf")
    pdf_path = proj_dir / pdf_file

    # Проверяем наличие файлов
    has_pdf = pdf_path.exists()
    pdf_size_mb = round(pdf_path.stat().st_size / 1024 / 1024, 1) if has_pdf else 0.0

    text_path = output_dir / "extracted_text.txt"
    has_text = text_path.exists() and text_path.stat().st_size > 0
    text_size_kb = round(text_path.stat().st_size / 1024, 1) if has_text else 0.0

    # MD-файл (структурированный текст из внешнего OCR)
    md_file_name = info.get("md_file")
    has_md = False
    md_size_kb = 0.0
    if md_file_name:
        md_path = proj_dir / md_file_name
        if md_path.exists() and md_path.stat().st_size > 0:
            has_md = True
            md_size_kb = round(md_path.stat().st_size / 1024, 1)
    # Определяем основной текстовый источник
    if has_md:
        text_source = "md"
    elif has_text:
        text_source = "extracted_text"
    else:
        text_source = "none"

    # Тайлы
    tiles_dir = output_dir / "tiles"
    tile_count = 0
    tile_pages = 0
    if tiles_dir.exists():
        page_dirs = [d for d in tiles_dir.iterdir() if d.is_dir() and d.name.startswith("page_")]
        tile_pages = len(page_dirs)
        for pd in page_dirs:
            tile_count += len(list(pd.glob("*.png")))

    # Pipeline status
    pipeline = _get_pipeline_status(output_dir)

    # Замечания
    findings_count = 0
    findings_by_severity = {}
    audit_date = None
    findings_path = output_dir / "03_findings.json"
    if findings_path.exists():
        fdata = _load_json(findings_path)
        if fdata:
            items = fdata.get("findings", fdata.get("items", []))
            findings_count = len(items)
            for item in items:
                sev = item.get("severity", "НЕИЗВЕСТНО")
                findings_by_severity[sev] = findings_by_severity.get(sev, 0) + 1
            audit_date = fdata.get("audit_date", fdata.get("generated_at"))

    # Пакеты тайлов
    total_batches = 0
    completed_batches = 0
    batches_path = output_dir / "tile_batches.json"
    if batches_path.exists():
        bdata = _load_json(batches_path)
        if bdata:
            total_batches = bdata.get("total_batches", len(bdata.get("batches", [])))
            # Считаем завершённые
            for i in range(1, total_batches + 1):
                batch_file = output_dir / f"tile_batch_{i:03d}.json"
                if batch_file.exists() and batch_file.stat().st_size > 100:
                    completed_batches += 1

    return ProjectStatus(
        project_id=project_id,
        name=info.get("name", project_id),
        description=info.get("description", ""),
        section=info.get("section", "EM"),
        object=info.get("object"),
        has_pdf=has_pdf,
        pdf_size_mb=pdf_size_mb,
        has_extracted_text=has_text,
        text_size_kb=text_size_kb,
        has_md_file=has_md,
        md_file_name=md_file_name if has_md else None,
        md_file_size_kb=md_size_kb,
        text_source=text_source,
        has_tiles=tile_count > 0,
        tile_count=tile_count,
        tile_pages=tile_pages,
        pipeline=pipeline,
        findings_count=findings_count,
        findings_by_severity=findings_by_severity,
        last_audit_date=audit_date,
        total_batches=total_batches,
        completed_batches=completed_batches,
    )


def get_project_info(project_id: str) -> Optional[dict]:
    """Прочитать raw project_info.json."""
    path = PROJECTS_DIR / project_id / "project_info.json"
    return _load_json(path)


def save_project_info(project_id: str, data: dict) -> bool:
    """Сохранить project_info.json."""
    path = PROJECTS_DIR / project_id / "project_info.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_tile_pages(project_id: str) -> list[dict]:
    """Получить список страниц с тайлами."""
    tiles_dir = PROJECTS_DIR / project_id / "_output" / "tiles"
    if not tiles_dir.exists():
        return []

    pages = []
    for page_dir in sorted(tiles_dir.iterdir()):
        if not page_dir.is_dir() or not page_dir.name.startswith("page_"):
            continue

        page_num = page_dir.name.replace("page_", "")
        tiles = sorted([f.name for f in page_dir.glob("*.png")])

        # Определяем размер сетки из имён файлов
        rows = set()
        cols = set()
        for t in tiles:
            # page_07_r1c2.png → r=1, c=2
            parts = t.replace(".png", "").split("_")
            for p in parts:
                if p.startswith("r") and "c" in p:
                    rc = p.split("c")
                    rows.add(int(rc[0][1:]))
                    cols.add(int(rc[1]))

        # Попробуем прочитать index.json
        index_path = page_dir / "index.json"
        index_data = _load_json(index_path) if index_path.exists() else None

        pages.append({
            "page_num": page_num,
            "tile_count": len(tiles),
            "rows": max(rows) if rows else 0,
            "cols": max(cols) if cols else 0,
            "tiles": tiles,
            "index": index_data,
        })

    return pages


def get_tile_path(project_id: str, page_num: str, row: int, col: int) -> Optional[Path]:
    """Получить путь к PNG-файлу тайла."""
    page_dir = PROJECTS_DIR / project_id / "_output" / "tiles" / f"page_{page_num}"
    tile_file = page_dir / f"page_{page_num}_r{row}c{col}.png"
    if tile_file.exists():
        return tile_file
    return None


def _get_pipeline_status(output_dir: Path) -> PipelineStatus:
    """Определить статус конвейера по наличию файлов."""
    status = PipelineStatus()

    if (output_dir / "00_init.json").exists():
        status.init = "done"

    if (output_dir / "01_text_analysis.json").exists():
        status.text_analysis = "done"

    if (output_dir / "02_tiles_analysis.json").exists():
        status.tiles_analysis = "done"
    elif list(output_dir.glob("tile_batch_*.json")):
        # Есть промежуточные файлы — анализ частично выполнен
        status.tiles_analysis = "partial"

    if (output_dir / "03_findings.json").exists():
        status.findings = "done"

    # Верификация нормативных ссылок
    if (output_dir / "03a_norms_verified.json").exists():
        status.norms_verified = "done"
    elif (output_dir / "norm_checks.json").exists():
        status.norms_verified = "partial"

    return status


def _load_json(path: Path) -> Optional[dict]:
    """Безопасное чтение JSON-файла."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
