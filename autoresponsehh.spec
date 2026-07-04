# autoresponsehh.spec — PyInstaller build specification
#
# Запуск:   pyinstaller autoresponsehh.spec --noconfirm
# Или через build.bat (рекомендуется).
#
# Результат: dist\AutoResponseHH\AutoResponseHH.exe
# При первом запуске exe скачивает Chromium (~200 МБ) в dist\AutoResponseHH\browsers\

from PyInstaller.utils.hooks import collect_all, collect_data_files

# Playwright: драйвер node.exe + playwright.cmd + все данные браузера
pl_datas, pl_binaries, pl_hidden = collect_all("playwright")

# playwright_stealth: JS-патчи для обхода детектирования
try:
    st_datas, st_binaries, st_hidden = collect_all("playwright_stealth")
except Exception:
    st_datas, st_binaries, st_hidden = [], [], []

# Flask + Jinja2 — шаблоны и данные
fl_datas = collect_data_files("flask") + collect_data_files("jinja2")

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=[
        ("gui/static", "gui/static"),   # веб-интерфейс Flask
        *pl_datas,
        *st_datas,
        *fl_datas,
    ],
    binaries=pl_binaries + st_binaries,
    hiddenimports=(
        pl_hidden + st_hidden + [
            # stdlib (могут не подхватиться при lazy-импортах)
            "sqlite3", "csv", "io", "uuid", "threading",
            "subprocess", "email", "logging.handlers",
            # flask-экосистема
            "flask", "flask.json", "flask.templating",
            "flask.cli", "flask.wrappers",
            "jinja2", "jinja2.ext", "jinja2.loaders",
            "jinja2.defaults", "jinja2.environment",
            "werkzeug", "werkzeug.serving", "werkzeug.routing",
            "werkzeug.exceptions", "werkzeug.utils",
            "werkzeug.middleware.shared_data",
            "click", "itsdangerous", "markupsafe",
            # python-dotenv
            "dotenv", "dotenv.main", "dotenv.parser",
            # schedule (lazy import в main.py)
            "schedule",
            # наши пакеты
            "bootstrap",
            "config", "config.config",
            "pages", "pages.login_page",
            "pages.locators_page", "pages.page_auto_response",
            "utils", "utils.db", "utils.auth",
            "utils.hh_api", "utils.negotiations", "utils.resume_raiser",
            "gui", "gui.app",
        ]
    ),
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoResponseHH",
    console=False,      # GUI-приложение — консоль скрыта; логи видны в веб-интерфейсе
    uac_admin=False,
    icon=None,          # TODO: icon="icon.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    name="AutoResponseHH",
)
