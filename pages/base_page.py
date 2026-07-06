from playwright.sync_api import Page, Locator, expect, TimeoutError as PlaywrightTimeoutError
from config import config
import logging

logger = logging.getLogger(__name__)


class BasePage:

    def __init__(self, page: Page):
        self.page = page
        self.base_url = config.Urls.BASE_URL
        self.timeouts = config.Timeouts

    def open(self, url: str = None):
        target_url = url or self.base_url
        logger.info(f"[BasePage] Открываем страницу: {target_url}")
        self.page.goto(target_url, timeout=self.timeouts.PAGE_LOAD)

    def reload(self):
        self.page.reload(timeout=self.timeouts.PAGE_LOAD)

    def click(self, selector_or_locator):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.click()
            logger.info(f"[BasePage] Клик по элементу: {locator}")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Элемент {locator} не найден для клика")
            raise

    def fill(self, selector_or_locator, value: str):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.fill(value)
            locator_str = str(locator).lower()
            is_sensitive = any(k in locator_str for k in ("password", "login", "account/login"))
            if is_sensitive:
                preview = "***"
            else:
                preview = value[:50] + "..." if len(value) > 50 else value
            logger.info(f"[BasePage] Заполнено {locator} значением '{preview}'")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Элемент {locator} не найден для заполнения")
            raise

    def hover(self, selector_or_locator):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.hover()
            logger.info(f"[BasePage] Hover на элементе: {locator}")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Элемент {locator} не найден для hover")
            raise

    def select_option(self, selector_or_locator, value):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.select_option(value)
            logger.info(f"[BasePage] Выбрано значение {value} в {locator}")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Элемент {locator} не найден для select_option")
            raise

    def check(self, selector_or_locator):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.check()
            logger.info(f"[BasePage] Чекбокс {locator} отмечен")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Чекбокс {locator} не найден для check")
            raise

    def uncheck(self, selector_or_locator):
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.EXPECT)
            locator.uncheck()
            logger.info(f"[BasePage] Чекбокс {locator} снят")
        except PlaywrightTimeoutError:
            logger.error(f"[BasePage] Чекбокс {locator} не найден для uncheck")
            raise

    def get_text(self, selector_or_locator) -> str:
        locator = self._ensure_locator(selector_or_locator)
        locator.wait_for(state="visible", timeout=self.timeouts.SHORT)
        return locator.inner_text()

    def get_attribute(self, selector_or_locator, attribute: str) -> str:
        locator = self._ensure_locator(selector_or_locator)
        locator.wait_for(state="visible", timeout=self.timeouts.SHORT)
        return locator.get_attribute(attribute)

    def is_visible(self, selector_or_locator) -> bool:
        locator = self._ensure_locator(selector_or_locator)
        return locator.is_visible()

    def is_enabled(self, selector_or_locator) -> bool:
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.SHORT)
            return locator.is_enabled()
        except PlaywrightTimeoutError:
            return False

    def has_text(self, selector_or_locator, expected_text: str) -> bool:
        try:
            locator = self._ensure_locator(selector_or_locator)
            locator.wait_for(state="visible", timeout=self.timeouts.SHORT)
            return expected_text in locator.inner_text()
        except PlaywrightTimeoutError:
            return False

    def has_url(self, expected_url: str) -> bool:
        return expected_url in self.page.url

    def assert_text(self, selector_or_locator, expected_text: str):
        locator = self._ensure_locator(selector_or_locator)
        expect(locator).to_contain_text(expected_text, timeout=self.timeouts.EXPECT)

    def assert_url(self, expected_url: str):
        expect(self.page).to_have_url(expected_url, timeout=self.timeouts.EXPECT)

    def _ensure_locator(self, selector_or_locator):
        if isinstance(selector_or_locator, str):
            return self.page.locator(selector_or_locator)
        return selector_or_locator

    def scroll_and_wait(self, locator, timeout=5000):
        locator.scroll_into_view_if_needed()
        locator.wait_for(state="visible", timeout=timeout)
