"""
Обёртка для generate_excel_report.py.
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from webapp.config import BASE_DIR, REPORTS_DIR, GENERATE_EXCEL_SCRIPT
from webapp.services.process_runner import run_script


async def generate_excel(output_path: Optional[str] = None) -> tuple[bool, str]:
    """
    Генерирует Excel-отчёт из 03_findings.json всех проектов.

    Returns:
        (success, file_path_or_error)
    """
    if not output_path:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = str(REPORTS_DIR / f"audit_report_{ts}.xlsx")

    args = ["--out", output_path]

    exit_code, stdout, stderr = await run_script(
        str(GENERATE_EXCEL_SCRIPT),
        args,
    )

    if exit_code == 0 and os.path.exists(output_path):
        return True, output_path
    else:
        return False, stderr or f"Exit code: {exit_code}"
