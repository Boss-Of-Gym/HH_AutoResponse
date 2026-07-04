@echo off
setlocal

set PYTHON=.venv\Scripts\python.exe
set DIST=dist\AutoResponseHH

echo.
echo =========================================================
echo   AutoResponseHH - сборка дистрибутива
echo =========================================================
echo.

if not exist %PYTHON% (
    echo ОШИБКА: виртуальное окружение не найдено.
    echo.
    echo Создайте его командами:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo   .venv\Scripts\python.exe -m playwright install chromium
    echo.
    pause
    exit /b 1
)

echo [1/3] Установка / проверка PyInstaller...
%PYTHON% -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo ОШИБКА: не удалось установить PyInstaller.
    pause
    exit /b 1
)

echo [2/3] Очистка предыдущей сборки...
if exist %DIST% rmdir /s /q %DIST%
if exist build   rmdir /s /q build

echo [3/3] Сборка...
%PYTHON% -m PyInstaller autoresponsehh.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ОШИБКА: сборка завершилась с ошибкой.
    pause
    exit /b 1
)

echo.
echo =========================================================
echo   Готово! Дистрибутив: %DIST%\
echo.
echo   Для пользователей:
echo     1. Скопируйте папку AutoResponseHH\ в любое место
echo     2. Скопируйте settings.json.example в settings.json
echo        и заполните логин / пароль HH.ru
echo     3. Запустите AutoResponseHH.exe
echo     4. При первом запуске скачается Chromium (~200 МБ)
echo =========================================================
echo.
pause
endlocal
