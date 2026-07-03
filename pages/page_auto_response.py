import json
import logging
import time
import random
import urllib.parse
from pathlib import Path
from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from pages.base_page import BasePage
from pages.locators_page import AutoResponse
from config import config

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hh.ru/search/vacancy"
APPLIED_FILE = "applied_vacancies.json"
MANUAL_REVIEW_FILE = "manual_review.json"
COVER_LETTER_FILE = "cover_letter.txt"


def _load_cover_letter() -> str:
    try:
        return Path(COVER_LETTER_FILE).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning(f"{COVER_LETTER_FILE} не найден — сопроводительное письмо не будет добавлено")
        return ""


COVER_LETTER = _load_cover_letter()


class AutoResponsePage(BasePage):

    def __init__(self, page: Page):
        super().__init__(page)
        self.locator = AutoResponse(page)

    def auto_response(self) -> None:
        applied = self._load_applied()
        manual_review = self._load_manual_review()
        total_count = 0
        query_stats: dict = {}

        for query in config.SearchConfig.QUERIES:
            stats = {"responses": 0, "skipped_applied": 0, "manual_review": 0, "errors": 0}
            query_stats[query] = stats

            logger.info(
                f"=== Запрос: '{query}' | "
                f"Регион: {config.SearchConfig.AREA} | "
                f"Опыт: {config.SearchConfig.EXPERIENCE} ==="
            )

            if not self._check_auth():
                logger.error("Сессия истекла — завершаем работу")
                break

            count_page = 0
            exp_params = "&".join(f"experience={e}" for e in config.SearchConfig.EXPERIENCE)

            try:
                while count_page < config.SearchConfig.MAX_PAGES:
                    url = (
                        f"{SEARCH_URL}"
                        f"?text={urllib.parse.quote(query)}"
                        f"&area={config.SearchConfig.AREA}"
                        f"&{exp_params}"
                        f"&order_by=relevance"
                        f"&page={count_page}"
                    )
                    self.page.goto(url)
                    self.page.wait_for_load_state("domcontentloaded")
                    time.sleep(0.5)

                    if self.locator.button_response.count() == 0:
                        logger.info(f"  Пустая страница {count_page + 1}, переходим к следующему запросу.")
                        break

                    search_page_url = self.page.url
                    skip_count = 0

                    while True:
                        total = self.locator.button_response.count()
                        if total == 0 or skip_count >= total or skip_count >= 100:
                            break

                        btn = self.locator.button_response.nth(skip_count)

                        if not btn.is_visible():
                            skip_count += 1
                            continue

                        # Дедупликация — быстрая проверка без задержки
                        vacancy_url = self._get_vacancy_url(btn)
                        if vacancy_url and vacancy_url in applied:
                            logger.debug(f"  Пропуск (уже откликались): {vacancy_url}")
                            stats["skipped_applied"] += 1
                            skip_count += 1
                            continue

                        # Задержка только перед реальным кликом
                        time.sleep(random.uniform(1.5, 3.5))

                        if self.locator.open_chat.is_visible():
                            self.click(self.locator.close_chat_button)

                        # Закрываем висячий модал перед кликом на кнопку вакансии
                        try:
                            if self.page.locator("[data-qa='modal-overlay']").is_visible():
                                logger.warning("Обнаружен висячий модал — закрываем перед кликом")
                                if self.locator.modal_close_button.is_visible():
                                    self.locator.modal_close_button.click()
                                else:
                                    self.page.keyboard.press("Escape")
                                time.sleep(0.5)
                        except Exception:
                            pass

                        url_before_click = self.page.url
                        try:
                            self.scroll_and_wait(btn)
                            self.click(btn)
                        except PlaywrightTimeoutError:
                            logger.warning(f"Таймаут клика на вакансию (nth={skip_count}) — пропускаем")
                            skip_count += 1
                            continue

                        # Пауза для открытия модала или срабатывания редиректа
                        time.sleep(0.8)

                        # Лимит — проверяем ДО URL-сравнения: работает при любом URL
                        if self._is_response_limit_reached():
                            self._handle_limit_reached(applied, manual_review, query_stats, total_count)
                            return

                        if self.locator.response_out_of_Russia.is_visible():
                            self.click(self.locator.response_permanent_button)
                            time.sleep(0.3)

                        if self.locator.text_response_to_go_to_page_out_hh.is_visible():
                            self.click(self.locator.cancel_button_for_response_to_go_to_page_out_hh)
                            skip_count += 1
                            continue

                        if self.page.url == url_before_click:
                            response_sent = False
                            try:
                                # Лимит 200 откликов за 24ч — проверяем прямо в модале
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count)
                                    return

                                # Вакансия требует ответа на вопросы работодателя
                                if self.locator.heading_response_answer_question.is_visible():
                                    logger.info(f"  Вопросы работодателя (ручной отклик): {vacancy_url}")
                                    if vacancy_url:
                                        manual_review.add(vacancy_url)
                                        self._save_manual_review(manual_review)
                                    stats["manual_review"] += 1
                                    if self.locator.modal_close_button.is_visible():
                                        self.locator.modal_close_button.click()
                                    time.sleep(0.3)
                                    skip_count += 1
                                    continue

                                if self.locator.modal_window_drop_base.is_visible():
                                    if self.locator.modal_window_drop_base_resume_auto.is_visible():
                                        self.click(self.locator.modal_window_drop_base_resume_auto)
                                    else:
                                        first_opt = self.locator.modal_window_drop_base.locator("[data-qa='cell']").first
                                        if first_opt.is_visible():
                                            self.click(first_opt)
                                    time.sleep(0.5)

                                if self.locator.button_add_cover_letter.is_visible():
                                    self.click(self.locator.button_add_cover_letter)
                                    time.sleep(0.5)
                                if COVER_LETTER and self.locator.textbox_cover_letter.is_visible():
                                    self.fill(self.locator.textbox_cover_letter, COVER_LETTER)

                                # Повторная проверка лимита — баннер мог появиться асинхронно
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count)
                                    return

                                self.click(self.locator.modal_window_button_response)
                                time.sleep(0.5)

                                # Лимит как ответ сервера на клик submit — сразу после отправки
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count)
                                    return

                                total_count += 1
                                stats["responses"] += 1
                                response_sent = True

                                # Ждём пока модал закроется, иначе overlay заблокирует следующий клик
                                try:
                                    self.page.locator("[data-qa='modal-overlay']").wait_for(
                                        state="hidden", timeout=1500
                                    )
                                except PlaywrightTimeoutError:
                                    try:
                                        self.page.keyboard.press("Escape")
                                        time.sleep(0.3)
                                    except Exception:
                                        pass

                                if vacancy_url:
                                    applied.add(vacancy_url)
                                if total_count % 5 == 0:
                                    self._save_applied(applied)

                                logger.info(
                                    f"Отклик #{total_count} отправлен "
                                    f"('{query}', стр. {count_page + 1})"
                                )

                            except Exception as modal_err:
                                # Лимит мог проявиться внутри модала
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count)
                                    return
                                logger.warning(f"Ошибка модала: {modal_err}. Пропускаем.")
                                stats["errors"] += 1
                                try:
                                    if self.locator.modal_close_button.is_visible():
                                        self.locator.modal_close_button.click()
                                        time.sleep(0.3)
                                except Exception:
                                    pass
                                if not response_sent:
                                    skip_count += 1

                            # Проверка тоста вне try/except — чисто информационная
                            if response_sent:
                                try:
                                    self.locator.status.wait_for(state="visible", timeout=2000)
                                except PlaywrightTimeoutError:
                                    logger.warning("Тост 'Отклик отправлен' не появился")
                        else:
                            # Редирект на внешний сайт — возвращаемся и обновляем URL
                            search_page_url = self._safe_return_to_search(search_page_url)
                            skip_count += 1

                    if not self.locator.pagination_next.is_visible():
                        logger.info(
                            f"  Запрос '{query}' завершён: "
                            f"стр. {count_page + 1}, откликов {stats['responses']}"
                        )
                        break

                    count_page += 1
                    logger.info(f"  Страница {count_page + 1} | Всего откликов: {total_count}")

            except PlaywrightError as e:
                if "closed" in str(e).lower():
                    logger.warning(f"Браузер закрылся неожиданно. Откликов отправлено: {total_count}")
                    self._save_applied(applied)
                    self._save_manual_review(manual_review)
                    self._log_final_stats(query_stats, total_count)
                    return
                raise

        self._save_applied(applied)
        self._save_manual_review(manual_review)
        self._log_final_stats(query_stats, total_count)

    def _check_auth(self) -> bool:
        try:
            return not self.page.locator("[data-qa='login']").is_visible(timeout=500)
        except Exception:
            return True

    def _get_vacancy_url(self, btn) -> str | None:
        try:
            return btn.evaluate("""el => {
                const card = el.closest('[data-qa="vacancy-serp__vacancy"]')
                          || el.closest('[class*="vacancy-serp-item"]');
                if (!card) return null;
                const link = card.querySelector('a[data-qa="serp-item__title"]')
                          || card.querySelector('a[href*="/vacancy/"]');
                if (!link) return null;
                const url = link.href.split('?')[0];
                return url.replace(/^https?:\\/\\/[a-z-]+\\.hh\\.ru/, 'https://hh.ru');
            }""")
        except Exception:
            return None

    def _load_applied(self) -> set:
        try:
            with open(APPLIED_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_applied(self, applied: set) -> None:
        with open(APPLIED_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(applied), f, ensure_ascii=False, indent=2)

    def _load_manual_review(self) -> set:
        try:
            with open(MANUAL_REVIEW_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_manual_review(self, manual_review: set) -> None:
        if not manual_review:
            return
        with open(MANUAL_REVIEW_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(manual_review), f, ensure_ascii=False, indent=2)

    def _safe_return_to_search(self, search_page_url: str) -> str:
        self.page.goto(search_page_url)
        self.page.wait_for_load_state("domcontentloaded")
        time.sleep(0.5)
        return self.page.url

    def _is_response_limit_reached(self) -> bool:
        # Способ 1: XPath + wait_for(attached) — полингует DOM до 500мс
        try:
            self.page.locator(
                "xpath=//*[@data-qa-popup-error-code='negotiations-limit-exceeded']"
            ).wait_for(state="attached", timeout=500)
            logger.warning("ЛИМИТ ОБНАРУЖЕН: XPath locator (основной фрейм)")
            return True
        except Exception:
            pass

        # Способ 2: pierce/ — пробивает Shadow DOM если используется
        try:
            self.page.locator(
                "pierce/[data-qa-popup-error-code='negotiations-limit-exceeded']"
            ).wait_for(state="attached", timeout=200)
            logger.warning("ЛИМИТ ОБНАРУЖЕН: pierce locator (shadow DOM)")
            return True
        except Exception:
            pass

        # Способ 3: поиск во ВСЕХ фреймах страницы (включая iframe)
        try:
            for frame in self.page.frames:
                try:
                    found = frame.evaluate(
                        'document.documentElement.innerHTML.includes("negotiations-limit-exceeded")'
                    )
                    if found:
                        logger.warning(f"ЛИМИТ ОБНАРУЖЕН: innerHTML в frame url={frame.url!r}")
                        return True
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"_is_response_limit_reached frames ошибка: {e}")

        # Способ 4: get_by_text — поиск по видимому тексту
        try:
            if self.page.get_by_text("исчерпали лимит", exact=False).count() > 0:
                logger.warning("ЛИМИТ ОБНАРУЖЕН: get_by_text")
                return True
        except Exception:
            pass

        return False

    def _handle_limit_reached(self, applied: set, manual_review: set, query_stats: dict, total_count: int) -> None:
        self._save_applied(applied)
        self._save_manual_review(manual_review)
        self._log_final_stats(query_stats, total_count)
        logger.info("=" * 55)
        logger.info("  ЛИМИТ ОТКЛИКОВ HH.RU ИСЧЕРПАН")
        logger.info(f"  Откликов отправлено: {total_count}")
        logger.info("  В течение 24 часов не более 200 откликов.")
        logger.info("  Попробуйте запустить программу позднее.")
        logger.info("=" * 55)

    def _log_final_stats(self, query_stats: dict, total_count: int) -> None:
        logger.info("=== Итоговая статистика ===")
        for query, stats in query_stats.items():
            logger.info(
                f"  '{query}': {stats['responses']} откликов | "
                f"{stats['skipped_applied']} пропущено | "
                f"{stats['manual_review']} с вопросами | "
                f"{stats['errors']} ошибок"
            )
        logger.info(f"Итого откликов: {total_count}")
