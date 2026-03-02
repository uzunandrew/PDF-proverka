"""
tile_pdf_pages.py
-----------------
Разрезает страницы PDF на блоки (тайлы) с перекрытием для детального анализа.

Использует адаптивный алгоритм из process_project.py (если доступен)
или встроенную конфигурацию PAGE_CONFIG.

Использование:
  python tile_pdf_pages.py              # нарезать все ключевые листы
  python tile_pdf_pages.py 7            # только страница 7
  python tile_pdf_pages.py 7 9 11       # страницы 7, 9, 11
  python tile_pdf_pages.py --quality high   # адаптивный режим (все чертёжные)
"""

import fitz
import os
import sys
import json
import math
import argparse

PDF_PATH  = r"D:\Отедел Системного Анализа\1. Calude code\project\document.pdf"
OUT_BASE  = r"D:\Отедел Системного Анализа\1. Calude code\project\tiles"

# ─── Адаптивный алгоритм (копия из process_project.py) ──────────────────
AREA_SKIP = 550_000

QUALITY_PROFILES = {
    "draft":    {"target_tile_px": 1800, "max_tile_px": 2500, "scale": 2.5, "overlap_pct": 8},
    "standard": {"target_tile_px": 1500, "max_tile_px": 2000, "scale": 3.0, "overlap_pct": 10},
    "high":     {"target_tile_px": 1200, "max_tile_px": 1600, "scale": 3.0, "overlap_pct": 12},
}

MAX_TILES_PER_PAGE = 42


def compute_adaptive_grid(page_width_pt, page_height_pt, quality="standard"):
    """Вычисляет оптимальную сетку нарезки для LLM (Claude)."""
    area = page_width_pt * page_height_pt
    if area <= AREA_SKIP:
        return None

    profile     = QUALITY_PROFILES.get(quality, QUALITY_PROFILES["standard"])
    scale       = profile["scale"]
    target      = profile["target_tile_px"]
    max_px      = profile["max_tile_px"]
    overlap_pct = profile["overlap_pct"]

    full_w_px = page_width_pt * scale
    full_h_px = page_height_pt * scale

    cols = max(max(1, math.ceil(full_w_px / max_px)), max(1, round(full_w_px / target)))
    rows = max(max(1, math.ceil(full_h_px / max_px)), max(1, round(full_h_px / target)))

    if rows * cols > MAX_TILES_PER_PAGE:
        factor = math.sqrt(rows * cols / MAX_TILES_PER_PAGE)
        cols = max(1, round(cols / factor))
        rows = max(1, round(rows / factor))

    return rows, cols, scale, overlap_pct


# ─── Ручная конфигурация (legacy) ───────────────────────────────────────
# Конфигурация для конкретного проекта 133-23-GK-EM1:
#   (rows, cols, scale, overlap_pct, description)
PAGE_CONFIG = {
    7:  (2, 4, 3.5, 8, "Odnolineinaya schema VRU"),
    8:  (3, 4, 3.0, 8, "Plan parkinga - normalnaya set"),
    9:  (2, 4, 3.5, 8, "Principialnye skhemy schitov"),
    11: (3, 4, 3.0, 8, "Plan parkinga - vse sistemy"),
    13: (2, 3, 3.0, 8, "Plan parkinga - glavnye belly"),
    14: (2, 3, 3.5, 8, "Uzel vvoda + razrezy + tablica"),
}
DEFAULT_CONFIG = (2, 2, 2.5, 5, "Stranica")


def tile_page(doc, page_idx, rows, cols, scale, overlap_pct, label, out_dir):
    """Нарезает одну страницу на rows*cols тайлов с перекрытием."""
    page = doc[page_idx]
    pw = page.rect.width
    ph = page.rect.height

    tw = pw / cols   # базовая ширина тайла
    th = ph / rows   # базовая высота тайла
    ox = tw * overlap_pct / 100  # перекрытие по X
    oy = th * overlap_pct / 100  # перекрытие по Y

    mat = fitz.Matrix(scale, scale)
    page_num = page_idx + 1
    page_dir = os.path.join(out_dir, f"page_{page_num:02d}")
    os.makedirs(page_dir, exist_ok=True)

    index = []  # для индексного файла

    for r in range(rows):
        for c in range(cols):
            # координаты с перекрытием, зажатые в границы страницы
            x0 = max(0,  c * tw - ox)
            y0 = max(0,  r * th - oy)
            x1 = min(pw, (c + 1) * tw + ox)
            y1 = min(ph, (r + 1) * th + oy)

            clip = fitz.Rect(x0, y0, x1, y1)
            pix  = page.get_pixmap(matrix=mat, clip=clip, alpha=False)

            fname = f"page_{page_num:02d}_r{r+1}c{c+1}.png"
            fpath = os.path.join(page_dir, fname)
            pix.save(fpath)
            size_kb = os.path.getsize(fpath) / 1024

            index.append({
                "file": fname,
                "row": r + 1,
                "col": c + 1,
                "x0_pct": round(x0 / pw * 100, 1),
                "y0_pct": round(y0 / ph * 100, 1),
                "x1_pct": round(x1 / pw * 100, 1),
                "y1_pct": round(y1 / ph * 100, 1),
                "size_kb": round(size_kb, 0),
            })
            print(f"    r{r+1}c{c+1}  {fname}  ({size_kb:.0f} KB)")

    # Сохраняем индекс тайлов в JSON
    idx_path = os.path.join(page_dir, "index.json")
    meta = {
        "page": page_num,
        "label": label,
        "grid": f"{rows}x{cols}",
        "scale": scale,
        "overlap_pct": overlap_pct,
        "page_size_pts": [round(pw, 1), round(ph, 1)],
        "tile_size_px": [round(tw * scale), round(th * scale)],
        "tiles": index,
    }
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return len(index)


def main():
    parser = argparse.ArgumentParser(
        description="Tile PDF pages for detailed LLM analysis")
    parser.add_argument("pages", nargs="*", type=int,
                        help="Page numbers to tile (default: all from PAGE_CONFIG)")
    parser.add_argument("--quality", choices=["draft", "standard", "high"],
                        default=None,
                        help="Use adaptive grid (overrides PAGE_CONFIG)")
    parser.add_argument("--pdf", default=PDF_PATH,
                        help="Path to PDF file")
    parser.add_argument("--out", default=OUT_BASE,
                        help="Output directory for tiles")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    doc = fitz.open(args.pdf)
    total_pages = len(doc)

    print(f"PDF: {total_pages} pages")
    print(f"Output: {args.out}")

    if args.quality:
        # Адаптивный режим: определяем сетку автоматически
        print(f"Mode: adaptive (quality={args.quality})")
        pages_to_process = args.pages if args.pages else list(range(1, total_pages + 1))
    else:
        # Legacy режим: используем PAGE_CONFIG
        pages_to_process = args.pages if args.pages else sorted(PAGE_CONFIG.keys())
        print(f"Mode: manual (PAGE_CONFIG)")

    print(f"Pages to tile: {pages_to_process}")
    print()

    total_tiles = 0
    for page_num in pages_to_process:
        if page_num < 1 or page_num > total_pages:
            print(f"  [SKIP] Page {page_num} out of range (1-{total_pages})")
            continue

        if args.quality:
            # Адаптивный режим
            page = doc[page_num - 1]
            result = compute_adaptive_grid(
                page.rect.width, page.rect.height, quality=args.quality)
            if result is None:
                continue  # текстовая страница
            rows, cols, scale, overlap = result
            label = f"page_{page_num}"
        else:
            # Legacy режим
            cfg = PAGE_CONFIG.get(page_num, DEFAULT_CONFIG)
            rows, cols, scale, overlap, label = cfg

        print(f"  Page {page_num:2d}: {rows}x{cols} grid, scale={scale}x, overlap={overlap}%")
        print(f"           [{label}]")

        n = tile_page(doc, page_num - 1, rows, cols, scale, overlap, label, args.out)
        total_tiles += n
        print()

    doc.close()
    print(f"Done! Total tiles created: {total_tiles}")
    print(f"Tiles saved to: {args.out}")


if __name__ == "__main__":
    main()
