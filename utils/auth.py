from playwright.sync_api import Page
from pages.login_page import LoginPage
from pages.base_page import BasePage
from config import config


class Auth(BasePage):
    def authentication(self, page: Page) -> Page:
        username = config.Credentials.LOGIN
        password = config.Credentials.PASSWORD

        self.open()

        login_page = LoginPage(page)
        login_page.login(username, password)
        page.wait_for_load_state("domcontentloaded")
        login_page.assert_login_on_page()
        return page
