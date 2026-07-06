import logging
import re
import time
from datetime import datetime

from playwright.sync_api import Page

from utils import db

logger = logging.getLogger(__name__)

_NEGOTIATIONS_URL = "https://hh.ru/applicant/negotiations"

_TABS = [
    ("tab_filter_invitation", "Приглашение"),
    ("tab_filter_interview",  "Собеседование"),
    ("tab_filter_hired",      "Выход на работу"),
    ("tab_filter_awaiting",   "Ожидание"),
    ("tab_filter_discard",    "Отказ"),
]


def check_and_save_negotiations(page: Page) -> list[dict]:
    logger.info("Проверка статусов откликов...")
    try:
        page.goto(_NEGOTIATIONS_URL)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1.5)
    except Exception as e:
        logger.warning(f"Не удалось открыть страницу откликов: {e}")
        return []

    negotiations = []

    for tab_qa, status_label in _TABS:
        count = _get_tab_count(page, tab_qa)
        if count == 0:
            logger.debug(f"  Вкладка '{status_label}': пусто, пропускаем")
            continue

        logger.info(f"  Вкладка '{status_label}': {count} откликов")
        items = _parse_tab(page, tab_qa, status_label)
        negotiations.extend(items)

    if not negotiations:
        logger.info("  Активных откликов не найдено (все вкладки пусты)")
        return []

    logger.info(f"  Всего откликов обработано: {len(negotiations)}")
    changes = db.save_negotiations(negotiations)
    _log_changes(changes)
    _log_summary(negotiations)
    return changes


def _get_tab_count(page: Page, tab_qa: str) -> int:
    try:
        wrapper = page.locator(f"[data-qa='wrapper-{tab_qa}']")
        if wrapper.count() == 0:
            tab = page.locator(f"[data-qa='{tab_qa}']")
            if tab.count() == 0:
                return 0
            aria = tab.get_attribute("aria-label") or ""
            digits = re.search(r"\d+", aria)
            return int(digits.group()) if digits else 0
        postfix = wrapper.locator("[data-qa='tab-postfix']")
        if postfix.count() == 0:
            return 0
        text = postfix.text_content().strip()
        digits = re.search(r"\d+", text)
        return int(digits.group()) if digits else 0
    except Exception:
        return 0


def _parse_tab(page: Page, tab_qa: str, status_label: str) -> list[dict]:
    try:
        tab = page.locator(f"[data-qa='{tab_qa}']")
        if tab.count() == 0:
            return []
        tab.click()
        time.sleep(1.2)
    except Exception as e:
        logger.debug(f"  Не удалось переключить вкладку {tab_qa}: {e}")
        return []

    return _parse_items(page, status_label)


def _parse_items(page: Page, status_label: str) -> list[dict]:
    items = []
    checked_at = datetime.now().isoformat(timespec="seconds")

    try:
        cards = page.locator("[data-qa='negotiations-item']")
        total = cards.count()

        for i in range(total):
            card = cards.nth(i)
            try:
                vac_link = card.locator("a[href*='/vacancy/']").first
                if vac_link.count() == 0:
                    continue
                href = vac_link.get_attribute("href") or ""
                vacancy_url = _normalize_url(href)
                if not vacancy_url:
                    continue

                title = ""
                title_el = card.locator("[data-qa='negotiations-item-vacancy']")
                if title_el.count() > 0:
                    title = title_el.text_content().strip()

                company = ""
                company_el = card.locator("[data-qa='negotiations-item-company']")
                if company_el.count() > 0:
                    company = company_el.text_content().strip()

                items.append({
                    "url": vacancy_url,
                    "title": title,
                    "company": company,
                    "status": status_label,
                    "checked_at": checked_at,
                })
            except Exception as e:
                logger.debug(f"  Ошибка парсинга карточки {i}: {e}")

    except Exception as e:
        logger.debug(f"  Ошибка при обходе карточек: {e}")

    return items


def _normalize_url(href: str) -> str | None:
    if not href or "/vacancy/" not in href:
        return None
    url = href.split("?")[0]
    if url.startswith("/"):
        url = "https://hh.ru" + url
    url = url.replace("http://", "https://")
    url = re.sub(r"https://[a-z-]+\.hh\.ru", "https://hh.ru", url)
    return url


def _log_changes(changes: list[dict]) -> None:
    if not changes:
        return
    logger.info(f"  Изменений статусов: {len(changes)}")
    for ch in changes:
        logger.info(
            f"  ★ {ch['company'] or '?'} | {ch['title'] or '?'} | "
            f"{ch['old_status']} → {ch['new_status']}"
        )


def _log_summary(negotiations: list[dict]) -> None:
    from collections import Counter
    counts = Counter(n["status"] for n in negotiations)
    parts = ", ".join(f"{s}: {c}" for s, c in sorted(counts.items()))
    logger.info(f"  Итого по статусам: {parts}")
