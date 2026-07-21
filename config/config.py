import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"


def _load_raw() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_cfg = _load_raw()


def _s(*keys, default=None):
    v = _cfg
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, default)
        if v is None:
            return default
    return v


def _parse_areas(raw) -> tuple:
    if isinstance(raw, list):
        parts = [str(a).strip() for a in raw if str(a).strip()]
    else:
        parts = [a.strip() for a in str(raw or "113").split(",") if a.strip()]
    return tuple(parts) if parts else ("113",)


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
    LOGIN: str = (_s("credentials", "login") or os.getenv("login_number", "default_login"))
    PASSWORD: str = (_s("credentials", "password") or os.getenv("password", "default_password"))


@dataclass(frozen=True)
class ProfileConfig:
    NAME: str = (_s("profile", "name") or "")
    PHONE: str = (_s("profile", "phone") or "")
    EMAIL: str = (_s("profile", "email") or _s("credentials", "login") or "")
    CITY: str = (_s("profile", "city") or "")
    YEARS_EXPERIENCE: str = (_s("profile", "years_experience") or "")
    KEY_SKILLS: str = (_s("profile", "key_skills") or "")
    GITHUB: str = (_s("profile", "github") or "")
    PORTFOLIO: str = (_s("profile", "portfolio") or "")
    POSITION: str = (_s("profile", "position") or "QA Engineer")
    RESUME_SUMMARY: str = (_s("profile", "resume_summary") or "")


@dataclass(frozen=True)
class SearchConfig:
    AREAS: tuple = _parse_areas(_s("search", "area"))
    AREA: str = _parse_areas(_s("search", "area"))[0]
    EXPERIENCE: tuple = tuple(
        _s("search", "experience") or ["between1And3", "between3And6"]
    )
    QUERIES: tuple = tuple(
        _s("search", "queries") or [
            "Тестировщик", "QA engineer",
            "Автоматизатор тестирования", "QA automation",
        ]
    )
    MAX_PAGES: int = int(_s("search", "max_pages") or 99)


@dataclass(frozen=True)
class BotConfig:
    MAX_RESPONSES_PER_RUN: int = int(_s("bot", "max_responses_per_run") or 150)
    DELAY_MIN: float = float(_s("bot", "delay_min") or 1.5)
    DELAY_MAX: float = float(_s("bot", "delay_max") or 3.5)
    DELAY_AFTER_MODAL: float = float(_s("bot", "delay_after_modal") or 0.8)
    APPLIED_EXPIRY_DAYS: int = int(_s("bot", "applied_expiry_days") or 30)
    FRESHNESS_DAYS: int = int(_s("bot", "freshness_days") or 14)
    MAX_PER_COMPANY: int = int(_s("bot", "max_per_company") or 5)
    SAVE_EVERY_N: int = int(_s("bot", "save_every_n") or 1)
    LOG_RESPONSES_CSV: bool = bool(_s("bot", "log_responses_csv", default=True))
    USE_API_PREFILTER: bool = bool(_s("bot", "use_api_prefilter", default=False))
    SALARY_MIN: int = int(_s("bot", "salary_min") or 0)
    USE_SCORING: bool = bool(_s("bot", "use_scoring", default=True))


def _build_tuples(section_key: str, val_key: str, defaults: list) -> tuple:
    raw = _s("resume", section_key)
    items = raw if isinstance(raw, list) else defaults
    return tuple(
        (item["keyword"], item[val_key])
        for item in items
        if isinstance(item, dict) and "keyword" in item and val_key in item
    )


@dataclass(frozen=True)
class ResumeConfig:
    DEFAULT: str = (_s("resume", "default") or "Automation QA Engineer")
    MATCH: tuple = _build_tuples("match", "resume", [
        {"keyword": "автоматизатор", "resume": "Automation QA Engineer"},
        {"keyword": "automation",    "resume": "Automation QA Engineer"},
        {"keyword": "auto qa",       "resume": "Automation QA Engineer"},
        {"keyword": "sdet",          "resume": "Automation QA Engineer"},
        {"keyword": "manual",        "resume": "QA Engineer"},
        {"keyword": "ручной",        "resume": "QA Engineer"},
    ])
    COVER_LETTER_DIR: str = (_s("resume", "cover_letter_dir") or "cover_letters")
    COVER_LETTER_MATCH: tuple = _build_tuples("cover_letter_match", "template", [
        {"keyword": "автоматизатор", "template": "automation"},
        {"keyword": "automation",    "template": "automation"},
        {"keyword": "auto qa",       "template": "automation"},
        {"keyword": "sdet",          "template": "automation"},
        {"keyword": "qa lead",       "template": "qa_lead"},
        {"keyword": "lead qa",       "template": "qa_lead"},
        {"keyword": "senior",        "template": "qa_lead"},
        {"keyword": "ведущий",       "template": "qa_lead"},
        {"keyword": "ручной",        "template": "manual"},
        {"keyword": "manual",        "template": "manual"},
    ])


@dataclass(frozen=True)
class BlacklistConfig:
    COMPANIES: tuple = tuple(
        _s("filters", "blacklist_companies") or []
    )


@dataclass(frozen=True)
class ResumeRaiseConfig:
    ENABLED: bool = bool(_s("schedule", "resume_raise_enabled", default=True))
    INTERVAL_HOURS: float = float(_s("schedule", "resume_raise_interval") or 4.0)


@dataclass(frozen=True)
class BrowserConfig:
    HEADLESS: bool = bool(_s("browser", "headless", default=False))
    LOCALE: str = (_s("browser", "locale") or "ru-RU")
    TIMEZONE: str = (_s("browser", "timezone") or "Europe/Moscow")


@dataclass(frozen=True)
class AIConfig:
    ENABLED: bool = bool(_s("ai", "enabled", default=False))
    PROVIDER: str = (_s("ai", "provider") or "ollama")
    MODEL: str = (_s("ai", "model") or "qwen2.5:7b")
    OLLAMA_URL: str = (_s("ai", "ollama_url") or "http://localhost:11434")
    GEMINI_API_KEY: str = (_s("ai", "gemini_api_key") or "")
    CONFIDENCE_THRESHOLD: float = float(_s("ai", "confidence_threshold") or 0.7)
    TIMEOUT: float = float(_s("ai", "timeout") or 60.0)


@dataclass(frozen=True)
class Config:
    Urls: Urls = Urls()
    Timeouts: Timeouts = Timeouts()
    Credentials: Credentials = Credentials()
    Profile: ProfileConfig = ProfileConfig()
    Search: SearchConfig = SearchConfig()
    Bot: BotConfig = BotConfig()
    Resume: ResumeConfig = ResumeConfig()
    Blacklist: BlacklistConfig = BlacklistConfig()
    ResumeRaise: ResumeRaiseConfig = ResumeRaiseConfig()
    Browser: BrowserConfig = BrowserConfig()
    AI: AIConfig = AIConfig()


config = Config()

Profile = ProfileConfig()
Browser = BrowserConfig()
AI = AIConfig()
