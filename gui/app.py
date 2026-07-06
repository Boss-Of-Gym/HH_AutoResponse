import json
import os
import queue as _queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"), static_url_path="/static")

SETTINGS_FILE = ROOT / "settings.json"
COVER_LETTERS_DIR = ROOT / "cover_letters"

_active_jobs: dict[str, dict] = {}


def _default_settings() -> dict:
    return {
        "credentials": {
            "login": os.getenv("login_number", ""),
            "password": os.getenv("password", ""),
        },
        "profile": {
            "name": "",
            "phone": "",
            "email": os.getenv("login_number", ""),
            "city": "",
            "years_experience": "",
            "key_skills": "Python, Playwright, Selenium, pytest",
            "github": "",
            "portfolio": "",
            "position": "QA Automation Engineer",
        },
        "search": {
            "area": "113",
            "max_pages": 99,
            "queries": ["Тестировщик", "QA engineer", "Автоматизатор тестирования", "QA automation"],
            "experience": ["between1And3", "between3And6"],
        },
        "bot": {
            "max_responses_per_run": 150,
            "max_per_company": 5,
            "delay_min": 1.5,
            "delay_max": 3.5,
            "delay_after_modal": 0.8,
            "save_every_n": 1,
            "freshness_days": 14,
            "applied_expiry_days": 30,
            "log_responses_csv": True,
            "use_api_prefilter": False,
            "salary_min": 0,
            "use_scoring": True,
        },
        "resume": {
            "default": "Automation QA Engineer",
            "match": [
                {"keyword": "автоматизатор", "resume": "Automation QA Engineer"},
                {"keyword": "automation", "resume": "Automation QA Engineer"},
                {"keyword": "auto qa", "resume": "Automation QA Engineer"},
                {"keyword": "sdet", "resume": "Automation QA Engineer"},
                {"keyword": "manual", "resume": "QA Engineer"},
                {"keyword": "ручной", "resume": "QA Engineer"},
            ],
            "cover_letter_dir": "cover_letters",
            "cover_letter_match": [
                {"keyword": "автоматизатор", "template": "automation"},
                {"keyword": "automation", "template": "automation"},
                {"keyword": "auto qa", "template": "automation"},
                {"keyword": "sdet", "template": "automation"},
                {"keyword": "qa lead", "template": "qa_lead"},
                {"keyword": "lead qa", "template": "qa_lead"},
                {"keyword": "senior", "template": "qa_lead"},
                {"keyword": "ведущий", "template": "qa_lead"},
                {"keyword": "ручной", "template": "manual"},
                {"keyword": "manual", "template": "manual"},
            ],
        },
        "filters": {"blacklist_companies": []},
        "schedule": {
            "resume_raise_enabled": True,
            "resume_raise_interval": 4.0,
            "daily_run_enabled": False,
            "daily_run_time": "08:00",
        },
        "browser": {
            "headless": False,
            "locale": "ru-RU",
            "timezone": "Europe/Moscow",
        },
    }


def _load_settings() -> dict:
    defaults = _default_settings()
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key, val in stored.items():
                if isinstance(val, dict) and key in defaults and isinstance(defaults[key], dict):
                    defaults[key].update(val)
                else:
                    defaults[key] = val
        except Exception:
            pass
    return defaults


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent / "static"), "index.html")


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(_load_settings())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    _save_settings(request.get_json(force=True) or {})
    return jsonify({"ok": True})


@app.route("/api/cover-letters", methods=["GET"])
def api_get_cover_letters():
    COVER_LETTERS_DIR.mkdir(exist_ok=True)
    result = {}
    for f in sorted(COVER_LETTERS_DIR.glob("*.txt")):
        result[f.stem] = f.read_text(encoding="utf-8")
    return jsonify(result)


@app.route("/api/cover-letters/<name>", methods=["POST"])
def api_save_cover_letter(name: str):
    safe = name.replace("_", "").replace("-", "")
    if not safe.isalnum() or len(name) > 50:
        return jsonify({"error": "invalid name"}), 400
    content = (request.get_json(force=True) or {}).get("content", "")
    (COVER_LETTERS_DIR / f"{name}.txt").write_text(content, encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/cover-letters/<name>", methods=["DELETE"])
def api_delete_cover_letter(name: str):
    if name == "default":
        return jsonify({"error": "cannot delete default template"}), 400
    path = COVER_LETTERS_DIR / f"{name}.txt"
    if path.exists():
        path.unlink()
    return jsonify({"ok": True})


@app.route("/api/db/stats", methods=["GET"])
def api_db_stats():
    try:
        from utils import db as _db
        _db.init_db()
        applied = _db.load_applied()
        manual = _db.load_manual_review()
        neg_stats = _db.get_negotiations_stats()
        weekly = _db.get_weekly_activity()
        top_co: dict = {}
        for meta in applied.values():
            co = meta.get("company", "")
            if co:
                top_co[co] = top_co.get(co, 0) + 1
        top_list = sorted(top_co.items(), key=lambda x: -x[1])[:5]
        return jsonify({
            "applied_count": len(applied),
            "manual_count": len(manual),
            "negotiations": neg_stats,
            "weekly_activity": weekly,
            "top_companies": [{"name": c, "count": n} for c, n in top_list],
        })
    except Exception as exc:
        return jsonify({
            "error": str(exc),
            "applied_count": 0, "manual_count": 0,
            "negotiations": {}, "weekly_activity": [0] * 7, "top_companies": [],
        })


@app.route("/api/history", methods=["GET"])
def api_history():
    try:
        from utils import db as _db
        _db.init_db()
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "").strip()
        rows = _db.get_history(limit=limit, offset=offset, search=search, status_filter=status)
        total = _db.get_history_count(search=search, status_filter=status)
        return jsonify({"rows": rows, "total": total})
    except Exception as exc:
        return jsonify({"error": str(exc), "rows": [], "total": 0})


@app.route("/api/export", methods=["GET"])
def api_export():
    import io
    import csv as csv_mod
    try:
        from utils import db as _db
        _db.init_db()
        rows = _db.get_history(limit=10000, offset=0)
        buf = io.StringIO()
        w = csv_mod.writer(buf)
        w.writerow(["url", "title", "company", "applied_at", "query", "status"])
        for row in rows:
            w.writerow([
                row.get("url", ""), row.get("title", ""), row.get("company", ""),
                row.get("applied_at", ""), row.get("query", ""), row.get("status", ""),
            ])
        output = "﻿" + buf.getvalue()
        return Response(
            output.encode("utf-8"),
            mimetype="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": "attachment; filename=autoresponse_history.csv"},
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    dead = [jid for jid, job in _active_jobs.items()
            if all(p.poll() is not None for p in job['procs'])]
    for jid in dead:
        _active_jobs.pop(jid, None)
    if _active_jobs:
        return jsonify({"error": "Бот уже запущен", "running": list(_active_jobs.keys())}), 409

    data = request.get_json(force=True) or {}
    mode = data.get("mode", "run")
    n_workers = max(1, min(int(data.get("threads", 1)), 8))
    if mode == "check-status":
        n_workers = 1

    if getattr(sys, "frozen", False):
        base_cmd = [sys.executable]
    else:
        base_cmd = [_find_python(), str(ROOT / "main.py")]

    if mode == "dry-run":
        flag = "--dry-run"
    elif mode == "check-status":
        flag = "--check-status"
    else:
        flag = "--run"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    job_id = uuid.uuid4().hex[:8]
    procs = []
    for i in range(n_workers):
        cmd = base_cmd + [flag, f"--worker-id={i}", f"--workers={n_workers}"]
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        procs.append(proc)

    _active_jobs[job_id] = {'procs': procs, 'n_workers': n_workers}
    return jsonify({"job_id": job_id})


@app.route("/api/log/<job_id>")
def api_log_stream(job_id: str):
    def _stream():
        job = _active_jobs.get(job_id)
        if job is None:
            yield "data: [job not found]\n\n"
            return

        procs = job['procs']
        n = job['n_workers']

        if n == 1:
            try:
                for line in procs[0].stdout:
                    yield f"data: {line.rstrip()}\n\n"
            except Exception:
                pass
        else:
            q = _queue.Queue()

            def _reader(proc, wid):
                try:
                    for line in proc.stdout:
                        q.put((wid, line.rstrip()))
                except Exception:
                    pass
                finally:
                    q.put((wid, None))

            for i, p in enumerate(procs):
                threading.Thread(target=_reader, args=(p, i), daemon=True).start()

            done = 0
            while done < n:
                try:
                    wid, line = q.get(timeout=0.5)
                    if line is None:
                        done += 1
                    else:
                        yield f"data: [Поток {wid + 1}] {line}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"

        yield "data: [EOF]\n\n"
        _active_jobs.pop(job_id, None)

    return Response(
        _stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/run/<job_id>", methods=["DELETE"])
def api_stop_run(job_id: str):
    job = _active_jobs.pop(job_id, None)
    if job:
        for proc in job['procs']:
            try:
                proc.terminate()
            except Exception:
                pass
    return jsonify({"ok": True})


def _find_python() -> str:
    for candidate in [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        ROOT / "venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "bin" / "python",
    ]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def run_gui(port: int = 5555) -> None:
    import threading
    import time

    flask_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    flask_thread.start()
    time.sleep(0.8)

    try:
        import webview
        webview.create_window(
            title="AutoResponseHH",
            url=f"http://127.0.0.1:{port}",
            width=1280,
            height=840,
            min_size=(900, 600),
            resizable=True,
        )
        webview.start()
    except ImportError:
        import webbrowser
        print("pywebview не установлен, открываем в браузере.")
        webbrowser.open(f"http://127.0.0.1:{port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    run_gui()
