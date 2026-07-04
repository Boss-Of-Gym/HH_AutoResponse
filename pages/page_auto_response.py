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
from utils import db

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hh.ru/search/vacancy"
COVER_LETTER_FILE = "cover_letter.txt"  # legacy fallback


def _load_cover_letters() -> dict[str, str]:
    """п.3: загружает все шаблоны из cover_letters/ + legacy cover_letter.txt."""
    templates: dict[str, str] = {}
    cover_dir = Path(config.ResumeConfig.COVER_LETTER_DIR)
    if cover_dir.is_dir():
        for f in sorted(cover_dir.glob("*.txt")):
            templates[f.stem] = f.read_text(encoding="utf-8").strip()
    # Legacy fallback
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

    def auto_response(self, dry_run: bool = False) -> None:
        if dry_run:
            logger.info("★ DRY-RUN режим: отклики НЕ отправляются")

        applied = self._load_applied()
        applied = self._expire_applied(applied)

        manual_review = self._load_manual_review()
        total_count = 0
        query_stats: dict = {}
        company_counts: dict = {}
        started_at = datetime.now()

        # п.5: предфильтрация через HH.ru API
        api_map: dict = {}
        if config.BotConfig.USE_API_PREFILTER:
            api_map = self._fetch_api_map()

        for query in config.SearchConfig.QUERIES:
            stats = {"responses": 0, "skipped_applied": 0, "skipped_fresh": 0,
                     "skipped_company": 0, "skipped_prefilter": 0,
                     "manual_review": 0, "errors": 0, "redirects": 0}
            query_stats[query] = stats

            logger.info(
                f"=== Запрос: '{query}' | "
                f"Регион: {config.SearchConfig.AREA} | "
                f"Опыт: {config.SearchConfig.EXPERIENCE} ==="
            )

            if not self._check_auth():
                logger.error("Сессия истекла — завершаем работу")
                break

            # п.14: проверяем личный лимит откликов за сессию
            if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                logger.info(
                    f"Достигнут лимит сессии: {config.BotConfig.MAX_RESPONSES_PER_RUN} откликов. "
                    f"Завершаем работу."
                )
                break

            count_page = 0
            exp_params = "&".join(f"experience={e}" for e in config.SearchConfig.EXPERIENCE)

            try:
                while count_page < config.SearchConfig.MAX_PAGES:
                    # п.14: проверка лимита перед каждой страницей
                    if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                        break

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
                        # п.14: проверка лимита в inner-цикле
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

                        # Извлекаем метаданные вакансии ДО клика (п.8, п.9, п.16, п.17, п.18, п.19)
                        vacancy_url = self._get_vacancy_url(btn)
                        title, company, date_text = self._get_vacancy_info(btn)

                        # п.18: межзапросная дедупликация (applied — dict с URL-ключами)
                        if vacancy_url and vacancy_url in applied:
                            logger.debug(f"  Пропуск (уже откликались): {vacancy_url}")
                            stats["skipped_applied"] += 1
                            skip_count += 1
                            continue

                        # Fuzzy-дедупликация: компания+название, ловит репосты с новым URL
                        if company and title and self._is_fuzzy_duplicate(company, title, applied):
                            logger.debug(f"  Пропуск (fuzzy-дубль): {company} | {title}")
                            stats["skipped_applied"] += 1
                            skip_count += 1
                            continue

                        # п.5: предфильтрация — пропускаем вакансии не из API-выборки
                        if api_map and vacancy_url and vacancy_url not in api_map:
                            logger.debug(f"  Пропуск (не прошла API-фильтр): {vacancy_url}")
                            stats["skipped_prefilter"] += 1
                            skip_count += 1
                            continue

                        # п.16: фильтрация по свежести вакансии
                        if config.BotConfig.FRESHNESS_DAYS > 0 and not self._is_vacancy_fresh(date_text):
                            logger.debug(f"  Пропуск (устарела, '{date_text}'): {title or vacancy_url}")
                            stats["skipped_fresh"] += 1
                            skip_count += 1
                            continue

                        # п.17: блэклист компаний + п.5: лимит откликов на компанию
                        if company and not self._is_company_allowed(company, company_counts):
                            logger.debug(f"  Пропуск (компания): {company}")
                            stats["skipped_company"] += 1
                            skip_count += 1
                            continue

                        # Задержка только перед реальным кликом (п.10: из конфига)
                        time.sleep(random.uniform(config.BotConfig.DELAY_MIN, config.BotConfig.DELAY_MAX))

                        if self.locator.open_chat.is_visible():
                            self.click(self.locator.close_chat_button)

                        # Закрываем висячий модал перед кликом
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

                        # п.10: задержка из конфига
                        time.sleep(config.BotConfig.DELAY_AFTER_MODAL)

                        # Лимит HH — проверяем ДО URL-сравнения
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

                                # п.19: авто-выбор резюме по типу вакансии
                                if self.locator.modal_window_drop_base.is_visible():
                                    self._select_resume_in_modal(title)
                                    time.sleep(0.5)

                                # п.3: персонализированное письмо по типу вакансии
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

                                # п.7: dry-run — не нажимаем submit
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
                                    self.click(self.locator.modal_window_button_response)
                                    time.sleep(0.5)

                                    # Лимит как ответ сервера на клик submit
                                    if self._is_response_limit_reached():
                                        self._handle_limit_reached(applied, manual_review, query_stats, total_count, started_at, company_counts)
                                        return

                                    total_count += 1
                                    stats["responses"] += 1
                                    response_sent = True

                                    # Обновляем счётчик компании
                                    if company:
                                        company_counts[company] = company_counts.get(company, 0) + 1

                                    # Ждём пока модал закроется
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

                                    # Сохраняем applied
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

                                    # п.6: скоринг + п.8: логируем
                                    score = self._get_vacancy_score(vacancy_url, api_map)
                                    score_str = f" [скор:{score}]" if score and config.BotConfig.USE_SCORING else ""
                                    logger.info(
                                        f"Отклик #{total_count} | {title or '?'} | "
                                        f"{company or '?'}{score_str} ('{query}', стр.{count_page + 1})"
                                    )

                                    # п.9: CSV-лог откликов
                                    if config.BotConfig.LOG_RESPONSES_CSV and vacancy_url:
                                        self._log_response_csv(vacancy_url, title, company, query)

                                # dry_run: тоже обновляем company_counts для корректного лимита
                                if dry_run and company:
                                    company_counts[company] = company_counts.get(company, 0) + 1

                            except Exception as modal_err:
                                # п.6: сетевой retry в обработчике модала
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
                            # Редирект на внешний сайт
                            stats["redirects"] += 1
                            logger.debug(f"  Редирект при отклике: {title or vacancy_url}")
                            search_page_url = self._safe_return_to_search(search_page_url)
                            skip_count += 1

                    if total_count >= config.BotConfig.MAX_RESPONSES_PER_RUN:
                        break

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
                    self._log_final_stats(query_stats, total_count, started_at, company_counts)
                    return
                raise

        self._save_applied(applied)
        self._save_manual_review(manual_review)
        self._log_final_stats(query_stats, total_count, started_at, company_counts)

    # ─────────────────────────── Вспомогательные ───────────────────────────

    def _is_fuzzy_duplicate(self, company: str, title: str, applied: dict) -> bool:
        """Возвращает True если (company, ~title) уже есть в applied — ловит репосты."""
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
        """Извлекает (title, company, date_text) из карточки вакансии (п.8, п.16)"""
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
        """п.16: True если вакансия не старше FRESHNESS_DAYS"""
        if not date_text:
            return True
        text = date_text.lower().strip()
        today = datetime.now()

        # ISO datetime (из атрибута datetime="2026-07-01T...")
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
        """п.5 + п.17: проверяет блэклист и лимит откликов на компанию"""
        company_lower = company.lower()

        for blacklisted in config.BlacklistConfig.COMPANIES:
            if blacklisted.lower() in company_lower:
                return False

        max_per = config.BotConfig.MAX_PER_COMPANY
        if max_per > 0 and company_counts.get(company, 0) >= max_per:
            return False

        return True

    def _select_resume_in_modal(self, vacancy_title: str) -> None:
        """п.19: выбирает резюме в дропдауне по типу вакансии"""
        title_lower = (vacancy_title or "").lower()
        selected_name = config.ResumeConfig.DEFAULT

        for keyword, resume_name in config.ResumeConfig.MATCH:
            if keyword in title_lower:
                selected_name = resume_name
                break

        resume_loc = self.locator.resume_option(selected_name)
        if resume_loc.is_visible():
            self.click(resume_loc)
            logger.debug(f"  Выбрано резюме: '{selected_name}'")
        else:
            # Fallback: первый доступный вариант
            first_opt = self.locator.modal_window_drop_base.locator("[data-qa='cell']").first
            if first_opt.is_visible():
                self.click(first_opt)

    def _get_cover_letter_for(self, title: str) -> str:
        """п.3: выбирает шаблон письма по ключевым словам в заголовке вакансии."""
        title_lower = (title or "").lower()
        for keyword, tpl_name in config.ResumeConfig.COVER_LETTER_MATCH:
            if keyword in title_lower:
                tpl = self._cover_letters.get(tpl_name, "")
                if tpl:
                    logger.debug(f"  Шаблон письма: '{tpl_name}' (ключ: '{keyword}')")
                    return tpl
        return self._cover_letters.get("default", "")

    def _format_cover_letter(self, template: str, title: str, company: str) -> str:
        """Подставляет все переменные профиля и вакансии в шаблон письма."""
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
        """п.5: получает вакансии через HH.ru API, возвращает {url: vacancy_dict}."""
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
        """п.6: считает скор вакансии по зарплате и свежести."""
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
        """Возвращает скор из api_map или 0 если не найдено."""
        if not url or not api_map:
            return 0
        return api_map.get(url, {}).get("score", 0)

    def _is_response_limit_reached(self) -> bool:
        """п.7: детектирует лимит 200 откликов/24ч от HH.ru"""
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
        self, applied: dict, manual_review: set, query_stats: dict,
        total_count: int, started_at: datetime, company_counts: dict
    ) -> None:
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

    # ─────────────────────────── Хранилище ───────────────────────────

    def _load_applied(self) -> dict:
        return db.load_applied()

    def _save_applied(self, applied: dict) -> None:
        db.save_applied(applied)

    def _expire_applied(self, applied: dict) -> dict:
        """п.13: удаляет из applied и из БД записи старше APPLIED_EXPIRY_DAYS"""
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

    def _load_manual_review(self) -> set:
        return db.load_manual_review()

    def _save_manual_review(self, manual_review: set) -> None:
        db.save_manual_review(manual_review)

    def _safe_return_to_search(self, search_page_url: str) -> str:
        self.page.goto(search_page_url)
        self.page.wait_for_load_state("domcontentloaded")
        time.sleep(0.5)
        return self.page.url

    # ─────────────────────────── Логирование ───────────────────────────

    def _log_response_csv(self, url: str, title: str, company: str, query: str) -> None:
        """п.9: записывает отклик в CSV-файл и в SQLite response_log"""
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
        """Расширенный вывод статистики"""
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
