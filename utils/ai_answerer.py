"""LLM-клиент для авто-ответов на скрининг-вопросы hh.ru.

Провайдер по умолчанию — локальный Ollama (бесплатно, офлайн).
Gemini API используется только если явно выбран в настройках.
"""
import json
import logging
import re
import urllib.error
import urllib.request

from config import config

logger = logging.getLogger(__name__)

# Локальные 7B-модели (в т.ч. qwen2.5) иногда съезжают на китайский/японский/корейский
# посреди ответа на русском — отсеиваем такие ответы, чтобы не отправить их работодателю.
_FOREIGN_SCRIPT = re.compile(r"[一-鿿぀-ヿ가-힯]")


def is_enabled() -> bool:
    return config.AI.ENABLED


def _seniority_title(position: str) -> str:
    position = (position or "специалист").strip()
    return position if "senior" in position.lower() else f"Senior {position}"


def build_persona() -> str:
    p = config.Profile
    title = _seniority_title(p.POSITION)
    who = f"{p.NAME} — {title}" if p.NAME else title
    experience = f" с опытом работы {p.YEARS_EXPERIENCE} лет" if p.YEARS_EXPERIENCE else ""
    return (
        f"Ты — {who}{experience}, отвечающий на скрининг-вопросы работодателя при "
        "отклике на вакансию на hh.ru. Отвечай от первого лица, уверенно и по-деловому, "
        "как реальный кандидат такого уровня — не как ассистент. Отвечай на том же "
        "языке, на котором задан вопрос. Используй только факты из резюме ниже — "
        "не выдумывай опыт, проекты и навыки, которых там нет."
    )


def build_profile_context() -> str:
    p = config.Profile
    if p.RESUME_SUMMARY.strip():
        return p.RESUME_SUMMARY.strip()
    lines = [
        f"Должность: {p.POSITION}",
        f"Опыт работы: {p.YEARS_EXPERIENCE} лет" if p.YEARS_EXPERIENCE else None,
        f"Навыки: {p.KEY_SKILLS}" if p.KEY_SKILLS else None,
        f"Город: {p.CITY}" if p.CITY else None,
    ]
    return "\n".join(line for line in lines if line)


def _build_prompt(question: str, options: list[str] | None) -> str:
    parts = [
        build_persona(),
        "",
        "Резюме кандидата:",
        build_profile_context(),
        "",
        f"Вопрос работодателя: {question}",
    ]
    if options:
        opts = "\n".join(f"- {o}" for o in options)
        parts.append(
            "Это вопрос с вариантами ответа. Выбери РОВНО ОДИН вариант, "
            f"дословно совпадающий с одним из них:\n{opts}"
        )
    parts.append(
        "Ответь строго в формате JSON без пояснений и markdown: "
        '{"answer": "<ответ>", "confidence": <число от 0 до 1 — насколько ты уверен, '
        'что этот ответ корректен и его безопасно отправить автоматически>}'
    )
    return "\n".join(parts)


def _call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": config.AI.MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.AI.OLLAMA_URL.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.AI.TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "")


def _call_gemini(prompt: str) -> str:
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }).encode("utf-8")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={config.AI.GEMINI_API_KEY}"
    )
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=config.AI.TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["candidates"][0]["content"]["parts"][0]["text"]


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(match.group(0) if match else raw)


def ask(question: str, options: list[str] | None = None) -> tuple[str, float]:
    """Возвращает (ответ, уверенность 0..1).

    При любой ошибке связи/парсинга возвращает ("", 0.0) — безопасный отказ,
    вызывающий код должен в этом случае откатиться к ручному отклику.
    """
    question = (question or "").strip()
    if not question:
        return "", 0.0

    prompt = _build_prompt(question, options)
    try:
        raw = _call_gemini(prompt) if config.AI.PROVIDER == "gemini" else _call_ollama(prompt)
        data = _extract_json(raw)
        answer = str(data.get("answer", "")).strip()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning(f"ai_answerer: не удалось получить ответ от {config.AI.PROVIDER} ({exc})")
        return "", 0.0

    if _FOREIGN_SCRIPT.search(answer):
        logger.warning(f"ai_answerer: модель съехала на другой алфавит, отклоняю ответ: {answer[:80]!r}")
        return "", 0.0

    if options:
        exact = next((o for o in options if o.strip().lower() == answer.lower()), None)
        if exact is None:
            logger.info(f"ai_answerer: ответ '{answer}' не совпал ни с одним вариантом — отклоняю")
            return "", 0.0
        answer = exact

    return answer, confidence
