"""
REST API для экспорта отчётов.
"""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from webapp.config import BASE_DIR
from webapp.services import excel_service

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/excel")
async def generate_excel():
    """Генерация Excel-отчёта из всех проектов."""
    success, result = await excel_service.generate_excel()
    if success:
        filename = os.path.basename(result)
        return {"status": "ok", "file": filename, "path": result}
    else:
        raise HTTPException(500, f"Ошибка генерации Excel: {result}")


@router.get("/download/{filename}")
async def download_file(filename: str):
    """Скачать файл отчёта."""
    # Безопасность: только файлы из BASE_DIR
    filepath = BASE_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, f"Файл '{filename}' не найден")
    if not str(filepath).startswith(str(BASE_DIR)):
        raise HTTPException(403, "Доступ запрещён")
    return FileResponse(
        str(filepath),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
