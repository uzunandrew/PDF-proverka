@echo off
chcp 65001 >nul
echo ════════════════════════════════════════
echo   Audit Manager — остановка
echo ════════════════════════════════════════

taskkill /FI "WINDOWTITLE eq AuditManager" /F >nul 2>&1

echo.
echo   Сервер остановлен.
echo ════════════════════════════════════════
timeout /t 2 >nul
