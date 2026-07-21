import csv
import logging
import random
import time
import urllib.parse
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from config import config
from pages.base_page import BasePage
from pages.locators_page import AutoResponse
from utils import db, question_filler

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hh.ru/search/vacancy"
COVER_LETTER_FILE = "cover_letter.txt"


def _load_cover_letters() -> dict[str, str]:
    templates: dict[str, str] = {}
    cover_dir = Path(config.ResumeConfig.COVER_LETTER_DIR)
    if cover_dir.is_dir():
        for f in sorted(cover_dir.glob("*.txt")):
            templates[f.stem] = f.read_text(encoding="utf-8").strip()
    if not templates.get("default"):
        legacy = Path(COVER_LETTER_FILE)
        if legacy.exists():
            templates["default"] = legacy.read_text(encoding="utf-8").strip()
    return templates

_MONTHS_RU = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
    'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
    'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
}


class AutoResponsePage(BasePage):

    def __init__(self, page: Page):
        super().__init__(page)
        self.locator = AutoResponse(page)
        db.init_db()
        self._cover_letters = _load_cover_letters()
        if self._cover_letters:
            names = ", ".join(self._cover_letters.keys())
            logger.info(f"Шаблоны писем загружены: {names}")

    def auto_response(self, dry_run: bool = False, queries=None, worker_id: int = 0, workers: int = 1) -> None:
        if queries is None:
            queries = config.SearchConfig.QUERIES
        if dry_run:
            logger.info("★ DRY-RUN режим: отклики НЕ отправляются")

        applied = self._load_applied()
        applied = self._expire_applied(applied)

        manual_review = self._load_manual_review()
        total_count = 0
        query_stats: dict = {}
        company_counts: dict = {}
        started_at = datetime.now()

        api_map: dict = {}
        if config.BotConfig.USE_API_PREFILTER:
            api_map = self._fetch_api_map()

        for query in queries:
            stats = {"responses": 0, "skipped_applied": 0, "skipped_fresh": 0,
                     "skipped_company": 0, "skipped_prefilter": 0,
                     "manual_review": 0, "errors": 0, "redirects": 0, "ai_answered": 0}
            query_stats[query] = stats

            logger.info(
                f"=== Запрос: '{query}' | "
                f"Регион: {','.join(config.SearchConfig.AREAS)} | "
                f"Опыт: {config.SearchConfig.EXPERIENCE} ==="
            )

            if not self._check_auth():
                logger.error("Сессия истекла — завершаем работу")
                break

            if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                logger.info(
                    f"Достигнут лимит сессии: {config.BotConfig.MAX_RESPONSES_PER_RUN} откликов. "
                    f"Завершаем работу."
                )
                break

            count_page = worker_id if workers > 1 else 0
            page_step = workers if workers > 1 else 1
            exp_params = "&".join(f"experience={e}" for e in config.SearchConfig.EXPERIENCE)

            try:
                while count_page < config.SearchConfig.MAX_PAGES:
                    if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                        break

                    url = (
                        f"{SEARCH_URL}"
                        f"?text={urllib.parse.quote(query)}"
                        f"&{'&'.join(f'area={a}' for a in config.SearchConfig.AREAS)}"
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
                        if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                            logger.info(f"Достигнут лимит сессии: {config.BotConfig.MAX_RESPONSES_PER_RUN}")
                            break

                        total = self.locator.button_response.count()
                        if total == 0 or skip_count >= total or skip_count >= 100:
                            break

                        btn = self.locator.button_response.nth(skip_count)

                        if not btn.is_visible():
                            skip_count += 1
                            continue

                        vacancy_url = self._get_vacancy_url(btn)
                        title, company, date_text = self._get_vacancy_info(btn)

                        if vacancy_url and (
                            vacancy_url in applied or db.is_already_applied(vacancy_url)
                        ):
                            logger.debug(f"  Пропуск (уже откликались): {vacancy_url}")
                            stats["skipped_applied"] += 1
                            skip_count += 1
                            continue

                        if company and title and self._is_fuzzy_duplicate(company, title, applied):
                            logger.debug(f"  Пропуск (fuzzy-дубль): {company} | {title}")
                            stats["skipped_applied"] += 1
                            skip_count += 1
                            continue

                        if api_map and vacancy_url and vacancy_url not in api_map:
                            logger.debug(f"  Пропуск (не прошла API-фильтр): {vacancy_url}")
                            stats["skipped_prefilter"] += 1
                            skip_count += 1
                            continue

                        if config.BotConfig.FRESHNESS_DAYS > 0 and not self._is_vacancy_fresh(date_text):
                            logger.debug(f"  Пропуск (устарела, '{date_text}'): {title or vacancy_url}")
                            stats["skipped_fresh"] += 1
                            skip_count += 1
                            continue

                        if company and not self._is_company_allowed(company, company_counts):
                            logger.debug(f"  Пропуск (компания): {company}")
                            stats["skipped_company"] += 1
                            skip_count += 1
                            continue

                        time.sleep(random.uniform(config.BotConfig.DELAY_MIN, config.BotConfig.DELAY_MAX))

                        if self.locator.open_chat.is_visible():
                            self.click(self.locator.close_chat_button)

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

                        time.sleep(config.BotConfig.DELAY_AFTER_MODAL)

                        if self._is_response_limit_reached():
                            self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                            return

                        if self.locator.response_out_of_Russia.is_visible():
                            self.click(self.locator.response_permanent_button)
                            time.sleep(0.3)

                        if self.locator.text_response_to_go_to_page_out_hh.is_visible():
                            self.click(self.locator.cancel_button_for_response_to_go_to_page_out_hh)
                            stats["redirects"] += 1
                            skip_count += 1
                            continue

                        if self.page.url == url_before_click:
                            response_sent = False
                            try:
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                                    return

                                if self.locator.heading_response_answer_question.is_visible():
                                    ai_answered = False
                                    try:
                                        ai_answered = question_filler.try_ai_answer(self.page, vacancy_url)
                                    except Exception as ai_exc:
                                        logger.warning(f"  Ошибка ИИ-ответа на вопросы: {ai_exc}")
                                        ai_answered = False

                                    if dry_run and ai_answered:
                                        logger.info(
                                            f"  [DRY-RUN] ИИ заполнил бы вопросы для {vacancy_url} — "
                                            "отклик НЕ отправляется, проверьте ai_answers в БД"
                                        )

                                    if ai_answered:
                                        stats["ai_answered"] = stats.get("ai_answered", 0) + 1
                                        logger.info(f"  Вопросы работодателя — отвечено ИИ: {vacancy_url}")
                                        # не continue — обычный flow ниже дозаполнит письмо/резюме и отправит отклик
                                    else:
                                        logger.info(f"  Вопросы работодателя (ручной отклик): {vacancy_url}")
                                        if vacancy_url:
                                            manual_review.append({'url': vacancy_url, 'title': title or '', 'company': company or ''})
                                            self._save_manual_review(manual_review)
                                        stats["manual_review"] += 1
                                        if self.locator.modal_close_button.is_visible():
                                            self.locator.modal_close_button.click()
                                        time.sleep(0.3)
                                        skip_count += 1
                                        continue

                                if self.locator.modal_window_drop_base.is_visible():
                                    self._select_resume_in_modal(title)
                                    time.sleep(0.5)

                                if self.locator.button_add_cover_letter.is_visible():
                                    self.click(self.locator.button_add_cover_letter)
                                    time.sleep(0.5)
                                cover_tpl = self._get_cover_letter_for(title)
                                if cover_tpl and self.locator.textbox_cover_letter.is_visible():
                                    letter = self._format_cover_letter(cover_tpl, title, company)
                                    self.fill(self.locator.textbox_cover_letter, letter)

                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                                    return

                                if dry_run:
                                    score = self._get_vacancy_score(vacancy_url, api_map)
                                    score_str = f" [скор:{score}]" if score else ""
                                    logger.info(
                                        f"[DRY-RUN] #{total_count + 1} | {title or '?'} | "
                                        f"{company or '?'}{score_str} ('{query}', стр.{count_page + 1})"
                                    )
                                    total_count += 1
                                    stats["responses"] += 1
                                    response_sent = True
                                    if self.locator.modal_close_button.is_visible():
                                        self.locator.modal_close_button.click()
                                    time.sleep(0.3)
                                else:
                                    btn_submit = self.locator.modal_window_button_response
                                    if btn_submit.count() == 0:
                                        logger.warning(f"  Кнопка «Откликнуться» отсутствует в DOM — пропускаем '{title}'")
                                        if self.locator.modal_close_button.is_visible():
                                            self.locator.modal_close_button.click()
                                        skip_count += 1
                                        continue
                                    clicked = False
                                    try:
                                        btn_submit.evaluate("el => el.click()")
                                        logger.info(f"  JS-клик «Откликнуться» — '{title}'")
                                        clicked = True
                                    except Exception as e:
                                        logger.warning(f"  JS-клик не сработал: {e}")
                                    if not clicked:
                                        try:
                                            btn_submit.click(force=True, timeout=8000)
                                            logger.info(f"  force-клик «Откликнуться» — '{title}'")
                                            clicked = True
                                        except Exception as e:
                                            logger.warning(f"  Таймаут клика «Откликнуться» — пропускаем '{title}'")
                                            if self.locator.modal_close_button.is_visible():
                                                self.locator.modal_close_button.click()
                                            skip_count += 1
                                            continue
                                    time.sleep(0.5)

                                    if self._is_response_limit_reached():
                                        self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                                        return

                                    total_count += 1
                                    stats["responses"] += 1
                                    response_sent = True

                                    company_key = company.strip().lower() if company else ''
                                    if company_key:
                                        company_counts[company_key] = company_counts.get(company_key, 0) + 1

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
                                        applied[vacancy_url] = {
                                            "title": title,
                                            "company": company,
                                            "applied_at": datetime.now().isoformat(timespec="seconds"),
                                            "query": query,
                                        }
                                    n = config.BotConfig.SAVE_EVERY_N
                                    if n <= 1 or total_count % n == 0:
                                        self._save_applied(applied)

                                    score = self._get_vacancy_score(vacancy_url, api_map)
                                    score_str = f" [скор:{score}]" if score and config.BotConfig.USE_SCORING else ""
                                    logger.info(
                                        f"Отклик #{total_count} | {title or '?'} | "
                                        f"{company or '?'}{score_str} ('{query}', стр.{count_page + 1})"
                                    )

                                    if config.BotConfig.LOG_RESPONSES_CSV and vacancy_url:
                                        self._log_response_csv(vacancy_url, title, company, query)

                                if dry_run and company:
                                    company_key = company.strip().lower()
                                    company_counts[company_key] = company_counts.get(company_key, 0) + 1

                            except Exception as modal_err:
                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
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

                            if response_sent:
                                try:
                                    self.locator.status.wait_for(state="visible", timeout=2000)
                                except PlaywrightTimeoutError:
                                    logger.warning("Тост 'Отклик отправлен' не появился")
                        else:
                            # Полный переход на отдельную страницу с вопросами (не модалка).
                            # Та же разметка task-body/task-question, что и в модалке —
                            # пробуем ответить ИИ и здесь, прежде чем сдаваться в manual_review.
                            ai_answered = False
                            try:
                                ai_answered = question_filler.try_ai_answer(self.page, vacancy_url)
                            except Exception as ai_exc:
                                logger.warning(f"  Ошибка ИИ-ответа на вопросы (отдельная страница): {ai_exc}")
                                ai_answered = False

                            if not ai_answered:
                                stats["redirects"] += 1
                                logger.info(f"  Редирект (вопросы): {title or vacancy_url} | {company or ''}")
                                manual_review.append({'url': vacancy_url, 'title': title or '', 'company': company or ''})
                                self._save_manual_review(manual_review)
                                search_page_url = self._safe_return_to_search(search_page_url)
                                skip_count += 1
                            else:
                                stats["ai_answered"] = stats.get("ai_answered", 0) + 1
                                logger.info(f"  Вопросы работодателя (отдельная страница) — отвечено ИИ: {vacancy_url}")

                                if self.locator.button_add_cover_letter.is_visible():
                                    self.click(self.locator.button_add_cover_letter)
                                    time.sleep(0.5)
                                cover_tpl = self._get_cover_letter_for(title)
                                if cover_tpl and self.locator.textbox_cover_letter.is_visible():
                                    letter = self._format_cover_letter(cover_tpl, title, company)
                                    self.fill(self.locator.textbox_cover_letter, letter)

                                if self._is_response_limit_reached():
                                    self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                                    return

                                if dry_run:
                                    logger.info(
                                        f"  [DRY-RUN] ИИ заполнил бы вопросы (отдельная страница) для "
                                        f"{title or vacancy_url} — отклик НЕ отправляется"
                                    )
                                    total_count += 1
                                    stats["responses"] += 1
                                    search_page_url = self._safe_return_to_search(search_page_url)
                                else:
                                    btn_submit = self.locator.modal_window_button_response
                                    clicked = False
                                    if btn_submit.count() > 0:
                                        try:
                                            btn_submit.click(timeout=8000)
                                            clicked = True
                                        except Exception as e:
                                            logger.warning(f"  Клик «Откликнуться» не сработал (отдельная страница): {e}")
                                    else:
                                        logger.warning(f"  Кнопка «Откликнуться» отсутствует на странице вопросов — пропускаем '{title}'")

                                    if clicked:
                                        time.sleep(0.5)
                                        total_count += 1
                                        stats["responses"] += 1
                                        company_key = company.strip().lower() if company else ''
                                        if company_key:
                                            company_counts[company_key] = company_counts.get(company_key, 0) + 1
                                        if vacancy_url:
                                            applied[vacancy_url] = {
                                                "title": title,
                                                "company": company,
                                                "applied_at": datetime.now().isoformat(timespec="seconds"),
                                                "query": query,
                                            }
                                        n = config.BotConfig.SAVE_EVERY_N
                                        if n <= 1 or total_count % n == 0:
                                            self._save_applied(applied)
                                        if config.BotConfig.LOG_RESPONSES_CSV and vacancy_url:
                                            self._log_response_csv(vacancy_url, title, company, query)
                                        logger.info(
                                            f"Отклик #{total_count} | {title or '?'} | {company or '?'} "
                                            f"('{query}', стр.{count_page + 1})"
                                        )
                                    else:
                                        skip_count += 1
                                    search_page_url = self._safe_return_to_search(search_page_url)

                    if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                        break

                    if not self.locator.pagination_next.is_visible():
                        logger.info(
                            f"  Запрос '{query}' завершён: "
                            f"стр. {count_page + 1}, откликов {stats['responses']}"
                        )
                        break

                    count_page += page_step
                    logger.info(f"  Страница {count_page + 1} | Всего откликов: {total_count}")

            except PlaywrightError as e:
                if "closed" in str(e).lower():
                    logger.warning(f"Браузер закрылся неожиданно. Откликов отправлено: {total_count}")
                    self._save_applied(applied)
                    self._save_manual_review(manual_review)
                    self._log_final_stats(query_stats, total_count, started_at, company_counts)
                    return
                raise

        self._save_applied(applied)
        self._save_manual_review(manual_review)
        self._log_final_stats(query_stats, total_count, started_at, company_counts)

    def _is_fuzzy_duplicate(self, company: str, title: str, applied: dict) -> bool:
        company_low = company.lower()
        title_low = title.lower()
        for meta in applied.values():
            if meta.get("company", "").lower() != company_low:
                continue
            ratio = SequenceMatcher(None, title_low, meta.get("title", "").lower()).ratio()
            if ratio >= 0.85:
                return True
        return False

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

    def _get_vacancy_info(self, btn) -> tuple[str, str, str]:
        try:
            info = btn.evaluate("""el => {
                const card = el.closest('[data-qa="vacancy-serp__vacancy"]')
                          || el.closest('[class*="vacancy-serp-item"]');
                if (!card) return {title: '', company: '', date: ''};
                const titleEl = card.querySelector('a[data-qa="serp-item__title"]');
                const companyEl = card.querySelector('[data-qa="vacancy-serp__vacancy-employer-text"]')
                               || card.querySelector('[data-qa="vacancy-serp__vacancy-employer"]')
                               || card.querySelector('a[data-qa*="employer"]');
                const dateEl = card.querySelector('[data-qa="vacancy-serp__vacancy-date"]')
                            || card.querySelector('time');
                return {
                    title:   titleEl   ? titleEl.textContent.trim()   : '',
                    company: companyEl ? companyEl.textContent.trim() : '',
                    date:    dateEl    ? (dateEl.getAttribute('datetime') || dateEl.textContent.trim()) : '',
                };
            }""")
            return info.get('title', ''), info.get('company', ''), info.get('date', '')
        except Exception:
            return '', '', ''

    def _is_vacancy_fresh(self, date_text: str) -> bool:
        if not date_text:
            return True
        text = date_text.lower().strip()
        today = datetime.now()

        try:
            dt = datetime.fromisoformat(date_text[:10])
            return (today - dt).days <= config.BotConfig.FRESHNESS_DAYS
        except (ValueError, TypeError):
            pass

        if any(w in text for w in ('сегодня', 'час', 'минут', 'только что')):
            return True
        if 'вчера' in text:
            return config.BotConfig.FRESHNESS_DAYS >= 1

        for month_name, month_num in _MONTHS_RU.items():
            if month_name in text:
                try:
                    day = int(text.split()[0])
                    year = today.year
                    dt = datetime(year, month_num, day)
                    if dt > today:
                        dt = datetime(year - 1, month_num, day)
                    return (today - dt).days <= config.BotConfig.FRESHNESS_DAYS
                except (ValueError, IndexError):
                    return True

        return True

    def _is_company_allowed(self, company: str, company_counts: dict) -> bool:
        company_lower = company.lower()

        for blacklisted in config.BlacklistConfig.COMPANIES:
            if blacklisted.lower() in company_lower:
                return False

        max_per = config.BotConfig.MAX_PER_COMPANY
        company_key = company.strip().lower() if company else ''
        if max_per > 0 and company_counts.get(company_key, 0) >= max_per:
            return False

        return True

    def _select_resume_in_modal(self, vacancy_title: str) -> None:
        title_lower = (vacancy_title or "").lower()
        selected_name = config.ResumeConfig.DEFAULT
        for keyword, resume_name in config.ResumeConfig.MATCH:
            if keyword in title_lower:
                selected_name = resume_name
                break

        drop_base = self.locator.modal_drop_base
        trigger   = self.locator.modal_resume_trigger

        if not drop_base.is_visible():
            try:
                current_text = self.locator.modal_window_drop_base.inner_text().strip()
            except Exception:
                current_text = ""
            logger.info(f"  Дропдаун закрыт, показано: '{current_text}'")

            if selected_name.lower() in current_text.lower() or not current_text:
                logger.info(f"  Резюме '{selected_name}' уже выбрано")
                return

            logger.info(f"  Нужно '{selected_name}', открываем дропдаун")
            if trigger.is_visible():
                trigger.click()
                time.sleep(0.3)

        if drop_base.is_visible():
            option = self.locator.resume_option_in_drop(selected_name)
            cnt = option.count()
            logger.info(f"  Вариантов '{selected_name}' в drop-base: {cnt}")
            if cnt > 0 and option.first.is_visible():
                option.first.click()
                logger.info(f"  Выбрано резюме: '{selected_name}'")
            else:
                first_opt = self.page.locator("[data-qa='drop-base'] [data-qa='cell']").first
                if first_opt.is_visible():
                    first_opt.click()
                    logger.info("  Fallback: выбрана первая опция из drop-base")
            time.sleep(0.2)

        if self.locator.modal_window_button_response.is_visible():
            return

        if drop_base.is_visible() and trigger.is_visible():
            trigger.click()
            logger.info("  Закрыли дропдаун кликом по trigger-карточке")
            time.sleep(0.2)

        if not self.locator.modal_window_button_response.is_visible():
            logger.warning("  Кнопка «Откликнуться» не видна после работы с дропдауном")

    def _get_cover_letter_for(self, title: str) -> str:
        title_lower = (title or "").lower()
        for keyword, tpl_name in config.ResumeConfig.COVER_LETTER_MATCH:
            if keyword in title_lower:
                tpl = self._cover_letters.get(tpl_name, "")
                if tpl:
                    logger.debug(f"  Шаблон письма: '{tpl_name}' (ключ: '{keyword}')")
                    return tpl
        return self._cover_letters.get("default", "")

    def _format_cover_letter(self, template: str, title: str, company: str) -> str:
        company_clause = f" в компании {company}" if company else ""
        variables = {
            "title": title or "данную вакансию",
            "company": company or "",
            "company_clause": company_clause,
            "name": config.Profile.NAME,
            "phone": config.Profile.PHONE,
            "email": config.Profile.EMAIL,
            "city": config.Profile.CITY,
            "years": config.Profile.YEARS_EXPERIENCE,
            "key_skills": config.Profile.KEY_SKILLS,
            "github": config.Profile.GITHUB,
            "portfolio": config.Profile.PORTFOLIO,
            "position": config.Profile.POSITION,
        }

        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        try:
            return template.format_map(_SafeDict(**variables))
        except Exception:
            return template

    def _fetch_api_map(self) -> dict:
        try:
            from utils.hh_api import fetch_all_queries
            logger.info("Предфильтрация через HH.ru API...")
            raw = fetch_all_queries(
                queries=config.SearchConfig.QUERIES,
                area=config.SearchConfig.AREA,
                experience=config.SearchConfig.EXPERIENCE,
                salary_min=config.BotConfig.SALARY_MIN or None,
                freshness_days=config.BotConfig.FRESHNESS_DAYS,
            )
            api_map = {}
            for vacancies in raw.values():
                for v in vacancies:
                    url = v.get("url", "")
                    if url:
                        v["score"] = self._compute_score(v)
                        api_map[url] = v
            logger.info(f"API предфильтр: {len(api_map)} вакансий прошли фильтрацию")
            return api_map
        except Exception as e:
            logger.warning(f"API предфильтр недоступен: {e}. Продолжаем без фильтра.")
            return {}

    def _compute_score(self, vacancy: dict) -> int:
        score = 0
        sal_from = vacancy.get("salary_from") or 0
        if sal_from:
            score += 2
            if sal_from >= 200_000:
                score += 4
            elif sal_from >= 150_000:
                score += 3
            elif sal_from >= 100_000:
                score += 1
        days_ago = vacancy.get("published_days_ago")
        if days_ago is not None:
            if days_ago == 0:
                score += 3
            elif days_ago <= 2:
                score += 2
            elif days_ago <= 7:
                score += 1
        return score

    def _get_vacancy_score(self, url: str | None, api_map: dict) -> int:
        if not url or not api_map:
            return 0
        return api_map.get(url, {}).get("score", 0)

    def _is_response_limit_reached(self) -> bool:
        try:
            self.page.locator(
                "xpath=//*[@data-qa-popup-error-code='negotiations-limit-exceeded']"
            ).wait_for(state="attached", timeout=500)
            logger.warning("ЛИМИТ ОБНАРУЖЕН: XPath locator (основной фрейм)")
            return True
        except Exception:
            pass

        try:
            self.page.locator(
                "pierce/[data-qa-popup-error-code='negotiations-limit-exceeded']"
            ).wait_for(state="attached", timeout=200)
            logger.warning("ЛИМИТ ОБНАРУЖЕН: pierce locator (shadow DOM)")
            return True
        except Exception:
            pass

        try:
            for frame in self.page.frames:
                try:
                    if frame.evaluate('document.documentElement.innerHTML.includes("negotiations-limit-exceeded")'):
                        logger.warning(f"ЛИМИТ ОБНАРУЖЕН: innerHTML в frame={frame.url!r}")
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self.page.get_by_text("исчерпали лимит", exact=False).count() > 0:
                logger.warning("ЛИМИТ ОБНАРУЖЕН: get_by_text")
                return True
        except Exception:
            pass

        return False

    def _handle_limit_reached(
        self, applied: dict, manual_review: list, query_stats: dict,
        total_count: int, started_at: datetime, company_counts: dict
    ) -> None:
        db.save_limit_reached(datetime.now())
        self._save_applied(applied)
        self._save_manual_review(manual_review)
        self._log_final_stats(query_stats, total_count, started_at, company_counts)
        sep = "=" * 55
        logger.info(sep)
        logger.info("  ЛИМИТ ОТКЛИКОВ HH.RU ИСЧЕРПАН")
        logger.info(f"  Откликов отправлено за сессию: {total_count}")
        logger.info("  В течение 24 часов не более 200 откликов.")
        logger.info("  Попробуйте запустить программу позднее.")
        logger.info(sep)

    def _load_applied(self) -> dict:
        return db.load_applied()

    def _save_applied(self, applied: dict) -> None:
        db.save_applied(applied)

    def _expire_applied(self, applied: dict) -> dict:
        days = config.BotConfig.APPLIED_EXPIRY_DAYS
        if not days:
            return applied
        cutoff = datetime.now() - timedelta(days=days)
        cutoff_iso = cutoff.isoformat(timespec="seconds")
        deleted = db.delete_expired_applied(cutoff_iso)
        if deleted:
            logger.info(f"Удалено {deleted} устаревших откликов из БД (старше {days} дней)")
        return {
            url: meta for url, meta in applied.items()
            if not meta.get("applied_at") or
               datetime.fromisoformat(meta["applied_at"]) >= cutoff
        }

    def _load_manual_review(self) -> list:
        return db.load_manual_review()

    def _save_manual_review(self, manual_review: list) -> None:
        db.save_manual_review(manual_review)

    def _safe_return_to_search(self, search_page_url: str) -> str:
        self.page.goto(search_page_url)
        self.page.wait_for_load_state("domcontentloaded")
        time.sleep(0.5)
        return self.page.url

    def _log_response_csv(self, url: str, title: str, company: str, query: str) -> None:
        db.log_response(url, title, company, query)
        log_file = Path(f"response_log_{datetime.now().strftime('%Y-%m-%d')}.csv")
        write_header = not log_file.exists()
        with open(log_file, 'a', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "query", "url", "title", "company"])
            w.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                query, url, title, company,
            ])

    def _log_final_stats(
        self, query_stats: dict, total_count: int,
        started_at: datetime, company_counts: dict
    ) -> None:
        elapsed = datetime.now() - started_at
        avg = elapsed.total_seconds() / total_count if total_count else 0

        sep = "=" * 60
        logger.info(sep)
        logger.info("         СТАТИСТИКА СЕССИИ AutoResponseHH")
        logger.info(sep)
        logger.info(f"  Время работы:            {elapsed}")
        logger.info(f"  Откликов отправлено:     {total_count}")
        if total_count:
            logger.info(f"  Среднее время/отклик:   {avg:.1f} сек")
        logger.info("")
        logger.info("  По поисковым запросам:")
        for query, st in query_stats.items():
            logger.info(f"    '{query}':")
            logger.info(f"      Откликов:             {st['responses']}")
            logger.info(f"      Уже откликались:      {st['skipped_applied']}")
            logger.info(f"      Устаревших:           {st.get('skipped_fresh', 0)}")
            logger.info(f"      Блэклист/компания:    {st.get('skipped_company', 0)}")
            if st.get('skipped_prefilter'):
                logger.info(f"      API-фильтр:           {st['skipped_prefilter']}")
            logger.info(f"      Ручной отклик:        {st['manual_review']}")
            if st.get('ai_answered'):
                logger.info(f"      Отвечено ИИ:          {st['ai_answered']}")
            logger.info(f"      Редиректов:           {st.get('redirects', 0)}")
            logger.info(f"      Ошибок:               {st['errors']}")

        if company_counts:
            logger.info("")
            logger.info("  Топ компаний (откликов за сессию):")
            top = sorted(company_counts.items(), key=lambda x: -x[1])[:10]
            for company, cnt in top:
                logger.info(f"      {cnt:>3}x  {company}")

        total_skipped = sum(
            st['skipped_applied'] + st.get('skipped_fresh', 0) + st.get('skipped_company', 0)
            for st in query_stats.values()
        )
        logger.info("")
        logger.info(f"  Итого откликов:          {total_count}")
        logger.info(f"  Итого пропущено:         {total_skipped}")
        if config.BotConfig.LOG_RESPONSES_CSV and total_count:
            csv_name = f"response_log_{started_at.strftime('%Y-%m-%d')}.csv"
            logger.info(f"  CSV-лог:                 {csv_name}")
        logger.info(sep)
