"""
merge_tile_results.py
---------------------
Сливает пакетные результаты анализа тайлов (tile_batch_*.json)
в единый файл 02_tiles_analysis.json.

Проверяет покрытие: все ли пакеты обработаны.
Перенумерует ID находок (G-001, G-002, ...) без дублей.

Использование:
  python merge_tile_results.py projects/133-23-GK-EM1
  python merge_tile_results.py projects/133-23-GK-EM1 --cleanup
  python merge_tile_results.py                         # все проекты
"""

import os
import sys
import json
import glob
import re
import argparse
from datetime import datetime

BASE_DIR = r"D:\Отедел Системного Анализа\1. Calude code"


def merge_project(project_path, cleanup=False):
    """Сливает tile_batch_*.json в 02_tiles_analysis.json для одного проекта."""
    output_dir = os.path.join(project_path, "_output")

    # Читаем project_info
    info_path = os.path.join(project_path, "project_info.json")
    if not os.path.isfile(info_path):
        print(f"  [SKIP] Нет project_info.json: {project_path}")
        return False

    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    project_id = info.get("project_id", os.path.basename(project_path))

    # Читаем tile_batches.json для проверки покрытия
    batches_path = os.path.join(output_dir, "tile_batches.json")
    expected_batches = 0
    expected_tiles = 0
    if os.path.isfile(batches_path):
        with open(batches_path, "r", encoding="utf-8") as f:
            batches_info = json.load(f)
        expected_batches = batches_info.get("total_batches", 0)
        expected_tiles = batches_info.get("total_tiles", 0)

    # Находим все tile_batch_*.json
    pattern = os.path.join(output_dir, "tile_batch_*.json")
    batch_files = sorted(glob.glob(pattern))

    if not batch_files:
        print(f"  [ERROR] Нет файлов tile_batch_*.json в: {output_dir}")
        return False

    print(f"  Проект: {project_id}")
    print(f"  Найдено пакетных файлов: {len(batch_files)}")
    if expected_batches:
        print(f"  Ожидается пакетов: {expected_batches}")

    # Собираем данные
    all_tiles_reviewed = []
    all_items_verified = []
    all_findings = []
    processed_batch_ids = set()
    errors = []

    for bf in batch_files:
        fname = os.path.basename(bf)
        try:
            with open(bf, "r", encoding="utf-8") as f:
                batch_data = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"  [ERROR] {fname}: невалидный JSON — {e}")
            continue

        batch_id = batch_data.get("batch_id", 0)
        processed_batch_ids.add(batch_id)

        # Собираем tiles_reviewed
        for tile in batch_data.get("tiles_reviewed", []):
            all_tiles_reviewed.append(tile)

        # Собираем items_verified_from_stage_01
        for item in batch_data.get("items_verified_from_stage_01", []):
            all_items_verified.append(item)

        # Собираем preliminary_findings
        for finding in batch_data.get("preliminary_findings", []):
            all_findings.append(finding)

        tile_count = len(batch_data.get("tiles_reviewed", []))
        finding_count = len(batch_data.get("preliminary_findings", []))
        print(f"    Пакет {batch_id:3d}: {tile_count} тайлов, {finding_count} находок")

    # Выводим ошибки
    for err in errors:
        print(err)

    # Проверяем покрытие
    if expected_batches > 0:
        missing = []
        for i in range(1, expected_batches + 1):
            if i not in processed_batch_ids:
                missing.append(i)
        if missing:
            print(f"\n  [WARN] Необработанные пакеты: {missing}")
            print(f"  Покрытие: {len(processed_batch_ids)}/{expected_batches} пакетов")
        else:
            print(f"\n  Покрытие: {len(processed_batch_ids)}/{expected_batches} пакетов (100%)")

    # Перенумеровываем ID находок (G-001, G-002, ...)
    finding_counter = 1
    id_map = {}  # старый ID → новый ID

    for finding in all_findings:
        old_id = finding.get("id", "")
        new_id = f"G-{finding_counter:03d}"
        id_map[old_id] = new_id
        finding["id"] = new_id
        finding_counter += 1

    # Перенумеровываем ID в tiles_reviewed -> findings
    for tile in all_tiles_reviewed:
        for f in tile.get("findings", []):
            old_id = f.get("id", "")
            if old_id in id_map:
                f["id"] = id_map[old_id]
            else:
                new_id = f"G-{finding_counter:03d}"
                id_map[old_id] = new_id
                f["id"] = new_id
                finding_counter += 1

    # Обновляем ссылки в items_verified
    for item in all_items_verified:
        old_fid = item.get("finding_id", "")
        if old_fid in id_map:
            item["finding_id"] = id_map[old_fid]

    # Формируем итоговый JSON
    result = {
        "meta": {
            "tiles_reviewed": len(all_tiles_reviewed),
            "total_tiles_expected": expected_tiles,
            "coverage_pct": round(len(all_tiles_reviewed) / expected_tiles * 100, 1) if expected_tiles > 0 else 0,
            "batches_merged": len(batch_files),
            "findings_count": len(all_findings),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        },
        "tiles_reviewed": all_tiles_reviewed,
        "items_verified_from_stage_01": all_items_verified,
        "preliminary_findings": all_findings,
    }

    # Сохраняем 02_tiles_analysis.json
    out_path = os.path.join(output_dir, "02_tiles_analysis.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  Результат:")
    print(f"    Тайлов проанализировано: {len(all_tiles_reviewed)}")
    print(f"    Находок (G-): {len(all_findings)}")
    print(f"    Верификаций из этапа 01: {len(all_items_verified)}")
    print(f"    Сохранено: {out_path}")

    # Очистка промежуточных файлов
    if cleanup:
        print(f"\n  Очистка промежуточных файлов...")
        for bf in batch_files:
            os.remove(bf)
            print(f"    Удалён: {os.path.basename(bf)}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Merge tile batch results into 02_tiles_analysis.json")
    parser.add_argument("project", nargs="?", default=None,
                        help="Project folder (e.g. projects/133-23-GK-EM1)")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete tile_batch_*.json after merge")
    args = parser.parse_args()

    if args.project:
        # Один проект
        project_path = args.project
        if not os.path.isabs(project_path):
            project_path = os.path.join(BASE_DIR, project_path)
        success = merge_project(project_path, args.cleanup)
        sys.exit(0 if success else 1)
    else:
        # Все проекты
        projects_dir = os.path.join(BASE_DIR, "projects")
        count = 0
        for entry in sorted(os.listdir(projects_dir)):
            proj_path = os.path.join(projects_dir, entry)
            # Проверяем есть ли tile_batch файлы
            batch_pattern = os.path.join(proj_path, "_output", "tile_batch_*.json")
            if glob.glob(batch_pattern):
                print(f"\n{'='*60}")
                if merge_project(proj_path, args.cleanup):
                    count += 1

        print(f"\n{'='*60}")
        print(f"Обработано проектов: {count}")


if __name__ == "__main__":
    main()
