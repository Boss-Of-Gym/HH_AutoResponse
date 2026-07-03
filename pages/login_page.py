from playwright.sync_api import Page
from pages.base_page import BasePage
from pages.locators_page import Login


class LoginPage(BasePage):

    def __init__(self, page: Page):
        super().__init__(page)
        self.locator = Login(page)

    def login(self, username: str, password: str) -> None:
        """
            Метод авторизации в hh

            :param username: Передается логин из .env
            :type username: str
            :param password: Передается пароль из .env
            :type password: str
            :return: None
        """
        # Убираем cookie banner и попап региона, если появились до логина
        cookie_btn = self.page.get_by_role("button", name="Понятно")
        if self.is_visible(cookie_btn):
            self.click(cookie_btn)

        region_btn = self.page.get_by_role("button", name="Да, верно")
        if self.is_visible(region_btn):
            self.click(region_btn)

        self.click(selector_or_locator=self.locator.enter_link)
        self.click(selector_or_locator=self.locator.enter_button)
        self.fill(selector_or_locator=self.locator.login_number, value=username)
        self.click(selector_or_locator=self.locator.button_enter_with_password)
        self.has_text(selector_or_locator=self.locator.expected_text_page_password, expected_text='Введите пароль')
        self.fill(selector_or_locator=self.locator.password_textbox, value=password)
        self.click(selector_or_locator=self.locator.password_button)
    
    def assert_login_on_page(self) -> None:
        self.has_url(expected_url=f'{self.base_url}/?role=applicant')
        self.has_text(selector_or_locator=self.locator.text_resume_and_profile, expected_text='Резюме и профиль')
        self.has_text(selector_or_locator=self.locator.text_response, expected_text='Отклики')




