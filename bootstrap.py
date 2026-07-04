"""
bootstrap.py — настройка окружения Playwright перед запуском.

Импортируется в main.py ДО всех playwright-импортов.
В exe-сборке (PyInstaller onedir) устанавливает PLAYWRIGHT_BROWSERS_PATH
и при первом запуске скачивает Chromium через встроенный playwright-драйвер.
"""
import os
import subprocess
import sys
from pathlib import Path


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _meipass() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _playwright_install_cmd(browsers: Path) -> list | None:
    base = _meipass()
    if base is None:
        return None
    for candidate in [base, _exe_dir()]:
        node = candidate / "playwright" / "driver" / "node.exe"
        cli = candidate / "playwright" / "driver" / "package" / "cli.js"
        if node.exists() and cli.exists():
            return [str(node), str(cli), "install", "chromium", "--with-deps"]
    return None


def _browsers_dir() -> Path:
    if getattr(sys, "frozen", False):
        return _exe_dir() / "browsers"
    return Path.home() / "AppData" / "Local" / "ms-playwright"


def setup_environment() -> None:
    """Устанавливает PLAYWRIGHT_BROWSERS_PATH и при необходимости скачивает Chromium."""
    browsers = _browsers_dir()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    if getattr(sys, "frozen", False):
        _ensure_chromium(browsers)


def _ensure_chromium(browsers: Path) -> None:
    if any(browsers.glob("chromium-*")):
        return
    browsers.mkdir(parents=True, exist_ok=True)
    print("=" * 58)
    print("  Первый запуск: установка браузера Chromium (~200 МБ)")
    print("  Пожалуйста, подождите...")
    print("=" * 58)
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    cmd = _playwright_install_cmd(browsers)
    if cmd is None:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print("ОШИБКА: не удалось установить Chromium.")
        sys.exit(1)
    print("Браузер установлен успешно!")
