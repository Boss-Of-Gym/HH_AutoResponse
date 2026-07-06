import logging
from playwright.sync_api import Page
from pages.base_page import BasePage
from pages.locators_page import Login

logger = logging.getLogger(__name__)


class LoginPage(BasePage):

    def __init__(self, page: Page):
        super().__init__(page)
        self.locator = Login(page)

    def login(self, username: str, password: str) -> None:
        cookie_btn = self.page.get_by_role("button", name="Понятно")
        if self.is_visible(cookie_btn):
            self.click(cookie_btn)

        region_btn = self.page.get_by_role("button", name="Да, верно")
        if self.is_visible(region_btn):
            self.click(region_btn)

        logger.info(f"[Auth] шаг 1: кликаем войти, url={self.page.url}")
        self.click(selector_or_locator=self.locator.enter_link)
        self.page.wait_for_load_state("domcontentloaded")
        logger.info(f"[Auth] шаг 2: после перехода, url={self.page.url}")

        applicant_btn = self.page.get_by_text("Я ищу работу", exact=False).first
        if self.is_visible(applicant_btn):
            logger.info("[Auth] шаг 2a: выбираем «Я ищу работу»")
            applicant_btn.click()
            self.page.wait_for_load_state("domcontentloaded")

        self.click(selector_or_locator=self.locator.enter_button)
        self.page.wait_for_load_state("domcontentloaded")
        logger.info(f"[Auth] шаг 3: после клика «Войти», url={self.page.url}")

        login_input = self.page.locator("[data-qa='login-input-username']")
        if login_input.count() == 0:
            login_input = self.page.locator("[data-qa='magritte-phone-input-national-number-input']")
        if login_input.count() == 0:
            login_input = self.page.get_by_role("textbox").first
        logger.info(f"[Auth] шаг 4: заполняем логин (локатор={login_input})")
        self.fill(selector_or_locator=login_input, value=username)

        self.click(selector_or_locator=self.locator.button_enter_with_password)
        self.page.wait_for_load_state("domcontentloaded")
        logger.info(f"[Auth] шаг 5: после «Войти с паролем», url={self.page.url}")

        try:
            self.locator.expected_text_page_password.wait_for(state="visible", timeout=self.timeouts.EXPECT)
        except Exception:
            logger.warning(f"[Auth] заголовок «Введите пароль» не появился, url={self.page.url}")
        self.fill(selector_or_locator=self.locator.password_textbox, value=password)
        self.click(selector_or_locator=self.locator.password_button)
        self.page.wait_for_load_state("domcontentloaded")
        logger.info(f"[Auth] шаг 6: после ввода пароля, url={self.page.url}")

    def assert_login_on_page(self) -> None:
        confirmed = False
        for locator in (self.locator.text_resume_and_profile, self.locator.text_response):
            try:
                locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
                confirmed = True
                break
            except Exception:
                pass
        if not confirmed:
            logger.error(f"Авторизация не подтверждена, url={self.page.url}")
            raise RuntimeError("Не удалось подтвердить авторизацию на HH.ru")
