import argparse
import logging
import os
import time
from datetime import datetime

import bootstrap
bootstrap.setup_environment()

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from config import config
from pages.page_auto_response import AutoResponsePage
from utils.auth import Auth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)

_NETWORK_ERRORS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
)
_MAX_RETRIES = 3
_RETRY_DELAY = 30


def run(dry_run: bool = False) -> None:
    logger.info("=" * 55)
    logger.info("  AutoResponseHH запущен")
    if dry_run:
        logger.info("  ★ РЕЖИМ DRY-RUN: отклики не отправляются")
    logger.info(f"  Запросов: {len(config.SearchConfig.QUERIES)}")
    logger.info(f"  Регион: {config.SearchConfig.AREA} | Страниц: {config.SearchConfig.MAX_PAGES}")
    logger.info(f"  Лимит сессии: {config.BotConfig.MAX_RESPONSES_PER_RUN} откликов")
    logger.info(f"  Задержка: {config.BotConfig.DELAY_MIN}-{config.BotConfig.DELAY_MAX}с")
    logger.info(f"  Хранить applied: {config.BotConfig.APPLIED_EXPIRY_DAYS} дней")
    if config.BotConfig.USE_API_PREFILTER:
        sal = f", мин. зарплата: {config.BotConfig.SALARY_MIN}₽" if config.BotConfig.SALARY_MIN else ""
        logger.info(f"  API предфильтр: вкл{sal}")
    if config.BlacklistConfig.COMPANIES:
        logger.info(f"  Блэклист компаний: {len(config.BlacklistConfig.COMPANIES)}")
    logger.info("=" * 55)

    started_at = datetime.now()

    with Stealth().use_sync(sync_playwright()) as playwright:
        browser = playwright.chromium.launch(
            headless=config.Browser.HEADLESS,
            args=["--start-maximized", "--window-size=1920,1080"],
            channel="chrome",
        )
        try:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale=config.Browser.LOCALE,
                timezone_id=config.Browser.TIMEZONE,
            )
            page = context.new_page()
            page.set_default_timeout(config.Timeouts.PAGE_LOAD)
            page.set_default_navigation_timeout(config.Timeouts.PAGE_LOAD)
            # Авторизация с retry при сетевых ошибках
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    auth = Auth(page)
                    auth.authentication(page)
                    break
                except Exception as e:
                    err_str = str(e)
                    if any(net_err in err_str for net_err in _NETWORK_ERRORS):
                        if attempt < _MAX_RETRIES:
                            logger.warning(
                                f"Сетевая ошибка (попытка {attempt}/{_MAX_RETRIES}): "
                                f"{err_str.splitlines()[0]}. Повтор через {_RETRY_DELAY}с..."
                            )
                            time.sleep(_RETRY_DELAY)
                            continue
                        else:
                            logger.error(f"Сеть недоступна после {_MAX_RETRIES} попыток.")
                            raise
                    raise

            # п.1: поднимаем резюме перед откликами
            if config.ResumeRaiseConfig.ENABLED and not dry_run:
                try:
                    from utils.resume_raiser import raise_all_resumes
                    raise_all_resumes(page)
                except Exception as e:
                    logger.warning(f"Не удалось поднять резюме: {e}")

            auto_response = AutoResponsePage(page)
            auto_response.auto_response(dry_run=dry_run)

        except KeyboardInterrupt:
            logger.warning("Прерывание пользователем (Ctrl+C). Сохраняем состояние...")
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
            try:
                os.makedirs("artifacts/screenshots", exist_ok=True)
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                page.screenshot(path=f"artifacts/screenshots/crash_{ts}.png", full_page=True)
                logger.info(f"Скриншот: artifacts/screenshots/crash_{ts}.png")
            except Exception as dump_err:
                logger.warning(f"Не удалось сохранить скриншот: {dump_err}")
            raise
        finally:
            elapsed = datetime.now() - started_at
            logger.info(f"=== Завершено. Время работы: {elapsed} ===")
            browser.close()


def check_status() -> None:
    """п.4: проверяет статусы откликов на странице переговоров HH.ru."""
    logger.info("=" * 55)
    logger.info("  AutoResponseHH — проверка статусов откликов")
    logger.info("=" * 55)

    with Stealth().use_sync(sync_playwright()) as playwright:
        browser = playwright.chromium.launch(
            headless=config.Browser.HEADLESS,
            args=["--start-maximized"],
            channel="chrome",
        )
        try:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale=config.Browser.LOCALE,
                timezone_id=config.Browser.TIMEZONE,
            )
            page = context.new_page()
            page.set_default_timeout(config.Timeouts.PAGE_LOAD)
            auth = Auth(page)
            auth.authentication(page)

            from utils.negotiations import check_and_save_negotiations
            from utils import db as _db
            _db.init_db()
            changes = check_and_save_negotiations(page)

            stats = _db.get_negotiations_stats()
            if stats:
                logger.info("Сводка по статусам откликов:")
                for status, cnt in stats.items():
                    logger.info(f"  {cnt:>4}  {status}")

            if not changes:
                logger.info("Изменений статусов нет")

        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.error(f"Ошибка при проверке статусов: {e}", exc_info=True)
        finally:
            browser.close()


def _run_scheduler(run_at: str) -> None:
    """п.32: запускает run() каждый день в указанное время HH:MM"""
    try:
        import schedule
    except ImportError:
        logger.error("Для планировщика установите: pip install schedule")
        return

    logger.info(f"Планировщик активен: ежедневный запуск в {run_at}")
    schedule.every().day.at(run_at).do(run)

    logger.info(f"Следующий запуск: {schedule.next_run()}")
    while True:
        schedule.run_pending()
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AutoResponseHH — автоматические отклики на HH.ru",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python main.py                    # открыть GUI (по умолчанию)\n"
            "  python main.py --port 8080        # GUI на другом порту\n"
            "  python main.py --run              # запустить бота без GUI\n"
            "  python main.py --dry-run          # тест без реальных откликов\n"
            "  python main.py --check-status     # проверить статусы откликов\n"
            "  python main.py --run-at 08:00     # ежедневно в 08:00\n"
        ),
    )
    parser.add_argument(
        "--run-at",
        metavar="HH:MM",
        help="Запускать ежедневно в указанное время (например, 08:00)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Запустить бота (отправлять отклики)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Тестовый режим: проходит по вакансиям, но не отправляет отклики",
    )
    parser.add_argument(
        "--check-status",
        action="store_true",
        help="Проверить статусы откликов на HH.ru и обновить базу данных",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить веб-интерфейс (поведение по умолчанию)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5555,
        metavar="PORT",
        help="Порт для GUI (по умолчанию 5555)",
    )
    args = parser.parse_args()

    if args.check_status:
        check_status()
    elif args.run_at:
        _run_scheduler(args.run_at)
    elif args.run or args.dry_run:
        run(dry_run=args.dry_run)
    else:
        # По умолчанию — GUI (python main.py или python main.py --gui)
        from gui.app import run_gui
        run_gui(port=args.port)


if __name__ == "__main__":
    main()
