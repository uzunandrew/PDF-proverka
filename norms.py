#!/usr/bin/env python3
"""
Нормативная база: извлечение, верификация, обновление кеша норм.

Использование:
    python norms.py verify projects/<name>                # извлечь нормы из findings
    python norms.py verify projects/<name> --extract-only # только извлечь, без Claude

    python norms.py update projects/<name>    # обновить базу из одного проекта
    python norms.py update --all              # обновить из всех проектов
    python norms.py update --stats            # статистика базы
    python norms.py update --stale            # устаревшие нормы
"""
import json
import re
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).parent
NORMS_DB_PATH = BASE_DIR / "norms_db.json"
NORMS_PARAGRAPHS_PATH = BASE_DIR / "norms_paragraphs.json"
PROJECTS_DIR = BASE_DIR / "projects"


def _iter_project_dirs_pathlib(root: Path) -> list[Path]:
    """Рекурсивно найти все папки проектов (pathlib-версия)."""
    results = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if (entry / "project_info.json").exists() or list(entry.glob("*.pdf")):
            results.append(entry)
        else:
            for sub in sorted(entry.iterdir()):
                if sub.is_dir() and not sub.name.startswith("_"):
                    results.append(sub)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# VERIFY — извлечение нормативных ссылок из findings
# ═══════════════════════════════════════════════════════════════════════════════

NORM_PATTERNS = [
    r'СП\s+[\d\.]+\.\d{7}\.\d{4}',
    r'СП\s+\d+\.\d+\.\d{4}',
    r'ГОСТ\s+(?:Р\s+)?(?:IEC\s+)?(?:МЭК\s+)?[\d\.\-]+(?:\-\d{4})?',
    r'ПУЭ[\s\-]*[67]?',
    r'СНиП\s+[\d\.\-\*]+',
    r'ВСН\s+[\d\-]+',
    r'ФЗ[\s\-]*\d+',
    r'ПП\s+РФ\s+[№]?\s*\d+',
    r'СО\s+[\d\.\-]+',
]

NORM_REGEX = re.compile('|'.join(f'({p})' for p in NORM_PATTERNS), re.IGNORECASE)


def extract_norms_from_text(text: str) -> list[str]:
    """Извлечь нормативные ссылки из текста."""
    matches = NORM_REGEX.findall(text)
    norms = set()
    for match_tuple in matches:
        for m in match_tuple:
            if m.strip():
                norms.add(m.strip())
    return sorted(norms)


def extract_norms_from_findings(findings_path: Path) -> dict:
    """Прочитать 03_findings.json, извлечь все нормативные ссылки."""
    with open(findings_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    findings = data.get("findings", [])
    norms_map = {}

    for finding in findings:
        fid = finding.get("id", "?")
        norm_field = finding.get("norm") or ""
        problem_field = finding.get("finding") or finding.get("problem") or ""
        recommendation = finding.get("recommendation") or finding.get("solution") or ""

        found_norms = extract_norms_from_text(norm_field)
        found_norms += extract_norms_from_text(problem_field)
        found_norms += extract_norms_from_text(recommendation)

        for norm in found_norms:
            key = re.sub(r'\s+', ' ', norm).strip()

            if key not in norms_map:
                norms_map[key] = {
                    "cited_as": [],
                    "affected_findings": [],
                    "contexts": [],
                    "low_confidence_findings": [],
                }

            if norm_field and norm_field not in norms_map[key]["cited_as"]:
                norms_map[key]["cited_as"].append(norm_field)
            if fid not in norms_map[key]["affected_findings"]:
                norms_map[key]["affected_findings"].append(fid)

            ctx = problem_field[:200] if problem_field else ""
            if ctx and ctx not in norms_map[key]["contexts"]:
                norms_map[key]["contexts"].append(ctx)

            confidence = finding.get("norm_confidence")
            if confidence is not None and confidence < 0.8:
                if fid not in norms_map[key]["low_confidence_findings"]:
                    norms_map[key]["low_confidence_findings"].append(fid)

    return {
        "norms": norms_map,
        "total_findings": len(findings),
        "total_unique_norms": len(norms_map),
    }


def format_norms_for_template(norms_data: dict) -> str:
    """Форматировать список норм для подстановки в шаблон Claude."""
    lines = []
    for i, (norm, info) in enumerate(norms_data["norms"].items(), 1):
        findings_str = ", ".join(info["affected_findings"])
        cited = info["cited_as"][0] if info["cited_as"] else norm
        entry = (
            f"{i}. **{norm}**\n"
            f"   - Как указано в проекте: `{cited}`\n"
            f"   - Затронутые замечания: {findings_str}"
        )
        low_conf = info.get("low_confidence_findings", [])
        if low_conf:
            entry += f"\n   - Требуют проверки цитат: {', '.join(low_conf)}"
        lines.append(entry)
    return "\n".join(lines)


def format_findings_to_fix(norm_checks_path: Path, findings_path: Path) -> str:
    """Определить какие замечания нужно пересмотреть после верификации норм."""
    with open(norm_checks_path, "r", encoding="utf-8") as f:
        checks = json.load(f)
    with open(findings_path, "r", encoding="utf-8") as f:
        findings_data = json.load(f)

    findings_map = {f["id"]: f for f in findings_data.get("findings", [])}
    lines = []

    revision_fids = set()
    for check in checks.get("checks", []):
        if not check.get("needs_revision", False):
            continue
        for fid in check.get("affected_findings", []):
            finding = findings_map.get(fid)
            if not finding:
                continue
            revision_fids.add(fid)
            lines.append(
                f"### {fid}\n"
                f"- **Текущая норма:** `{finding.get('norm', '?')}`\n"
                f"- **Проблема:** {check.get('status', '?')} — {check.get('details', '')}\n"
                f"- **Актуальный документ:** `{check.get('current_version', '?')}`\n"
                f"- **Замена:** `{check.get('replacement_doc') or 'нет'}`\n"
            )

    for pc in checks.get("paragraph_checks", []):
        if pc.get("paragraph_verified", True):
            continue
        fid = pc.get("finding_id", "")
        if fid in revision_fids:
            continue
        finding = findings_map.get(fid)
        if not finding:
            continue
        revision_fids.add(fid)
        lines.append(
            f"### {fid}\n"
            f"- **Текущая норма:** `{finding.get('norm', '?')}`\n"
            f"- **Проблема:** Цитата пункта не подтверждена\n"
            f"- **Заявленная цитата:** `{pc.get('claimed_quote', '?')}`\n"
            f"- **Реальный текст:** `{pc.get('actual_quote') or 'не найден'}`\n"
            f"- **Расхождение:** {pc.get('mismatch_details', '?')}\n"
        )

    if not lines:
        return "Все нормы актуальны. Пересмотр не требуется."
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# UPDATE — обновление централизованной базы норм
# ═══════════════════════════════════════════════════════════════════════════════

def load_norms_db() -> dict:
    """Загрузить базу норм."""
    if not NORMS_DB_PATH.exists():
        return {
            "meta": {
                "description": "Централизованная база нормативных документов с автообновлением",
                "last_updated": datetime.now().isoformat(),
                "total_norms": 0,
                "stale_after_days": 30,
                "update_history": [],
            },
            "norms": {},
            "replacements": {},
        }
    with open(NORMS_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_norms_db(db: dict):
    """Сохранить базу норм."""
    db["meta"]["total_norms"] = len(db["norms"])
    db["meta"]["last_updated"] = datetime.now().isoformat()
    with open(NORMS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def load_norms_paragraphs() -> dict:
    """Загрузить справочник проверенных параграфов."""
    if not NORMS_PARAGRAPHS_PATH.exists():
        return {
            "meta": {
                "description": "Проверенные цитаты конкретных пунктов нормативных документов",
                "last_updated": None,
                "total_paragraphs": 0,
            },
            "paragraphs": {},
        }
    with open(NORMS_PARAGRAPHS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_norms_paragraphs(pdb: dict):
    """Сохранить справочник параграфов."""
    pdb["meta"]["total_paragraphs"] = len(pdb["paragraphs"])
    pdb["meta"]["last_updated"] = datetime.now().isoformat()
    with open(NORMS_PARAGRAPHS_PATH, "w", encoding="utf-8") as f:
        json.dump(pdb, f, ensure_ascii=False, indent=2)


def update_paragraphs_from_project(pdb: dict, project_path: Path) -> int:
    """Обновить справочник параграфов из paragraph_checks."""
    norm_checks_path = project_path / "_output" / "norm_checks.json"
    if not norm_checks_path.exists():
        return 0

    with open(norm_checks_path, "r", encoding="utf-8") as f:
        checks_data = json.load(f)

    paragraph_checks = checks_data.get("paragraph_checks", [])
    if not paragraph_checks:
        return 0

    count = 0
    for pc in paragraph_checks:
        if not pc.get("paragraph_verified"):
            continue
        norm = pc.get("norm", "")
        actual_quote = pc.get("actual_quote")
        if not norm or not actual_quote:
            continue

        key = norm.strip()
        existing = pdb["paragraphs"].get(key)
        if existing and existing.get("quote") == actual_quote:
            continue

        pdb["paragraphs"][key] = {
            "norm": key,
            "quote": actual_quote,
            "verified_at": datetime.now().isoformat(),
            "source_project": project_path.name,
        }
        count += 1

    return count


def _guess_category(doc_number: str) -> str:
    """Определить категорию по номеру документа."""
    dn = doc_number.upper()
    if dn.startswith("ФЗ"):
        return "federal_law"
    if dn.startswith("ПП РФ"):
        return "government_decree"
    if dn.startswith("ПУЭ"):
        return "pue"
    if "13130" in dn or "1311500" in dn:
        return "sp_fire"
    if dn.startswith("СП"):
        return "sp"
    if dn.startswith("ГОСТ"):
        return "gost"
    if dn.startswith("СО ") or dn.startswith("ВСН"):
        return "other"
    return "other"


def normalize_doc_number(raw: str) -> str:
    """Нормализовать номер документа для использования как ключ."""
    doc = raw.strip().replace("**", "")
    return doc


def merge_norm_check(db: dict, check: dict, project_id: str) -> str:
    """Слить одну проверку нормы в базу."""
    doc_number = normalize_doc_number(check.get("doc_number", ""))
    if not doc_number:
        return "skipped"

    status = check.get("status", "not_found")
    if status == "not_found":
        return "skipped"

    now = datetime.now().isoformat()

    status_map = {
        "active": "active",
        "outdated_edition": "active",
        "replaced": "replaced",
        "cancelled": "cancelled",
    }
    db_status = status_map.get(status, status)

    existing = db.get("norms", {}).get(doc_number)

    if existing:
        changed = False
        if existing.get("status") != db_status:
            existing["status"] = db_status
            changed = True
        new_version = check.get("current_version")
        if new_version and new_version != existing.get("current_version"):
            existing["current_version"] = new_version
            changed = True
        replacement = check.get("replacement_doc")
        if replacement and replacement != existing.get("replacement_doc"):
            existing["replacement_doc"] = replacement
            changed = True
            if "replacements" not in db:
                db["replacements"] = {}
            db["replacements"][doc_number] = replacement
        source_url = check.get("source_url")
        if source_url and source_url != existing.get("source_url"):
            existing["source_url"] = source_url
            changed = True
        details = check.get("details")
        if details:
            existing["notes"] = details
        existing["last_verified"] = now
        existing["verified_by"] = f"websearch:{project_id}"
        return "updated" if changed else "unchanged"
    else:
        new_entry = {
            "doc_number": doc_number,
            "title": check.get("norm_as_cited", doc_number),
            "status": db_status,
            "current_version": check.get("current_version"),
            "replacement_doc": check.get("replacement_doc"),
            "category": _guess_category(doc_number),
            "notes": check.get("details", ""),
            "source_url": check.get("source_url"),
            "last_verified": now,
            "verified_by": f"websearch:{project_id}",
        }
        db["norms"][doc_number] = new_entry
        replacement = check.get("replacement_doc")
        if replacement and db_status in ("replaced", "cancelled"):
            if "replacements" not in db:
                db["replacements"] = {}
            db["replacements"][doc_number] = replacement
        return "added"


def update_from_project(db: dict, project_path: Path) -> dict:
    """Обновить базу из norm_checks.json одного проекта."""
    norm_checks_path = project_path / "_output" / "norm_checks.json"
    if not norm_checks_path.exists():
        return {"error": f"norm_checks.json не найден в {project_path}"}

    with open(norm_checks_path, "r", encoding="utf-8") as f:
        checks_data = json.load(f)

    checks = checks_data.get("checks", [])
    project_id = project_path.name

    stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for check in checks:
        result = merge_norm_check(db, check, project_id)
        stats[result] = stats.get(result, 0) + 1
    return stats


def get_stale_norms(db: dict) -> list:
    """Получить список норм, которые давно не проверялись."""
    stale_days = db.get("meta", {}).get("stale_after_days", 30)
    threshold = datetime.now() - timedelta(days=stale_days)
    stale = []
    for doc_number, norm in db.get("norms", {}).items():
        last_verified = norm.get("last_verified", "")
        if not last_verified:
            stale.append(doc_number)
            continue
        try:
            verified_dt = datetime.fromisoformat(last_verified)
            if verified_dt < threshold:
                stale.append(doc_number)
        except (ValueError, TypeError):
            stale.append(doc_number)
    return stale


def print_stats(db: dict):
    """Вывести статистику базы."""
    norms = db.get("norms", {})
    total = len(norms)

    by_status = {}
    by_category = {}
    for norm in norms.values():
        status = norm.get("status", "unknown")
        category = norm.get("category", "other")
        by_status[status] = by_status.get(status, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1

    stale = get_stale_norms(db)
    replacements = db.get("replacements", {})
    pdb = load_norms_paragraphs()
    total_paragraphs = len(pdb.get("paragraphs", {}))

    print(f"\n{'='*60}")
    print(f"  БАЗА НОРМАТИВНЫХ ДОКУМЕНТОВ — СТАТИСТИКА")
    print(f"{'='*60}")
    print(f"  Всего норм:         {total}")
    print(f"  Проверенных цитат:  {total_paragraphs}")
    print(f"  Таблица замен:      {len(replacements)} записей")
    print(f"  Устаревших (>{db['meta'].get('stale_after_days', 30)} дн): {len(stale)}")
    print(f"  Последнее обновление: {db['meta'].get('last_updated', 'N/A')}")
    print()
    print("  По статусу:")
    for status, count in sorted(by_status.items()):
        icon = {"active": "+", "replaced": "!", "cancelled": "X", "limited": "~", "voluntary": "?"}.get(status, " ")
        print(f"    [{icon}] {status}: {count}")
    print()
    print("  По категории:")
    for cat, count in sorted(by_category.items()):
        print(f"    {cat}: {count}")

    if stale:
        print(f"\n  Нормы, требующие повторной проверки ({len(stale)}):")
        for doc in stale[:10]:
            norm = norms[doc]
            print(f"    - {doc} (проверена: {norm.get('last_verified', 'никогда')[:10]})")
        if len(stale) > 10:
            print(f"    ... и ещё {len(stale) - 10}")

    print(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — точка входа с подкомандами
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python norms.py verify projects/<name>              # извлечь нормы")
        print("  python norms.py verify projects/<name> --extract-only")
        print("  python norms.py update projects/<name>              # обновить из проекта")
        print("  python norms.py update --all                       # обновить из всех")
        print("  python norms.py update --stats                     # статистика")
        print("  python norms.py update --stale                     # устаревшие")
        sys.exit(1)

    command = sys.argv[1]

    if command == "verify":
        if len(sys.argv) < 3:
            print("Использование: python norms.py verify projects/<name> [--extract-only]")
            sys.exit(1)

        project_dir = Path(sys.argv[2])
        extract_only = "--extract-only" in sys.argv

        if not project_dir.is_absolute():
            project_dir = Path.cwd() / project_dir

        output_dir = project_dir / "_output"
        findings_path = output_dir / "03_findings.json"

        if not findings_path.exists():
            print(f"ОШИБКА: Файл {findings_path} не найден. Сначала выполните аудит (этап 03).")
            sys.exit(1)

        print(f"Извлечение нормативных ссылок из {findings_path.name}...")
        norms_data = extract_norms_from_findings(findings_path)

        print(f"Найдено замечаний: {norms_data['total_findings']}")
        print(f"Уникальных нормативных ссылок: {norms_data['total_unique_norms']}")

        for norm, info in norms_data["norms"].items():
            findings_str = ", ".join(info["affected_findings"])
            print(f"  - {norm} (в замечаниях: {findings_str})")

        norms_extracted_path = output_dir / "norms_extracted.json"
        with open(norms_extracted_path, "w", encoding="utf-8") as f:
            json.dump({
                "project_dir": str(project_dir),
                "extracted_at": datetime.now().isoformat(),
                **norms_data,
            }, f, ensure_ascii=False, indent=2)

        print(f"Сохранено: {norms_extracted_path}")

        if extract_only:
            print("Режим --extract-only: Claude CLI не запускается.")
            return

        norms_list_text = format_norms_for_template(norms_data)
        print(f"\nСписок норм для верификации:\n{norms_list_text}")
        print(f"\nДля запуска верификации через Claude CLI используйте webapp или pipeline.")

    elif command == "update":
        if len(sys.argv) < 3:
            print("Использование: python norms.py update [projects/<name> | --all | --stats | --stale]")
            sys.exit(1)

        db = load_norms_db()
        arg = sys.argv[2]

        if arg == "--stats":
            print_stats(db)
            return

        if arg == "--stale":
            stale = get_stale_norms(db)
            if stale:
                print(f"Нормы, требующие проверки ({len(stale)}):")
                for doc in stale:
                    norm = db["norms"].get(doc, {})
                    print(f"  {doc} — проверена: {norm.get('last_verified', 'никогда')[:10]}")
            else:
                print("Все нормы актуальны.")
            return

        if arg == "--all":
            if not PROJECTS_DIR.is_dir():
                print(f"Папка проектов не найдена: {PROJECTS_DIR}")
                sys.exit(1)

            total_stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
            processed = 0
            pdb = load_norms_paragraphs()
            total_paragraphs = 0

            for project_dir in _iter_project_dirs_pathlib(PROJECTS_DIR):
                norm_checks = project_dir / "_output" / "norm_checks.json"
                if not norm_checks.exists():
                    continue

                stats = update_from_project(db, project_dir)
                if "error" in stats:
                    print(f"  [{project_dir.name}] {stats['error']}")
                    continue

                processed += 1
                for key in total_stats:
                    total_stats[key] += stats.get(key, 0)

                p_count = update_paragraphs_from_project(pdb, project_dir)
                total_paragraphs += p_count

                print(
                    f"  [{project_dir.name}] +"
                    f"{stats['added']} добавлено, "
                    f"{stats['updated']} обновлено, "
                    f"{stats['unchanged']} без изменений, "
                    f"{stats['skipped']} пропущено"
                    + (f", {p_count} параграфов" if p_count else "")
                )

            db["meta"]["update_history"] = db["meta"].get("update_history", [])[-9:]
            db["meta"]["update_history"].append({
                "date": datetime.now().isoformat(),
                "source": "all_projects",
                "projects_processed": processed,
                "stats": total_stats,
            })

            save_norms_db(db)
            if total_paragraphs > 0:
                save_norms_paragraphs(pdb)
            print(f"\nИтого из {processed} проектов: "
                  f"+{total_stats['added']} добавлено, "
                  f"{total_stats['updated']} обновлено, "
                  f"{total_stats['unchanged']} без изменений")
            if total_paragraphs > 0:
                print(f"Параграфов добавлено: {total_paragraphs} (всего в базе: {len(pdb['paragraphs'])})")
            print(f"База сохранена: {NORMS_DB_PATH}")
            return

        # Один проект
        project_path = Path(arg)
        if not project_path.is_absolute():
            project_path = BASE_DIR / project_path

        if not project_path.is_dir():
            print(f"Проект не найден: {project_path}")
            sys.exit(1)

        stats = update_from_project(db, project_path)
        if "error" in stats:
            print(stats["error"])
            sys.exit(1)

        pdb = load_norms_paragraphs()
        p_count = update_paragraphs_from_project(pdb, project_path)
        if p_count > 0:
            save_norms_paragraphs(pdb)

        db["meta"]["update_history"] = db["meta"].get("update_history", [])[-9:]
        db["meta"]["update_history"].append({
            "date": datetime.now().isoformat(),
            "source": project_path.name,
            "stats": stats,
        })

        save_norms_db(db)
        print(
            f"[{project_path.name}] +"
            f"{stats['added']} добавлено, "
            f"{stats['updated']} обновлено, "
            f"{stats['unchanged']} без изменений, "
            f"{stats['skipped']} пропущено"
            + (f", {p_count} параграфов" if p_count else "")
        )
        print(f"База сохранена: {NORMS_DB_PATH}")

    else:
        print(f"Неизвестная команда: {command}")
        print("Доступные команды: verify, update")
        sys.exit(1)


if __name__ == "__main__":
    main()
