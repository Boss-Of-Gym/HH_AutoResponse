from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Urls:
    BASE_URL: str = "https://hh.ru/"


@dataclass(frozen=True)
class Timeouts:
    PAGE_LOAD: int = 30_000
    EXPECT: int = 10_000
    SHORT: int = 1_000


@dataclass(frozen=True)
class Credentials:
    LOGIN: str = os.getenv('login_number', 'default_login')
    PASSWORD: str = os.getenv('password', 'default_password')


@dataclass(frozen=True)
class SearchConfig:
    # 1=Москва, 2=Санкт-Петербург, 113=Россия
    AREA: str = "113"
    EXPERIENCE: tuple = ("between1And3", "between3And6")
    QUERIES: tuple = (
        "Тестировщик",
        "QA engineer",
        "Автоматизатор тестирования",
        "QA automation",
    )
    MAX_PAGES: int = 99


@dataclass(frozen=True)
class BotConfig:
    # Личный лимит откликов за сессию — оставляем запас до лимита HH (200)
    MAX_RESPONSES_PER_RUN: int = 150
    # Задержки между кликами (сек) — имитация человека
    DELAY_MIN: float = 1.5
    DELAY_MAX: float = 3.5
    # Пауза после открытия модального окна (сек)
    DELAY_AFTER_MODAL: float = 0.8
    # Удалять записи из applied_vacancies.json старше N дней
    APPLIED_EXPIRY_DAYS: int = 30
    # Пропускать вакансии старше N дней (0 = не фильтровать)
    FRESHNESS_DAYS: int = 14
    # Максимум откликов на одну компанию за сессию (0 = без лимита)
    MAX_PER_COMPANY: int = 5
    # Писать CSV-лог откликов (response_log_YYYY-MM-DD.csv)
    LOG_RESPONSES_CSV: bool = True


@dataclass(frozen=True)
class ResumeConfig:
    # Имя резюме по умолчанию (часть строки — регистронезависимо)
    DEFAULT: str = "Automation QA Engineer"
    # Маппинг: (ключевое_слово_в_заголовке_вакансии, имя_резюме)
    # Первое совпадение побеждает
    MATCH: tuple = (
        ("автоматизатор", "Automation QA Engineer"),
        ("automation", "Automation QA Engineer"),
        ("auto qa", "Automation QA Engineer"),
        ("sdet", "Automation QA Engineer"),
        ("manual", "QA Engineer"),
        ("ручной", "QA Engineer"),
    )


@dataclass(frozen=True)
class BlacklistConfig:
    # Компании, на вакансии которых НЕ откликаться (проверка по вхождению строки)
    COMPANIES: tuple = (
        # "Рога и Копыта",
        # "МЛМ Сеть",
    )


@dataclass(frozen=True)
class Config:
    Urls: Urls = Urls()
    Timeouts: Timeouts = Timeouts()
    Credentials: Credentials = Credentials()
    Search: SearchConfig = SearchConfig()
    Bot: BotConfig = BotConfig()
    Resume: ResumeConfig = ResumeConfig()
    Blacklist: BlacklistConfig = BlacklistConfig()


config = Config()
