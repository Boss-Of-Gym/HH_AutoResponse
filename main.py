import logging
import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from pages.page_auto_response import AutoResponsePage
from utils.auth import Auth
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

_NETWORK_ERRORS = ("ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT")
_MAX_RETRIES = 3
_RETRY_DELAY = 30


def run() -> None:
    logger.info("=== AutoResponseHH запущен ===")
    started_at = datetime.now()

    with Stealth().use_sync(sync_playwright()) as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--start-maximized", "--window-size=1920,1080"],
            channel="chrome"
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Europe/Moscow"
        )
        page = context.new_page()
        page.set_default_timeout(config.Timeouts.PAGE_LOAD)
        page.set_default_navigation_timeout(config.Timeouts.PAGE_LOAD)

        try:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    auth = Auth(page)
                    auth.authentication(page)
                    break
                except Exception as e:
                    err_str = str(e)
                    if any(net_err in err_str for net_err in _NETWORK_ERRORS):
                        if attempt < _MAX_RETRIES:
                            logger.warning(f"Сетевая ошибка (попытка {attempt}/{_MAX_RETRIES}): {err_str.splitlines()[0]}. Повтор через {_RETRY_DELAY}с...")
                            time.sleep(_RETRY_DELAY)
                            continue
                        else:
                            logger.error(f"Сеть недоступна после {_MAX_RETRIES} попыток. Проверьте интернет-соединение.")
                            raise
                    raise

            auto_response = AutoResponsePage(page)
            auto_response.auto_response()

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


if __name__ == "__main__":
    run()
