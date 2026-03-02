"""
REST API для проектов.
"""
from fastapi import APIRouter, HTTPException
from webapp.services import project_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects():
    """Список всех проектов с их статусом."""
    projects = project_service.list_projects()
    return {"projects": [p.model_dump() for p in projects]}


@router.get("/{project_id}")
async def get_project(project_id: str):
    """Детали одного проекта."""
    status = project_service.get_project_status(project_id)
    if not status:
        raise HTTPException(404, f"Проект '{project_id}' не найден")
    return status.model_dump()


@router.get("/{project_id}/config")
async def get_project_config(project_id: str):
    """Сырой project_info.json."""
    info = project_service.get_project_info(project_id)
    if not info:
        raise HTTPException(404, f"project_info.json не найден для '{project_id}'")
    return info
