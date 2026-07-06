from playwright.sync_api import Page
from config import config


class Login:
    def __init__(self, page: Page):
        self.page = page

    @property
    def enter_link(self):
        return self.page.locator("[data-qa='login']")

    @property
    def enter_button(self):
        return self.page.locator("[data-qa='submit-button']")

    @property
    def login_number(self):
        return self.page.get_by_role("textbox").nth(1)

    @property
    def password_textbox(self):
        return self.page.get_by_role("textbox").first

    @property
    def button_enter_with_password(self):
        return self.page.get_by_role("button", name="Войти с паролем")

    @property
    def expected_text_page_password(self):
        return self.page.get_by_role("heading", name="Введите пароль")

    @property
    def password_button(self):
        return self.page.locator("[data-qa='submit-button']")

    @property
    def text_resume_and_profile(self):
        return self.page.get_by_role("link", name="Резюме и профиль")

    @property
    def text_response(self):
        return self.page.get_by_role("link", name="Отклики").first


class AutoResponse:
    def __init__(self, page: Page):
        self.page = page

    @property
    def textbox_search(self):
        return self.page.locator("[data-qa='search-input']")

    @property
    def button_search(self):
        return self.page.get_by_role("button", name="Найти")

    @property
    def button_understand(self):
        return self.page.get_by_role("button", name="Понятно")

    @property
    def button_response(self):
        return self.page.locator("[data-qa='vacancy-serp__vacancy_response']")

    @property
    def modal_window(self):
        return self.page.get_by_role("dialog", name="Отклик на вакансию")

    @property
    def heading_response_on_vacancie(self):
        return self.page.locator("[data-qa='title']", has_text="Отклик на вакансию")

    @property
    def heading_response_answer_question(self):
        return self.page.locator("[data-qa='title']", has_text="Ответьте на вопросы")

    @property
    def modal_window_drop_base(self):
        # [data-qa='resume-title'] виден когда дропдаун есть (закрыт или открыт)
        return self.page.locator("[data-qa='resume-title']")

    @property
    def modal_drop_base(self):
        """Открытый listbox дропдауна; виден ТОЛЬКО когда список развёрнут."""
        return self.page.locator("[data-qa='drop-base']")

    @property
    def modal_resume_trigger(self):
        """Trigger-карточка (role=button) — кликаем чтобы открыть / закрыть список.
        Подтверждено из DOM: div[role='button'] > [data-qa='cell'] > [data-qa='resume-title']
        """
        return self.page.locator("div[role='button']").filter(
            has=self.page.locator("[data-qa='cell'] [data-qa='resume-title']")
        )

    def resume_option_in_drop(self, name: str):
        """Опция по имени внутри открытого [data-qa='drop-base'].
        Структура: [data-qa='drop-base'] [data-qa='cell'] [data-qa='cell-text-content'] = название
        """
        return (
            self.page.locator("[data-qa='drop-base'] [data-qa='cell']")
            .filter(has=self.page.locator("[data-qa='cell-text-content']", has_text=name))
        )

    @property
    def modal_window_drop_base_resume_auto(self):
        """Резюме по умолчанию из конфига — не хардкод"""
        return self.resume_option_in_drop(config.ResumeConfig.DEFAULT)

    @property
    def modal_window_button_response(self):
        return self.page.locator("[data-qa='vacancy-response-submit-popup']")

    @property
    def status(self):
        return self.page.get_by_text("Отклик отправлен").first

    @property
    def number_pagination(self):
        return self.page.locator("[data-qa='pager-block']").locator("ul li a[data-qa='pager-page']")

    @property
    def response_out_of_Russia(self):
        return self.page.locator("[data-qa='relocation-warning-title']")

    @property
    def response_permanent_button(self):
        return self.page.get_by_role("button", name="Все равно откликнуться")

    @property
    def button_add_cover_letter(self):
        return self.page.locator("[data-qa='add-cover-letter']")

    @property
    def textbox_cover_letter(self):
        # data-qa подтверждён из DOM: <textarea data-qa="vacancy-response-popup-form-letter-input">
        return self.page.locator("[data-qa='vacancy-response-popup-form-letter-input']")

    @property
    def open_chat(self):
        return self.page.locator("[data-qa='chatik-root']")

    @property
    def close_chat_button(self):
        return self.page.locator("[data-qa='chatik-root']").get_by_role("button", name="close")

    @property
    def modal_close_button(self):
        return self.page.locator("[data-qa='response-popup-close']")

    @property
    def pagination_next(self):
        return self.page.locator("[data-qa='pager-next']")

    @property
    def cancel_button_for_response_to_go_to_page_out_hh(self):
        return self.page.locator("[data-qa='vacancy-response-link-advertising-cancel']")

    @property
    def text_response_to_go_to_page_out_hh(self):
        return self.page.get_by_text('Вакансия с прямым откликом')

    @property
    def filter_button(self):
        return self.page.locator("[data-qa='header-search-filters-button']")

    @property
    def filter_experiance(self):
        return self.page.locator("[data-qa='search-filter-experience-value-noExperience']")

    @property
    def filter_submit(self):
        return self.page.locator("[data-qa='search-drawer-filters-submit']")
