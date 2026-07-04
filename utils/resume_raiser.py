"""
п.1: Автоматическое поднятие резюме в поиске HH.ru.
HH.ru разрешает поднятие раз в 4 часа.
"""
import logging
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

_RESUMES_URL = "https://hh.ru/applicant/resumes"


def raise_all_resumes(page: Page) -> int:
    """
    Переходит на страницу резюме и нажимает «Поднять в поиске» для каждого
    доступного резюме. Возвращает количество поднятых резюме.

    Кнопка поднятия — <a data-qa="resume-update-button"> (не <button>).
    Может быть несколько резюме — нажимаем на каждое.
    """
    logger.info("Поднятие резюме в поиске...")
    try:
        page.goto(_RESUMES_URL)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1.5)
    except Exception as e:
        logger.warning(f"Не удалось открыть страницу резюме: {e}")
        return 0

    raised = 0

    # Основной локатор: <a data-qa="resume-update-button"> (подтверждён из DOM)
    try:
        buttons = page.locator("[data-qa='resume-update-button']")
        count = buttons.count()
        logger.debug(f"  Найдено кнопок поднятия: {count}")

        for i in range(count):
            btn = buttons.nth(i)
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(1.2)
                    raised += 1
                    logger.info(f"  Резюме #{raised} поднято в поиске")
            except PlaywrightTimeoutError:
                pass
            except Exception as e:
                logger.debug(f"  Не удалось нажать кнопку поднятия: {e}")
    except Exception as e:
        logger.debug(f"  Ошибка поиска кнопок поднятия: {e}")

    if raised == 0:
        logger.info("  Резюме не подняты: кнопки недоступны или интервал ещё не истёк (4 ч)")
    else:
        logger.info(f"Поднято резюме: {raised}")

    return raised
