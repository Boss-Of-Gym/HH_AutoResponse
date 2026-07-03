from playwright.sync_api import Page
from pages.login_page import LoginPage
from pages.base_page import BasePage
from config import config


class Auth(BasePage):
    def authentication(self, page: Page) -> Page:
        """
        Авторизация пользователя на HH.ru.

        Args:
            page: Playwright Page объект

        Returns:
            Аутентифицированная страница
        """
        username = config.Credentials.LOGIN
        password = config.Credentials.PASSWORD

        self.open()

        login_page = LoginPage(page)
        login_page.login(username, password)
        login_page.assert_login_on_page()
        return page
