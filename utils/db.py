import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_FILE = "autoresponse.db"
_LEGACY_APPLIED = "applied_vacancies.json"
_LEGACY_MANUAL = "manual_review.json"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS applied_vacancies (
                url         TEXT PRIMARY KEY,
                title       TEXT DEFAULT '',
                company     TEXT DEFAULT '',
                applied_at  TEXT DEFAULT '',
                query       TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS manual_review (
                url        TEXT PRIMARY KEY,
                title      TEXT DEFAULT '',
                company    TEXT DEFAULT '',
                added_at   TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS response_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                query       TEXT DEFAULT '',
                url         TEXT DEFAULT '',
                title       TEXT DEFAULT '',
                company     TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS negotiations (
                vacancy_url   TEXT PRIMARY KEY,
                title         TEXT DEFAULT '',
                company       TEXT DEFAULT '',
                status        TEXT DEFAULT '',
                prev_status   TEXT DEFAULT '',
                first_seen    TEXT DEFAULT '',
                last_checked  TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ai_answers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                vacancy_url TEXT DEFAULT '',
                question    TEXT DEFAULT '',
                answer      TEXT DEFAULT ''
            );
        """)
        for col, typedef in [('title', 'TEXT DEFAULT ""'), ('company', 'TEXT DEFAULT ""'), ('added_at', 'TEXT DEFAULT ""')]:
            try:
                conn.execute(f"ALTER TABLE manual_review ADD COLUMN {col} {typedef}")
            except Exception:
                pass
    _migrate_legacy()


def _migrate_legacy() -> None:
    applied_path = Path(_LEGACY_APPLIED)
    manual_path = Path(_LEGACY_MANUAL)

    if applied_path.exists():
        try:
            raw = json.loads(applied_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                logger.warning(f"Неожиданный формат {_LEGACY_APPLIED}: ожидался list, получен {type(raw).__name__}. Файл сохранён как .bak.")
                applied_path.rename(applied_path.with_suffix(".json.bak"))
                raw = []
            if raw:
                if isinstance(raw[0], str):
                    applied_dict = {
                        url: {"title": "", "company": "", "applied_at": "", "query": ""}
                        for url in raw
                    }
                else:
                    applied_dict = {e["url"]: e for e in raw if "url" in e}
            else:
                applied_dict = {}

            if applied_dict:
                with _connect() as conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO applied_vacancies "
                        "(url, title, company, applied_at, query) VALUES (?, ?, ?, ?, ?)",
                        [
                            (url, m.get("title", ""), m.get("company", ""),
                             m.get("applied_at", ""), m.get("query", ""))
                            for url, m in applied_dict.items()
                        ],
                    )
                logger.info(
                    f"Мигрировано {len(applied_dict)} записей "
                    f"{_LEGACY_APPLIED} → SQLite (autoresponse.db)"
                )
            applied_path.rename(applied_path.with_suffix(".json.bak"))
        except Exception as e:
            logger.warning(f"Ошибка миграции {_LEGACY_APPLIED}: {e}")

    if manual_path.exists():
        try:
            urls = json.loads(manual_path.read_text(encoding="utf-8"))
            if urls:
                with _connect() as conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO manual_review (url) VALUES (?)",
                        [(u,) for u in urls if u],
                    )
                logger.info(
                    f"Мигрировано {len(urls)} записей {_LEGACY_MANUAL} → SQLite"
                )
            manual_path.rename(manual_path.with_suffix(".json.bak"))
        except Exception as e:
            logger.warning(f"Ошибка миграции {_LEGACY_MANUAL}: {e}")


def load_applied() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT url, title, company, applied_at, query FROM applied_vacancies"
        ).fetchall()
    return {row["url"]: dict(row) for row in rows}


def delete_expired_applied(cutoff_iso: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM applied_vacancies WHERE applied_at != '' AND applied_at < ?",
            (cutoff_iso,),
        )
        return cur.rowcount


def save_applied(applied: dict) -> None:
    if not applied:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO applied_vacancies (url, title, company, applied_at, query) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (url, m.get("title", ""), m.get("company", ""),
                 m.get("applied_at", ""), m.get("query", ""))
                for url, m in applied.items()
            ],
        )


def load_manual_review() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT url, title, company, added_at FROM manual_review ORDER BY added_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def save_manual_review(items) -> None:
    if not items:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if items and isinstance(next(iter(items), None), str):
        rows = [(u, '', '', now) for u in items]
    else:
        rows = [(i.get('url', ''), i.get('title', ''), i.get('company', ''), i.get('added_at', now)) for i in items if i.get('url')]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO manual_review (url, title, company, added_at) VALUES (?,?,?,?)",
            rows
        )


def delete_manual_review(url: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM manual_review WHERE url = ?", (url,))


def log_response(url: str, title: str, company: str, query: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO response_log (timestamp, query, url, title, company) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), query, url, title, company),
        )


def save_ai_answer(vacancy_url: str, question: str, answer: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ai_answers (timestamp, vacancy_url, question, answer) VALUES (?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), vacancy_url, question, answer),
        )


def load_negotiations() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT vacancy_url, title, company, status, prev_status, first_seen, last_checked "
            "FROM negotiations"
        ).fetchall()
    return {row["vacancy_url"]: dict(row) for row in rows}


def save_negotiations(items: list[dict]) -> list[dict]:
    if not items:
        return []

    existing = load_negotiations()
    changes = []
    now = datetime.now().isoformat(timespec="seconds")

    with _connect() as conn:
        for item in items:
            url = item.get("url", "")
            if not url:
                continue
            new_status = item.get("status", "")
            title = item.get("title", "")
            company = item.get("company", "")
            checked_at = item.get("checked_at", now)

            if url in existing:
                old_status = existing[url]["status"]
                first_seen = existing[url]["first_seen"] or now
                if old_status != new_status:
                    changes.append({
                        "url": url,
                        "title": title or existing[url]["title"],
                        "company": company or existing[url]["company"],
                        "old_status": old_status,
                        "new_status": new_status,
                    })
                conn.execute(
                    "UPDATE negotiations SET title=?, company=?, status=?, prev_status=?, "
                    "last_checked=? WHERE vacancy_url=?",
                    (title or existing[url]["title"],
                     company or existing[url]["company"],
                     new_status, old_status, checked_at, url),
                )
            else:
                conn.execute(
                    "INSERT INTO negotiations "
                    "(vacancy_url, title, company, status, prev_status, first_seen, last_checked) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (url, title, company, new_status, "", now, checked_at),
                )

    return changes


def get_negotiations_stats() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM negotiations GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def get_weekly_activity() -> list:
    from datetime import date, timedelta
    today = date.today()
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT DATE(timestamp) as day, COUNT(*) as cnt "
                "FROM response_log "
                "WHERE DATE(timestamp) >= DATE('now', '-6 days') "
                "GROUP BY day"
            ).fetchall()
        day_map = {row["day"]: row["cnt"] for row in rows}
    except Exception:
        day_map = {}
    return [day_map.get((today - timedelta(days=i)).isoformat(), 0) for i in range(6, -1, -1)]


def get_history(limit: int = 50, offset: int = 0, search: str = '', status_filter: str = '') -> list:
    like = f'%{search}%' if search else '%'
    with _connect() as conn:
        if status_filter and status_filter != 'all':
            rows = conn.execute(
                """SELECT av.url, av.title, av.company, av.applied_at, av.query,
                       COALESCE(n.status, 'Ожидание') AS status
                   FROM applied_vacancies av
                   LEFT JOIN negotiations n ON n.vacancy_url = av.url
                   WHERE (av.title LIKE ? OR av.company LIKE ?)
                     AND COALESCE(n.status, 'Ожидание') = ?
                   ORDER BY av.applied_at DESC LIMIT ? OFFSET ?""",
                (like, like, status_filter, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT av.url, av.title, av.company, av.applied_at, av.query,
                       COALESCE(n.status, 'Ожидание') AS status
                   FROM applied_vacancies av
                   LEFT JOIN negotiations n ON n.vacancy_url = av.url
                   WHERE av.title LIKE ? OR av.company LIKE ?
                   ORDER BY av.applied_at DESC LIMIT ? OFFSET ?""",
                (like, like, limit, offset),
            ).fetchall()
    return [dict(row) for row in rows]


def is_already_applied(url: str) -> bool:
    if not url:
        return False
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM applied_vacancies WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def save_limit_reached(reached_at: datetime) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('limit_reached_at', ?)",
            (reached_at.isoformat(timespec="seconds"),),
        )


def get_limit_reached_at() -> "datetime | None":
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'limit_reached_at'"
            ).fetchone()
        if row and row["value"]:
            return datetime.fromisoformat(row["value"])
    except Exception:
        pass
    return None


def get_history_count(search: str = '', status_filter: str = '') -> int:
    like = f'%{search}%' if search else '%'
    with _connect() as conn:
        if status_filter and status_filter != 'all':
            row = conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_vacancies av
                   LEFT JOIN negotiations n ON n.vacancy_url = av.url
                   WHERE (av.title LIKE ? OR av.company LIKE ?)
                     AND COALESCE(n.status, 'Ожидание') = ?""",
                (like, like, status_filter),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM applied_vacancies av "
                "WHERE av.title LIKE ? OR av.company LIKE ?",
                (like, like),
            ).fetchone()
    return row['cnt'] if row else 0
