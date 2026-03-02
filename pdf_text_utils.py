"""
pdf_text_utils.py
-----------------
Утилиты для интеллектуального извлечения текста из PDF.
Обнаруживает повреждённый текст от CAD-шрифтов (ISOCPEUR и др.)
и автоматически переключается на OCR через Tesseract.

Зависимости:
  - PyMuPDF (fitz)  -- обязательно (уже установлен)
  - pytesseract     -- опционально (для OCR-фолбэка)
  - Pillow (PIL)    -- опционально (для OCR-фолбэка, уже установлен)
  - Tesseract OCR   -- опционально (системная утилита для Windows)

Использование:
  from pdf_text_utils import extract_text_smart, quality_metadata
  report = extract_text_smart("document.pdf", "_output/extracted_text.txt")
"""

import io
import os
import sys
from dataclasses import dataclass, field

import fitz  # PyMuPDF


# ═══════════════════════════════════════════════════════════════════════════════
#  КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════════════════════

# CAD-шрифты маппируют кириллицу на эти Unicode-диапазоны (вместо U+0400-U+04FF):
CAD_SUSPECT_RANGES = [
    (0x0180, 0x01CF),  # Latin Extended-B (нижний)
    (0x01D0, 0x01FF),  # Latin Extended-B (основной диапазон порчи ISOCPEUR)
    (0x0230, 0x024F),  # Latin Extended-B (верхний)
    (0x0250, 0x02AF),  # IPA Extensions (иногда используется CAD)
]

# Нормальная кириллица
CYRILLIC_RANGE = (0x0400, 0x04FF)

# Пороги качества (corruption_ratio = suspect / (suspect + cyrillic))
THRESHOLD_PARTIAL  = 0.10   # >10% подозрительных → PARTIAL
THRESHOLD_CRITICAL = 0.30   # >30% подозрительных → CRITICAL

# Минимальное кол-во текстовых символов для анализа качества
MIN_TEXT_FOR_ANALYSIS = 20

# Масштаб рендеринга страницы для OCR (3.0 ≈ 216 DPI для A4)
OCR_RENDER_DPI = 300

# Известные CAD-шрифты (для детектирования по имени)
KNOWN_CAD_FONTS = [
    'ISOCPEUR', 'ISOCP', 'ISOCTEUR', 'ISOCT', 'ISOCPEUR ITALIC',
    'GOST', 'GOSTYPE', 'GOSTTYPE', 'GOST TYPE A', 'GOST TYPE B',
    'SIMPLEX', 'COMPLEX', 'MONOTXT',
    'ROMANS', 'ROMAND', 'ROMANC', 'ROMANT',
    'SCRIPTS', 'SCRIPTC',
    'ITALIC', 'ITALICC', 'ITALICT',
    'TXT', 'TXTB',
    'SYASTRO', 'SYMAP', 'SYMATH', 'SYMETEO', 'SYMUSIC',
]

# Стандартные пути Tesseract на Windows
_TESSERACT_PATHS_WIN = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    os.path.expanduser(r'~\AppData\Local\Tesseract-OCR\tesseract.exe'),
    os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  DATACLASSES — результаты анализа
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PageQuality:
    """Результат оценки качества текста одной страницы."""
    page_num: int
    total_chars: int
    cyrillic_chars: int
    suspect_chars: int
    corruption_ratio: float   # 0.0 - 1.0
    quality: str              # "OK", "PARTIAL", "CRITICAL", "EMPTY"
    text_method: str          # "direct", "ocr", "corrupted"
    detected_fonts: list = field(default_factory=list)


@dataclass
class ExtractionReport:
    """Сводный отчёт об извлечении текста из PDF."""
    total_pages: int
    direct_ok: int
    ocr_fallback: int
    corrupted_kept: int
    empty_pages: int
    corrupted_fonts: list
    overall_quality: str      # "OK", "PARTIAL", "CRITICAL"
    pages: list               # list[PageQuality]
    ocr_available: bool
    ocr_engine: str           # "tesseract" / "none"


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 1: Оценка качества текста
# ═══════════════════════════════════════════════════════════════════════════════

def assess_text_quality(text: str, page_num: int) -> PageQuality:
    """
    Оценивает качество извлечённого текста, детектируя порчу от CAD-шрифтов.

    Считает символы в подозрительных Unicode-диапазонах (куда CAD маппирует
    кириллицу) и сравнивает с количеством нормальных кириллических символов.

    Args:
        text: Извлечённый текст страницы
        page_num: Номер страницы (1-based)

    Returns:
        PageQuality с результатами анализа
    """
    if not text or len(text.strip()) < MIN_TEXT_FOR_ANALYSIS:
        return PageQuality(
            page_num=page_num, total_chars=len(text) if text else 0,
            cyrillic_chars=0, suspect_chars=0,
            corruption_ratio=0.0, quality="EMPTY", text_method="direct",
        )

    suspect = 0
    cyrillic = 0

    for ch in text:
        cp = ord(ch)
        # Проверяем подозрительные диапазоны
        is_suspect = False
        for lo, hi in CAD_SUSPECT_RANGES:
            if lo <= cp <= hi:
                suspect += 1
                is_suspect = True
                break
        if not is_suspect and CYRILLIC_RANGE[0] <= cp <= CYRILLIC_RANGE[1]:
            cyrillic += 1

    # Вычисляем corruption_ratio
    text_chars = suspect + cyrillic
    if text_chars == 0:
        corruption_ratio = 0.0
    else:
        corruption_ratio = suspect / text_chars

    # Классификация
    if corruption_ratio >= THRESHOLD_CRITICAL:
        quality = "CRITICAL"
    elif corruption_ratio >= THRESHOLD_PARTIAL:
        quality = "PARTIAL"
    else:
        quality = "OK"

    return PageQuality(
        page_num=page_num,
        total_chars=len(text),
        cyrillic_chars=cyrillic,
        suspect_chars=suspect,
        corruption_ratio=round(corruption_ratio, 4),
        quality=quality,
        text_method="direct",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 2: Детекция CAD-шрифтов
# ═══════════════════════════════════════════════════════════════════════════════

def detect_cad_fonts(page) -> list:
    """
    Извлекает список CAD-шрифтов, обнаруженных на странице PDF.

    Проверяет все шрифты на странице по списку известных CAD-шрифтов
    (ISOCPEUR, GOST, SHX-совместимые и т.п.).

    Args:
        page: Объект fitz.Page

    Returns:
        Список имён CAD-шрифтов (может быть пустым)
    """
    cad_found = []
    try:
        fonts = page.get_fonts(full=True)
        for font_info in fonts:
            font_name = font_info[3] if len(font_info) > 3 else ""
            if not font_name:
                continue
            # Нормализуем имя
            name_upper = font_name.upper().replace('-', '').replace('_', '').replace(' ', '')
            for cad in KNOWN_CAD_FONTS:
                cad_norm = cad.upper().replace('-', '').replace('_', '').replace(' ', '')
                if cad_norm in name_upper:
                    cad_found.append(font_name)
                    break
    except Exception:
        pass
    return cad_found


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 3: Проверка Tesseract
# ═══════════════════════════════════════════════════════════════════════════════

def _configure_tesseract_path():
    """Пытается найти Tesseract в стандартных путях Windows."""
    if os.name != 'nt':
        return

    try:
        import pytesseract
        # Проверяем текущий путь
        try:
            pytesseract.get_tesseract_version()
            return  # уже работает
        except Exception:
            pass

        # Ищем в стандартных местах
        for path in _TESSERACT_PATHS_WIN:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                try:
                    pytesseract.get_tesseract_version()
                    return  # нашли и работает
                except Exception:
                    continue
    except ImportError:
        pass


def check_tesseract():
    """
    Проверяет наличие Tesseract OCR и поддержку русского языка.

    Returns:
        (available: bool, info: str)
    """
    _configure_tesseract_path()

    try:
        import pytesseract
        version = pytesseract.get_tesseract_version()

        # Проверяем поддержку русского языка
        try:
            langs = pytesseract.get_languages()
        except Exception:
            langs = []

        if 'rus' not in langs:
            return False, (
                f"Tesseract v{version} найден, но русский язык НЕ установлен. "
                f"Доступные языки: {', '.join(langs)}. "
                f"Установите русский язык через инсталлятор Tesseract "
                f"(Additional language data -> Russian)."
            )
        return True, f"Tesseract v{version}, языки: {', '.join(langs)}"

    except ImportError:
        return False, (
            "pytesseract не установлен. "
            "Запустите: pip install pytesseract"
        )
    except Exception as e:
        return False, (
            f"Tesseract OCR не найден. Установите: "
            f"https://github.com/UB-Mannheim/tesseract/wiki "
            f"(ошибка: {e})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 4: OCR страницы через Tesseract
# ═══════════════════════════════════════════════════════════════════════════════

def ocr_page(page, page_num: int, dpi: int = OCR_RENDER_DPI) -> str:
    """
    Рендерит страницу PDF в изображение и распознаёт текст через Tesseract OCR.

    1. Рендерит страницу через PyMuPDF в pixmap с заданным DPI
    2. Конвертирует в PIL Image
    3. Запускает Tesseract OCR с языками rus+eng

    Args:
        page: Объект fitz.Page
        page_num: Номер страницы (для логирования)
        dpi: DPI рендеринга (по умолчанию 300)

    Returns:
        Распознанный текст страницы
    """
    import pytesseract
    from PIL import Image

    # Вычисляем масштаб: fitz использует 72 DPI как базу
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)

    # Рендерим страницу
    pix = page.get_pixmap(matrix=mat, alpha=False)

    # Конвертируем в PIL Image
    img_bytes = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_bytes))

    # Запускаем Tesseract OCR (rus+eng для смешанного текста)
    ocr_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(
        img,
        lang='rus+eng',
        config=ocr_config,
    )

    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 5: Главная — умное извлечение текста
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text_smart(
    pdf_path: str,
    out_txt: str,
    force_ocr: bool = False,
    no_ocr: bool = False,
) -> ExtractionReport:
    """
    Интеллектуальное извлечение текста из PDF с автоматическим
    OCR-фолбэком для страниц с повреждёнными CAD-шрифтами.

    Алгоритм для каждой страницы:
      1. Извлечь текст через page.get_text()
      2. Оценить качество (assess_text_quality)
      3. Если порча обнаружена и OCR доступен → распознать через Tesseract
      4. Если OCR недоступен → пометить [CAD_FONT_CORRUPTED]

    Args:
        pdf_path: Путь к PDF-файлу
        out_txt: Путь к выходному файлу с текстом
        force_ocr: OCR для ВСЕХ страниц (для тестирования)
        no_ocr: Пропустить OCR даже при обнаружении порчи

    Returns:
        ExtractionReport с детальной статистикой
    """
    # --- Проверяем OCR ---
    ocr_available = False
    ocr_info = ""

    if no_ocr:
        ocr_available = False
        ocr_info = "OCR отключён (--no-ocr)"
    else:
        ocr_available, ocr_info = check_tesseract()

    if force_ocr and not ocr_available:
        print(f"  [WARN] --force-ocr указан, но OCR недоступен: {ocr_info}")
        force_ocr = False

    print(f"  Извлечение текста из PDF...")
    if ocr_available:
        print(f"  OCR: {ocr_info}")
    elif not no_ocr:
        print(f"  OCR: {ocr_info}")

    # --- Открываем PDF ---
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    lines = []
    page_qualities = []
    all_cad_fonts = set()

    direct_ok = 0
    ocr_fallback = 0
    corrupted_kept = 0
    empty_pages = 0

    for i, page in enumerate(doc):
        page_num = i + 1

        # 1. Обычное извлечение текста
        raw_text = page.get_text()

        # 2. Оценка качества
        pq = assess_text_quality(raw_text, page_num)

        # 3. Детекция CAD-шрифтов
        cad_fonts = detect_cad_fonts(page)
        pq.detected_fonts = cad_fonts
        all_cad_fonts.update(cad_fonts)

        # 4. Определяем нужен ли OCR
        needs_ocr = force_ocr or (pq.quality in ("CRITICAL", "PARTIAL"))

        # --- Заголовок страницы ---
        lines.append(f"\n{'='*60}")
        lines.append(f"PAGE {page_num}")
        lines.append(f"{'='*60}")

        # --- Обработка ---
        if pq.quality == "EMPTY":
            # Пустая или минимальная страница
            lines.append(raw_text)
            pq.text_method = "direct"
            empty_pages += 1

        elif needs_ocr and ocr_available:
            # OCR-фолбэк
            try:
                ocr_text = ocr_page(page, page_num)
                fonts_str = ', '.join(cad_fonts) if cad_fonts else 'auto-detected'
                lines.append(f"[OCR] (CAD-шрифты: {fonts_str}, "
                             f"corruption={pq.corruption_ratio:.0%})")
                lines.append(ocr_text)
                pq.text_method = "ocr"
                ocr_fallback += 1
                print(f"    Стр. {page_num}: OCR "
                      f"(порча={pq.corruption_ratio:.0%}, "
                      f"шрифты: {fonts_str})")
            except Exception as e:
                # OCR не удался
                lines.append(f"[CAD_FONT_CORRUPTED] (OCR failed: {e})")
                lines.append(raw_text)
                pq.text_method = "corrupted"
                corrupted_kept += 1
                print(f"    Стр. {page_num}: OCR ОШИБКА ({e})")

        elif needs_ocr and not ocr_available:
            # OCR нужен, но недоступен
            lines.append(f"[CAD_FONT_CORRUPTED] (порча={pq.corruption_ratio:.0%})")
            if cad_fonts:
                lines.append(f"[ШРИФТЫ: {', '.join(cad_fonts)}]")
            lines.append(raw_text)
            pq.text_method = "corrupted"
            corrupted_kept += 1
            if page_num <= 5 or page_num == total_pages:
                print(f"    Стр. {page_num}: ПОВРЕЖДЁН "
                      f"(порча={pq.corruption_ratio:.0%})")

        else:
            # Нормальный текст
            lines.append(raw_text)
            pq.text_method = "direct"
            direct_ok += 1

        page_qualities.append(pq)

    doc.close()

    # --- Сохраняем текст ---
    os.makedirs(os.path.dirname(out_txt) if os.path.dirname(out_txt) else '.', exist_ok=True)
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    size_kb = os.path.getsize(out_txt) / 1024
    print(f"  -> {out_txt}  ({size_kb:.0f} KB)")

    # --- Итоговое качество ---
    if corrupted_kept > 0:
        overall = "CRITICAL"
    elif ocr_fallback > 0:
        overall = "PARTIAL_OCR"
    else:
        overall = "OK"

    report = ExtractionReport(
        total_pages=total_pages,
        direct_ok=direct_ok,
        ocr_fallback=ocr_fallback,
        corrupted_kept=corrupted_kept,
        empty_pages=empty_pages,
        corrupted_fonts=sorted(all_cad_fonts),
        overall_quality=overall,
        pages=page_qualities,
        ocr_available=ocr_available,
        ocr_engine="tesseract" if ocr_available else "none",
    )

    # --- Сводка в консоль ---
    _print_summary(report)

    return report


def _print_summary(report: ExtractionReport):
    """Печатает сводку по извлечению текста."""
    print(f"\n  Извлечение текста: {report.total_pages} стр.")
    if report.direct_ok > 0:
        print(f"    {report.direct_ok} стр. — прямое извлечение (OK)")
    if report.ocr_fallback > 0:
        print(f"    {report.ocr_fallback} стр. — OCR (CAD-шрифты)")
    if report.corrupted_kept > 0:
        print(f"    {report.corrupted_kept} стр. — ПОВРЕЖДЕНО (OCR недоступен)")
    if report.empty_pages > 0:
        print(f"    {report.empty_pages} стр. — пустые/минимальный текст")
    if report.corrupted_fonts:
        print(f"    CAD-шрифты: {', '.join(report.corrupted_fonts)}")

    readable = report.direct_ok + report.ocr_fallback
    if report.total_pages > 0:
        pct = readable / report.total_pages * 100
        print(f"  Качество: {pct:.0f}% читаемый текст "
              f"({report.direct_ok} direct + {report.ocr_fallback} OCR)")

    if report.corrupted_kept > 0 and not report.ocr_available:
        print(f"\n  [!] Установите Tesseract OCR для распознавания CAD-текста:")
        print(f"      https://github.com/UB-Mannheim/tesseract/wiki")
        print(f"      При установке отметьте 'Russian' в языках.")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  ФУНКЦИЯ 6: Метаданные для project_info.json
# ═══════════════════════════════════════════════════════════════════════════════

def quality_metadata(report: ExtractionReport) -> dict:
    """
    Формирует словарь метаданных качества для записи в project_info.json.

    Args:
        report: ExtractionReport от extract_text_smart()

    Returns:
        dict для JSON-сериализации
    """
    per_page = []
    for pq in report.pages:
        entry = {
            "page": pq.page_num,
            "quality": pq.quality,
            "method": pq.text_method,
        }
        if pq.corruption_ratio > 0:
            entry["corruption_ratio"] = pq.corruption_ratio
        if pq.detected_fonts:
            entry["cad_fonts"] = pq.detected_fonts
        per_page.append(entry)

    return {
        "total_pages": report.total_pages,
        "direct_ok": report.direct_ok,
        "ocr_fallback": report.ocr_fallback,
        "corrupted_kept": report.corrupted_kept,
        "empty_pages": report.empty_pages,
        "corrupted_fonts": report.corrupted_fonts,
        "overall_quality": report.overall_quality,
        "ocr_engine": report.ocr_engine,
        "pages": per_page,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN — для тестирования модуля отдельно
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Smart PDF text extraction with CAD font detection + OCR fallback"
    )
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("-o", "--output", default="extracted_text.txt",
                        help="Output text file (default: extracted_text.txt)")
    parser.add_argument("--force-ocr", action="store_true",
                        help="Force OCR for ALL pages")
    parser.add_argument("--no-ocr", action="store_true",
                        help="Skip OCR even if corruption detected")
    parser.add_argument("--check-ocr", action="store_true",
                        help="Only check Tesseract availability and exit")
    args = parser.parse_args()

    if args.check_ocr:
        available, info = check_tesseract()
        print(f"Tesseract OCR: {'ДОСТУПЕН' if available else 'НЕДОСТУПЕН'}")
        print(f"  {info}")
        sys.exit(0 if available else 1)

    if not os.path.exists(args.pdf_path):
        print(f"[ERROR] PDF не найден: {args.pdf_path}")
        sys.exit(1)

    report = extract_text_smart(
        args.pdf_path,
        args.output,
        force_ocr=args.force_ocr,
        no_ocr=args.no_ocr,
    )

    print(f"\nИтого: {report.overall_quality}")
    if report.corrupted_fonts:
        print(f"CAD-шрифты: {', '.join(report.corrupted_fonts)}")
