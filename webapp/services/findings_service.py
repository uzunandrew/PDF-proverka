"""
Сервис для работы с замечаниями аудита.
Чтение, фильтрация, сводка из 03_findings.json.
"""
import json
from pathlib import Path
from typing import Optional

from webapp.config import PROJECTS_DIR, SEVERITY_CONFIG
from webapp.models.findings import FindingsResponse, FindingsSummary


def _get_findings_path(project_id: str) -> Path:
    """Выбрать лучший файл замечаний: 03a (верифицированный) или 03 (базовый)."""
    output_dir = PROJECTS_DIR / project_id / "_output"
    verified = output_dir / "03a_norms_verified.json"
    if verified.exists():
        return verified
    return output_dir / "03_findings.json"


def get_findings(
    project_id: str,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    sheet: Optional[str] = None,
    search: Optional[str] = None,
) -> Optional[FindingsResponse]:
    """Получить замечания проекта с фильтрацией."""
    path = _get_findings_path(project_id)
    data = _load_json(path)
    if data is None:
        return None

    items = data.get("findings", data.get("items", []))
    audit_date = data.get("audit_date", data.get("generated_at"))

    # Фильтрация
    filtered = items
    if severity:
        sev_upper = severity.upper()
        filtered = [f for f in filtered if sev_upper in f.get("severity", "").upper()]
    if category:
        cat_lower = category.lower()
        filtered = [f for f in filtered if cat_lower in f.get("category", "").lower()]
    if sheet:
        filtered = [f for f in filtered if sheet in str(f.get("sheet", ""))]
    if search:
        s_lower = search.lower()
        filtered = [
            f for f in filtered
            if s_lower in json.dumps(f, ensure_ascii=False).lower()
        ]

    # Сводка по критичности (по всем, не отфильтрованным)
    by_severity = {}
    for item in items:
        sev = item.get("severity", "НЕИЗВЕСТНО")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    # Сортировка по критичности
    sev_order = {s: cfg["order"] for s, cfg in SEVERITY_CONFIG.items()}
    filtered.sort(key=lambda f: sev_order.get(f.get("severity", ""), 99))

    return FindingsResponse(
        project_id=project_id,
        total=len(items),
        by_severity=by_severity,
        findings=filtered,
        audit_date=audit_date,
    )


def get_finding_by_id(project_id: str, finding_id: str) -> Optional[dict]:
    """Получить одно замечание по ID."""
    path = _get_findings_path(project_id)
    data = _load_json(path)
    if data is None:
        return None

    items = data.get("findings", data.get("items", []))
    for item in items:
        if item.get("id", "") == finding_id:
            return item
    return None


def get_all_summaries() -> list[FindingsSummary]:
    """Сводка замечаний по всем проектам."""
    summaries = []
    if not PROJECTS_DIR.exists():
        return summaries

    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        # Предпочитаем верифицированный файл
        path = entry / "_output" / "03a_norms_verified.json"
        if not path.exists():
            path = entry / "_output" / "03_findings.json"
        data = _load_json(path)
        if data is None:
            continue

        items = data.get("findings", data.get("items", []))
        by_severity = {}
        for item in items:
            sev = item.get("severity", "НЕИЗВЕСТНО")
            by_severity[sev] = by_severity.get(sev, 0) + 1

        summaries.append(FindingsSummary(
            project_id=entry.name,
            total=len(items),
            by_severity=by_severity,
            audit_date=data.get("audit_date", data.get("generated_at")),
        ))

    return summaries


def _load_json(path: Path) -> Optional[dict]:
    """Безопасное чтение JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
