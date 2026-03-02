@echo off
chcp 65001 >nul
echo ════════════════════════════════════════
echo   Audit Manager — запуск в фоне
echo ════════════════════════════════════════

cd /d "%~dp0"

:: Проверяем, не запущен ли уже
tasklist /FI "WINDOWTITLE eq AuditManager" 2>nul | find "python" >nul
if not errorlevel 1 (
    echo.
    echo   Сервер уже запущен!
    start "" http://localhost:8080
    echo   Браузер открыт.
    echo.
    timeout /t 2 >nul
    exit /b
)

:: Запускаем в фоне
start "AuditManager" /MIN python main.py

echo.
echo   Сервер запускается...
echo.

:: Ждём пока сервер стартует, затем открываем браузер
timeout /t 2 >nul
start "" http://localhost:8080

echo   Браузер открыт: http://localhost:8080
echo   Чтобы остановить — запустите stop_server.bat
echo ════════════════════════════════════════
timeout /t 3 >nul
