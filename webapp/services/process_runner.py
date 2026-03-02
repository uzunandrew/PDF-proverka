"""
Универсальный async subprocess runner.
Запускает Python-скрипты и внешние процессы с перехватом stdout/stderr.
"""
import asyncio
import os
import sys
import platform
from typing import Callable, Optional, Awaitable

from webapp.config import BASE_DIR

# На Windows скрываем консольные окна подпроцессов
_SUBPROCESS_FLAGS: dict = {}
if platform.system() == "Windows":
    _SUBPROCESS_FLAGS["creationflags"] = 0x08000000  # CREATE_NO_WINDOW


async def run_script(
    script: str,
    args: list[str] = None,
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
    env_overrides: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
) -> tuple[int, str, str]:
    """
    Запускает Python-скрипт как подпроцесс.

    Args:
        script: Путь к скрипту (относительно BASE_DIR или абсолютный)
        args: Аргументы командной строки
        on_output: Async-callback для каждой строки вывода (для live-лога)
        env_overrides: Дополнительные переменные окружения
        cwd: Рабочая директория (по умолчанию BASE_DIR)
        timeout: Таймаут в секундах

    Returns:
        (exit_code, stdout, stderr)
    """
    env = os.environ.copy()
    # Обеспечиваем UTF-8
    env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    cmd = [sys.executable, str(script)] + (args or [])
    work_dir = cwd or str(BASE_DIR)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir,
        env=env,
        **_SUBPROCESS_FLAGS,
    )

    stdout_lines = []
    stderr_lines = []

    async def read_stream(stream, lines, is_stderr=False):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            lines.append(text)
            if on_output:
                prefix = "[ERR] " if is_stderr else ""
                try:
                    await on_output(f"{prefix}{text}")
                except Exception:
                    pass

    try:
        if timeout:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(proc.stdout, stdout_lines),
                    read_stream(proc.stderr, stderr_lines, True),
                ),
                timeout=timeout,
            )
        else:
            await asyncio.gather(
                read_stream(proc.stdout, stdout_lines),
                read_stream(proc.stderr, stderr_lines, True),
            )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        stderr_lines.append(f"[TIMEOUT] Процесс превысил таймаут {timeout} сек.")
        return -1, "\n".join(stdout_lines), "\n".join(stderr_lines)
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        return -2, "\n".join(stdout_lines), "Отменено"

    return proc.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)


async def run_command(
    cmd: list[str],
    on_output: Optional[Callable[[str], Awaitable[None]]] = None,
    env_overrides: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    input_text: Optional[str] = None,
) -> tuple[int, str, str]:
    """
    Запускает произвольную команду (не только Python).
    Используется для Claude CLI.

    Args:
        cmd: Команда и аргументы (например, ["claude", "-p", ...])
        input_text: Текст для подачи через stdin
        остальное аналогично run_script
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    # Если удаляется CLAUDECODE — удаляем ВСЕ переменные Claude Code сессии,
    # чтобы вложенный Claude CLI не думал что он внутри другой сессии
    if env_overrides and env_overrides.get("CLAUDECODE") is None:
        claude_keys = [k for k in env if k.startswith("CLAUDE")]
        for k in claude_keys:
            env.pop(k, None)

    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    work_dir = cwd or str(BASE_DIR)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_text else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir,
        env=env,
        **_SUBPROCESS_FLAGS,
    )

    stdout_lines = []
    stderr_lines = []

    if input_text:
        # Для Claude CLI: подаём задачу через stdin, читаем stdout/stderr
        try:
            if timeout:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(input=input_text.encode("utf-8")),
                    timeout=timeout,
                )
            else:
                stdout_data, stderr_data = await proc.communicate(
                    input=input_text.encode("utf-8")
                )

            stdout_text = stdout_data.decode("utf-8", errors="replace")
            stderr_text = stderr_data.decode("utf-8", errors="replace")

            if on_output and stdout_text.strip():
                for line in stdout_text.splitlines():
                    try:
                        await on_output(line)
                    except Exception:
                        pass

            return proc.returncode, stdout_text, stderr_text

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"[TIMEOUT] Claude-сессия превысила таймаут {timeout} сек."
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            return -2, "", "Отменено"
    else:
        # Без stdin — стриминг stdout
        async def read_stream(stream, lines, is_stderr=False):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                lines.append(text)
                if on_output:
                    prefix = "[ERR] " if is_stderr else ""
                    try:
                        await on_output(f"{prefix}{text}")
                    except Exception:
                        pass

        try:
            if timeout:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(proc.stdout, stdout_lines),
                        read_stream(proc.stderr, stderr_lines, True),
                    ),
                    timeout=timeout,
                )
            else:
                await asyncio.gather(
                    read_stream(proc.stdout, stdout_lines),
                    read_stream(proc.stderr, stderr_lines, True),
                )
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "\n".join(stdout_lines), "\n".join(stderr_lines) + "\n[TIMEOUT]"
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            return -2, "\n".join(stdout_lines), "Отменено"

        return proc.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)
