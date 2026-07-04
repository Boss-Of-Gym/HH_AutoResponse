"""
HH.ru Public API client — опциональное обогащение данных (п.25).
Используется для предварительной фильтрации вакансий по зарплате,
свежести и другим параметрам ДО открытия браузера.

Текущая браузерная логика откликов НЕ затрагивается.
API используется только для сбора метаданных.
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

HH_API_BASE = "https://api.hh.ru"
_USER_AGENT = "AutoResponseHH/2.0 (job search automation)"


def fetch_vacancies(
    query: str,
    area: str,
    experience: tuple,
    max_pages: int = 20,
    salary_min: int | None = None,
    freshness_days: int = 0,
) -> list[dict]:
    """Получает список вакансий через HH.ru API.

    Возвращает список dict с полями:
      id, url, title, company, salary_from, salary_to,
      salary_currency, published_at, published_days_ago
    """
    exp_params = "&".join(f"experience={e}" for e in experience)
    results = []

    for page in range(max_pages):
        endpoint = (
            f"{HH_API_BASE}/vacancies"
            f"?text={urllib.parse.quote(query)}"
            f"&area={area}"
            f"&{exp_params}"
            f"&order_by=relevance"
            f"&per_page=100"
            f"&page={page}"
        )

        try:
            req = urllib.request.Request(
                endpoint,
                headers={"User-Agent": _USER_AGENT, "HH-User-Agent": _USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"HH API ошибка (стр. {page}): {e}")
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            salary = item.get("salary") or {}
            pub_at = item.get("published_at", "")
            days_ago = _days_ago(pub_at)

            # Фильтр по свежести (если задан)
            if freshness_days > 0 and days_ago is not None and days_ago > freshness_days:
                continue

            sal_from = salary.get("from")
            sal_to = salary.get("to")
            currency = salary.get("currency", "RUR")
            gross = salary.get("gross", True)

            # Фильтр по минимальной зарплате (если задан)
            if salary_min and currency == "RUR":
                net_from = int(sal_from * 0.87) if sal_from and gross else sal_from
                net_to = int(sal_to * 0.87) if sal_to and gross else sal_to
                effective = net_from or net_to or 0
                if effective and effective < salary_min:
                    continue

            results.append({
                "id": item["id"],
                "url": f"https://hh.ru/vacancy/{item['id']}",
                "title": item.get("name", ""),
                "company": item.get("employer", {}).get("name", ""),
                "salary_from": sal_from,
                "salary_to": sal_to,
                "salary_currency": currency,
                "salary_gross": gross,
                "published_at": pub_at,
                "published_days_ago": days_ago,
            })

        total_pages = data.get("pages", 1)
        if page + 1 >= total_pages:
            break

        time.sleep(0.3)  # уважаем rate-limit API

    return results


def fetch_all_queries(
    queries: tuple,
    area: str,
    experience: tuple,
    salary_min: int | None = None,
    freshness_days: int = 0,
) -> dict[str, list[dict]]:
    """Выполняет запросы ко всем поисковым запросам из конфига.
    Возвращает {query: [vacancy_dict, ...]}"""
    result = {}
    for query in queries:
        logger.info(f"[HH API] Запрос: '{query}'")
        vacancies = fetch_vacancies(
            query=query,
            area=area,
            experience=experience,
            salary_min=salary_min,
            freshness_days=freshness_days,
        )
        result[query] = vacancies
        logger.info(f"[HH API] Найдено: {len(vacancies)} вакансий для '{query}'")
    return result


def _days_ago(iso_dt: str) -> int | None:
    """Возвращает количество дней с момента публикации вакансии."""
    if not iso_dt:
        return None
    try:
        # HH.ru отдаёт "2026-07-01T10:00:00+0300"
        dt = datetime.fromisoformat(iso_dt.replace("+0300", "+03:00"))
        return (datetime.now(dt.tzinfo) - dt).days
    except (ValueError, TypeError):
        return None
