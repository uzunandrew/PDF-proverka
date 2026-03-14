"""
Сервис для работы с замечаниями аудита.
Чтение, фильтрация, сводка из 03_findings.json.
"""
import json
from pathlib import Path
from typing import Optional

from webapp.config import SEVERITY_CONFIG
from webapp.models.findings import FindingsResponse, FindingsSummary
from webapp.services.project_service import resolve_project_dir


def _get_findings_path(project_id: str) -> Path:
    """Выбрать лучший файл замечаний: 03a (верифицированный) или 03 (базовый)."""
    output_dir = resolve_project_dir(project_id) / "_output"
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
    from webapp.services.project_service import iter_project_dirs
    summaries = []
    for project_id, entry in iter_project_dirs():
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
            project_id=project_id,
            total=len(items),
            by_severity=by_severity,
            audit_date=data.get("audit_date", data.get("generated_at")),
        ))

    return summaries


def get_finding_block_map(project_id: str) -> Optional[dict]:
    """Маппинг finding_id → [block_ids] через совпадение страниц."""
    import re

    findings_path = _get_findings_path(project_id)
    findings_data = _load_json(findings_path)
    if findings_data is None:
        return None

    blocks_by_page, block_info, all_block_ids = _load_blocks_data(project_id)
    block_id_re = re.compile(r'\b([A-Z0-9]{3,5}-[A-Z0-9]{3,5}-[A-Z0-9]{2,4})\b')

    items = findings_data.get("findings", findings_data.get("items", []))
    result: dict[str, list[str]] = {}

    for f in items:
        fid = f.get("id", "")
        if not fid:
            continue

        matched_blocks: list[str] = []
        seen: set[str] = set()

        # 1. evidence array (наивысший приоритет — точная трассировка)
        evidence = f.get("evidence")
        if evidence and isinstance(evidence, list):
            for ev in evidence:
                bid = ev.get("block_id", "")
                if ev.get("type") == "image" and bid in all_block_ids and bid not in seen:
                    matched_blocks.append(bid)
                    seen.add(bid)

        # 2. related_block_ids (fallback от evidence)
        if not matched_blocks:
            related = f.get("related_block_ids")
            if related and isinstance(related, list):
                for bid in related:
                    if bid in all_block_ids and bid not in seen:
                        matched_blocks.append(bid)
                        seen.add(bid)

        # 2. Явные block_id в description (fallback)
        if not matched_blocks:
            desc = f.get("description", "")
            for m in block_id_re.finditer(desc):
                bid = m.group(1)
                if bid in all_block_ids and bid not in seen:
                    matched_blocks.append(bid)
                    seen.add(bid)

        # 3. По страницам из sheet (последний fallback)
        if not matched_blocks:
            pages = _parse_pages_from_text(f.get("sheet", ""))
            for page in sorted(pages):
                for bid in blocks_by_page.get(page, []):
                    if bid not in seen:
                        matched_blocks.append(bid)
                        seen.add(bid)

        if matched_blocks:
            result[fid] = matched_blocks

    return {
        "project_id": project_id,
        "block_map": result,
        "block_info": block_info,
    }


def _load_blocks_data(project_id: str) -> tuple[dict, dict, set]:
    """Загрузить блоки: blocks_by_page, block_info, all_block_ids."""
    import re

    blocks_by_page: dict[int, list[str]] = {}
    all_block_ids: set[str] = set()
    block_info: dict[str, dict] = {}

    blocks_path = resolve_project_dir(project_id) / "_output" / "02_blocks_analysis.json"
    blocks_data = _load_json(blocks_path)
    if blocks_data:
        for block in blocks_data.get("blocks", []):
            bid = block.get("block_id", "")
            page = block.get("page")
            if bid and page is not None:
                all_block_ids.add(bid)
                blocks_by_page.setdefault(page, []).append(bid)

    index_path = resolve_project_dir(project_id) / "_output" / "blocks" / "index.json"
    index_data = _load_json(index_path)
    if index_data:
        for b in index_data.get("blocks", []):
            bid = b.get("block_id", "")
            if bid:
                block_info[bid] = {
                    "block_id": bid,
                    "page": b.get("page"),
                    "ocr_label": b.get("ocr_label", ""),
                }
                page = b.get("page")
                if page is not None:
                    all_block_ids.add(bid)
                    if bid not in blocks_by_page.get(page, []):
                        blocks_by_page.setdefault(page, []).append(bid)

    return blocks_by_page, block_info, all_block_ids


def _parse_pages_from_text(text: str) -> set[int]:
    """Извлечь номера страниц/листов из строки.

    Поддерживает: 'стр. 8-20', 'листы 19-27', 'лист 23', 'листы 6-7, 21'.
    """
    import re
    pages: set[int] = set()
    # Ищем "стр." или "лист(ы)" с числами
    pattern = re.compile(r'(?:стр\.|листы?)\s*([\d,\s\-–]+)', re.IGNORECASE)
    for m in pattern.finditer(text):
        pages_str = m.group(1)
        for part in re.split(r'[,;]\s*', pages_str):
            part = part.strip().replace('–', '-')
            if '-' in part:
                bounds = part.split('-')
                try:
                    start, end = int(bounds[0].strip()), int(bounds[-1].strip())
                    pages.update(range(start, end + 1))
                except ValueError:
                    pass
            else:
                try:
                    pages.add(int(part))
                except ValueError:
                    pass
    return pages


def get_optimization_block_map(project_id: str) -> Optional[dict]:
    """Маппинг optimization_id → [block_ids] через совпадение листов/страниц."""
    import re

    opt_path = resolve_project_dir(project_id) / "_output" / "optimization.json"
    opt_data = _load_json(opt_path)
    if opt_data is None:
        return None

    blocks_by_page, block_info, all_block_ids = _load_blocks_data(project_id)

    # Паттерн для block_id в тексте
    block_id_re = re.compile(r'\b([A-Z0-9]{3,5}-[A-Z0-9]{3,5}-[A-Z0-9]{2,4})\b')

    items = opt_data.get("items", [])
    result: dict[str, list[str]] = {}

    for item in items:
        oid = item.get("id", "")
        if not oid:
            continue

        matched_blocks: list[str] = []
        seen: set[str] = set()

        # 1. Явные block_id в текстовых полях
        for field in ("current", "proposed", "risks"):
            text = item.get(field, "")
            for m in block_id_re.finditer(text):
                bid = m.group(1)
                if bid in all_block_ids and bid not in seen:
                    matched_blocks.append(bid)
                    seen.add(bid)

        # 2. По листам/страницам из section
        section = item.get("section", "")
        pages = _parse_pages_from_text(section)

        # Пробуем прямое совпадение (листы = страницы PDF)
        # и со смещением +3 (типичный offset: лист 1 = стр. 4)
        for page in sorted(pages):
            for offset in (0, 3):
                p = page + offset
                for bid in blocks_by_page.get(p, []):
                    if bid not in seen:
                        matched_blocks.append(bid)
                        seen.add(bid)

        if matched_blocks:
            result[oid] = matched_blocks

    return {
        "project_id": project_id,
        "block_map": result,
        "block_info": block_info,
    }


def _load_json(path: Path) -> Optional[dict]:
    """Безопасное чтение JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
