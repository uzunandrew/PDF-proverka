"""
generate_tile_batches.py
------------------------
Разбивает тайлы проекта на пакеты для пакетного анализа Claude.

Принцип: одна страница чертежа ВСЕГДА целиком в одном пакете.
Пакет ~10 тайлов (маленькие страницы объединяются, большие — по одной).

Использование:
  python generate_tile_batches.py projects/133-23-GK-EM1
  python generate_tile_batches.py projects/133-23-GK-EM1 --batch-size 8
  python generate_tile_batches.py                         # все проекты
"""

import os
import sys
import json
import glob
import argparse

BASE_DIR = r"D:\Отедел Системного Анализа\1. Calude code"
DEFAULT_BATCH_SIZE = 10


def find_project_tiles(project_path):
    """Находит все тайлы проекта, сгруппированные по страницам.

    Возвращает: [(page_num, [tile_info, ...]), ...] — отсортировано по page_num.
    """
    tiles_dir = os.path.join(project_path, "_output", "tiles")
    if not os.path.isdir(tiles_dir):
        return []

    pages = []
    # Ищем подпапки page_XX с index.json
    for entry in sorted(os.listdir(tiles_dir)):
        page_dir = os.path.join(tiles_dir, entry)
        index_path = os.path.join(page_dir, "index.json")
        if not os.path.isdir(page_dir) or not os.path.isfile(index_path):
            continue

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        page_num = index.get("page", 0)
        label = index.get("label", entry)
        grid = index.get("grid", "?x?")
        tiles = []

        for tile in index.get("tiles", []):
            tiles.append({
                "page": page_num,
                "file": f"{entry}/{tile['file']}",
                "row": tile.get("row", 0),
                "col": tile.get("col", 0),
                "size_kb": tile.get("size_kb", 0),
            })

        if tiles:
            pages.append({
                "page": page_num,
                "label": label,
                "grid": grid,
                "tile_count": len(tiles),
                "tiles": tiles,
            })

    pages.sort(key=lambda p: p["page"])
    return pages


def split_page_by_rows(page_info, batch_size):
    """Разбивает большую страницу на группы по рядам.

    Если страница содержит > batch_size тайлов (например 5x7 = 35),
    разбиваем по рядам так, чтобы каждая группа <= batch_size.
    """
    tiles = page_info["tiles"]
    page_num = page_info["page"]

    if len(tiles) <= batch_size:
        return [tiles]

    # Группируем тайлы по рядам
    rows_dict = {}
    for tile in tiles:
        row = tile.get("row", 0)
        if row not in rows_dict:
            rows_dict[row] = []
        rows_dict[row].append(tile)

    # Собираем группы из рядов, не превышая batch_size
    groups = []
    current_group = []

    for row_num in sorted(rows_dict.keys()):
        row_tiles = rows_dict[row_num]

        if current_group and (len(current_group) + len(row_tiles)) > batch_size:
            groups.append(current_group)
            current_group = []

        current_group.extend(row_tiles)

        if len(current_group) >= batch_size:
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)

    return groups


def generate_batches(pages, batch_size=DEFAULT_BATCH_SIZE):
    """Формирует пакеты из страниц.

    Правила:
    - Маленькие страницы (≤ batch_size) идут целиком, объединяются до ~batch_size
    - Большие страницы (> batch_size) разбиваются по рядам
    - Каждый ряд — логическая единица на чертеже (горизонтальная полоса)
    """
    batches = []
    current_batch_tiles = []
    current_batch_pages = []

    for page_info in pages:
        page_tiles = page_info["tiles"]
        page_num = page_info["page"]

        # Большая страница: разбиваем по рядам
        if len(page_tiles) > batch_size:
            # Сначала закрываем текущий пакет если он не пуст
            if current_batch_tiles:
                batches.append({
                    "batch_id": len(batches) + 1,
                    "tiles": current_batch_tiles,
                    "pages_included": current_batch_pages,
                    "tile_count": len(current_batch_tiles),
                })
                current_batch_tiles = []
                current_batch_pages = []

            # Разбиваем страницу на группы по рядам
            groups = split_page_by_rows(page_info, batch_size)
            for group in groups:
                batches.append({
                    "batch_id": len(batches) + 1,
                    "tiles": group,
                    "pages_included": [page_num],
                    "tile_count": len(group),
                })
            continue

        # Маленькая страница: объединяем с другими
        # Если текущий пакет + эта страница > batch_size — закрываем текущий
        if current_batch_tiles and (len(current_batch_tiles) + len(page_tiles)) > batch_size:
            batches.append({
                "batch_id": len(batches) + 1,
                "tiles": current_batch_tiles,
                "pages_included": current_batch_pages,
                "tile_count": len(current_batch_tiles),
            })
            current_batch_tiles = []
            current_batch_pages = []

        # Добавляем страницу в текущий пакет
        current_batch_tiles.extend(page_tiles)
        current_batch_pages.append(page_num)

        # Если набрали >= batch_size — закрываем
        if len(current_batch_tiles) >= batch_size:
            batches.append({
                "batch_id": len(batches) + 1,
                "tiles": current_batch_tiles,
                "pages_included": current_batch_pages,
                "tile_count": len(current_batch_tiles),
            })
            current_batch_tiles = []
            current_batch_pages = []

    # Остаток
    if current_batch_tiles:
        batches.append({
            "batch_id": len(batches) + 1,
            "tiles": current_batch_tiles,
            "pages_included": current_batch_pages,
            "tile_count": len(current_batch_tiles),
        })

    return batches


def process_project(project_path, batch_size=DEFAULT_BATCH_SIZE):
    """Генерирует tile_batches.json для одного проекта."""
    # Читаем project_info.json
    info_path = os.path.join(project_path, "project_info.json")
    if not os.path.isfile(info_path):
        print(f"  [SKIP] Нет project_info.json: {project_path}")
        return None

    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    project_id = info.get("project_id", os.path.basename(project_path))

    # Находим тайлы
    pages = find_project_tiles(project_path)
    if not pages:
        print(f"  [SKIP] Нет тайлов: {project_path}")
        return None

    total_tiles = sum(p["tile_count"] for p in pages)
    total_pages = len(pages)

    # Формируем пакеты
    batches = generate_batches(pages, batch_size)

    result = {
        "project_id": project_id,
        "project_path": project_path,
        "total_tiles": total_tiles,
        "total_pages": total_pages,
        "batch_size_target": batch_size,
        "total_batches": len(batches),
        "tile_config_source": info.get("tile_config_source", "unknown"),
        "pages_summary": [
            {
                "page": p["page"],
                "label": p["label"],
                "grid": p["grid"],
                "tile_count": p["tile_count"],
            }
            for p in pages
        ],
        "batches": batches,
    }

    # Сохраняем
    out_path = os.path.join(project_path, "_output", "tile_batches.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  Проект: {project_id}")
    print(f"  Страниц: {total_pages}, тайлов: {total_tiles}")
    print(f"  Пакетов: {len(batches)} (целевой размер: {batch_size})")
    for b in batches:
        pages_str = ", ".join(str(p) for p in b["pages_included"])
        print(f"    Пакет {b['batch_id']:3d}: {b['tile_count']:3d} тайлов  (стр. {pages_str})")
    print(f"  Сохранено: {out_path}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Generate tile batches for batch Claude analysis")
    parser.add_argument("project", nargs="?", default=None,
                        help="Project folder (e.g. projects/133-23-GK-EM1)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Target tiles per batch (default: {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args()

    if args.project:
        # Один проект
        project_path = args.project
        if not os.path.isabs(project_path):
            project_path = os.path.join(BASE_DIR, project_path)
        process_project(project_path, args.batch_size)
    else:
        # Все проекты
        projects_dir = os.path.join(BASE_DIR, "projects")
        if not os.path.isdir(projects_dir):
            print(f"[ERROR] Папка проектов не найдена: {projects_dir}")
            sys.exit(1)

        count = 0
        for entry in sorted(os.listdir(projects_dir)):
            proj_path = os.path.join(projects_dir, entry)
            info_path = os.path.join(proj_path, "project_info.json")
            if os.path.isdir(proj_path) and os.path.isfile(info_path):
                print(f"\n{'='*60}")
                result = process_project(proj_path, args.batch_size)
                if result:
                    count += 1

        print(f"\n{'='*60}")
        print(f"Обработано проектов: {count}")


if __name__ == "__main__":
    main()
