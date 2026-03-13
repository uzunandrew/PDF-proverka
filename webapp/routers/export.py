"""
REST API для экспорта отчётов.
"""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from webapp.config import BASE_DIR
from webapp.services import excel_service
from webapp.services.project_service import resolve_project_dir

router = APIRouter(prefix="/api/export", tags=["export"])


class ExcelSectionRequest(BaseModel):
    section: str
    project_ids: list[str]


@router.post("/excel")
async def generate_excel(report_type: str = "all"):
    """Генерация Excel-отчёта. report_type: findings | optimization | all"""
    if report_type not in ("findings", "optimization", "all"):
        raise HTTPException(400, f"Неверный тип отчёта: {report_type}")
    success, result = await excel_service.generate_excel(report_type=report_type)
    if success:
        filename = os.path.basename(result)
        return {"status": "ok", "file": filename, "path": result}
    else:
        raise HTTPException(500, f"Ошибка генерации Excel: {result}")


@router.post("/excel/section")
async def generate_section_excel(req: ExcelSectionRequest):
    """Генерация Excel-отчёта для одного раздела."""
    project_dirs = []
    for pid in req.project_ids:
        try:
            d = resolve_project_dir(pid)
            project_dirs.append(str(d))
        except Exception:
            continue
    if not project_dirs:
        raise HTTPException(400, "Нет проектов с данными в этом разделе")
    success, result = await excel_service.generate_excel(
        report_type="all",
        project_dirs=project_dirs,
    )
    if success:
        filename = os.path.basename(result)
        return {"status": "ok", "file": filename, "path": result}
    else:
        raise HTTPException(500, f"Ошибка генерации Excel: {result}")


@router.get("/download/{filename}")
async def download_file(filename: str):
    """Скачать файл отчёта."""
    from webapp.config import REPORTS_DIR
    # Ищем в REPORTS_DIR (отчет/), затем в BASE_DIR
    filepath = REPORTS_DIR / filename
    if not filepath.exists():
        filepath = BASE_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, f"Файл '{filename}' не найден")
    if not str(filepath.resolve()).startswith(str(BASE_DIR.resolve())):
        raise HTTPException(403, "Доступ запрещён")
    return FileResponse(
        str(filepath),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
