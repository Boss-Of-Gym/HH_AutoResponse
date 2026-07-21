"""Извлечение и авто-заполнение скрининг-вопросов hh.ru через ИИ.

Селекторы построены по двум реальным образцам разметки (файл
html_page_response.html, дважды приложенный к проекту — сначала вариант
"модалка на той же странице", потом вариант "переход на отдельную страницу"):

    <div data-qa="task-body">
      <div data-qa="task-question">Текст вопроса</div>
      ...
      <textarea name="task_<id>_text">...</textarea>                 <!-- свободный текст -->
      -- или --
      <label data-qa="cell">                                         <!-- вариант ответа -->
        <input type="radio" value="<id или 'open'>">
        <span data-qa="cell-text-content">Текст варианта</span>
      </label>
    </div>

Важные детали, подтверждённые на реальных примерах:
- Оба сценария (модалка и отдельная страница) используют один и тот же
  паттерн task-body/task-question — можно не различать их в коде.
- У radio/checkbox <label> оборачивает <input> НЕЯВНО, без for/id — текст
  варианта нужно брать из вложенного [data-qa="cell-text-content"], а не
  искать label[for=...].
- Один из вариантов ответа может быть "Свой вариант" с value="open" — при
  его выборе открывается скрытый <textarea>, который эта реализация не
  заполняет. Такой вариант исключается из списка, предлагаемого ИИ, чтобы
  модель не выбрала его как "наиболее подходящий" и не оставила пустой ответ.
- Порядок проверки типа поля важен: у вопроса с "Свой вариант" внутри
  task-body присутствует СКРЫТЫЙ <textarea> наравне с radio-кнопками —
  поэтому radio/checkbox проверяются раньше textarea/text.

Ветка для <select> добавлена по аналогии (тот же паттерн task-body), живого
образца с выпадающим списком не было. Если что-то не распознано — вакансия
безопасно уходит в ручной отклик (см. try_ai_answer), ничего не отправляется
вслепую.
"""
import logging

from playwright.sync_api import Page

from config import config
from utils import ai_answerer, db

logger = logging.getLogger(__name__)

_TASK_BODY = '[data-qa="task-body"]'
_TASK_QUESTION = '[data-qa="task-question"]'
_CELL_TEXT = '[data-qa="cell-text-content"]'
_CUSTOM_ANSWER_VALUE = "open"  # value варианта "Свой вариант" — открывает доп. текстовое поле


def _collect_choice_labels(body, input_selector: str) -> list[str]:
    """Текст видимых вариантов ответа, кроме "Свой вариант" (value="open")."""
    inputs = body.locator(input_selector)
    labels = []
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        if (inp.get_attribute("value") or "") == _CUSTOM_ANSWER_VALUE:
            continue

        text = ""
        input_id = inp.get_attribute("id")
        if input_id:
            lbl = body.locator(f'label[for="{input_id}"]')
            if lbl.count():
                text = lbl.inner_text().strip()
        if not text:
            # <label> оборачивает <input> неявно — текст в data-qa="cell-text-content"
            ancestor_label = inp.locator("xpath=ancestor::label[1]")
            if ancestor_label.count():
                content = ancestor_label.locator(_CELL_TEXT)
                text = (content.first.inner_text() if content.count() else ancestor_label.first.inner_text()).strip()
        if not text:
            text = inp.get_attribute("value") or ""
        if text:
            labels.append(text)
    return labels


def extract_questions(page: Page) -> list[dict]:
    bodies = page.locator(_TASK_BODY)
    questions = []
    for i in range(bodies.count()):
        body = bodies.nth(i)
        label_el = body.locator(_TASK_QUESTION)
        label = label_el.first.inner_text().strip() if label_el.count() else ""

        if not label:
            questions.append({"index": i, "type": "unknown", "label": "", "options": []})
        # radio/checkbox проверяются раньше textarea: у варианта "Свой вариант"
        # в том же task-body есть скрытый textarea для доп. ввода.
        elif body.locator('input[type="radio"]').count():
            options = _collect_choice_labels(body, 'input[type="radio"]')
            questions.append({"index": i, "type": "radio", "label": label, "options": options})
        elif body.locator('input[type="checkbox"]').count():
            options = _collect_choice_labels(body, 'input[type="checkbox"]')
            questions.append({"index": i, "type": "checkbox", "label": label, "options": options})
        elif body.locator("select").count():
            options = body.locator("select").first.locator("option").all_inner_texts()
            questions.append({
                "index": i, "type": "select", "label": label,
                "options": [o.strip() for o in options if o.strip()],
            })
        elif body.locator("textarea").count():
            questions.append({"index": i, "type": "textarea", "label": label, "options": []})
        elif body.locator('input[type="text"], input:not([type])').count():
            questions.append({"index": i, "type": "text", "label": label, "options": []})
        else:
            questions.append({"index": i, "type": "unknown", "label": label, "options": []})
    return questions


def try_ai_answer(page: Page, vacancy_url: str = "") -> bool:
    """Пытается ответить на вопросы модалки через ИИ.

    Возвращает True, если все вопросы распознаны и заполнены с достаточной
    уверенностью (можно продолжать обычный flow подачи отклика). Возвращает
    False при любой неуверенности/ошибке — не трогая форму, чтобы вызывающий
    код мог откатиться к ручному ревью как раньше.
    """
    if not ai_answerer.is_enabled():
        return False

    logger.info(f"question_filler: пробую ИИ-ответ для {vacancy_url or page.url}")

    try:
        questions = extract_questions(page)
    except Exception as exc:
        logger.warning(f"question_filler: не удалось извлечь вопросы ({exc})")
        return False

    if not questions:
        try:
            generic_count = page.locator(
                "textarea, select, input[type='radio'], input[type='checkbox'], input[type='text']"
            ).count()
        except Exception:
            generic_count = -1
        logger.info(
            "question_filler: блоки [data-qa='task-body'] не найдены на странице "
            f"(полей ввода на странице всего: {generic_count}) — ручной отклик"
        )
        return False

    logger.info(
        "question_filler: найдено вопросов: "
        + "; ".join(f"[{q['type']}] {q['label'][:60]!r} опций={len(q['options'])}" for q in questions)
    )

    threshold = config.AI.CONFIDENCE_THRESHOLD
    answered = []
    for q in questions:
        if q["type"] == "unknown" or not q["label"]:
            logger.info("question_filler: не удалось определить тип/текст вопроса — ручной отклик")
            return False
        if q["type"] in ("radio", "checkbox", "select") and not q["options"]:
            logger.info(f"question_filler: у вопроса '{q['label']}' нет распознанных вариантов — ручной отклик")
            return False

        options = q["options"] or None
        answer, confidence = ai_answerer.ask(q["label"], options)
        if confidence < threshold or not answer:
            logger.info(
                f"question_filler: низкая уверенность ({confidence:.2f}) для "
                f"вопроса '{q['label']}' — ручной отклик"
            )
            return False
        answered.append({**q, "answer": answer})

    for q in answered:
        try:
            _apply_answer(page, q)
        except Exception as exc:
            logger.warning(f"question_filler: не удалось заполнить поле '{q['label']}' ({exc})")
            return False

    for q in answered:
        db.save_ai_answer(vacancy_url, q["label"], q["answer"])

    logger.info(f"question_filler: ИИ ответил на {len(answered)} вопрос(ов)")
    return True


def _apply_answer(page: Page, q: dict) -> None:
    body = page.locator(_TASK_BODY).nth(q["index"])
    field_type = q["type"]
    answer = q["answer"]

    if field_type in ("radio", "checkbox"):
        target = body.locator(_CELL_TEXT, has_text=answer)
        if target.count():
            target.first.click()
        else:
            body.get_by_text(answer, exact=False).first.click()
    elif field_type == "select":
        body.locator("select").first.select_option(label=answer)
    elif field_type == "textarea":
        body.locator("textarea").first.fill(answer)
    elif field_type == "text":
        body.locator('input[type="text"], input:not([type])').first.fill(answer)
    else:
        raise ValueError(f"неизвестный тип поля: {field_type}")
