import os
import logging
from datetime import datetime
from playwright.sync_api import Page, BrowserContext

logger = logging.getLogger(__name__)


class DebugHelper:
    def __init__(self, page: Page, context: BrowserContext, test_name: str):
        self.page = page
        self.context = context
        self.test_name = test_name
        self.ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        self.base = "artifacts"
        self.screens = f"{self.base}/screenshots"
        self.html = f"{self.base}/html"
        self.traces = f"{self.base}/traces"

        for d in (self.screens, self.html, self.traces):
            os.makedirs(d, exist_ok=True)

    def start_trace(self):
        self.context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True
        )

    def stop_trace(self, failed: bool):
        path = f"{self.traces}/{self.test_name}_{self.ts}.zip"
        self.context.tracing.stop(path=path)
        if failed:
            logger.error(f"📦 Trace сохранён: {path}")

    def dump(self, reason: str):
        logger.error(f"💥 TEST FAILED: {self.test_name}")
        logger.error(f"URL: {self.page.url}")

        screenshot = f"{self.screens}/{self.test_name}_{self.ts}.png"
        html = f"{self.html}/{self.test_name}_{self.ts}.html"

        try:
            self.page.screenshot(path=screenshot, full_page=True)
            logger.error(f"🖼 Screenshot: {screenshot}")
        except Exception as e:
            logger.error(f"Screenshot error: {e}")

        try:
            with open(html, "w", encoding="utf-8") as f:
                f.write(self.page.content())
            logger.error(f"📄 HTML: {html}")
        except Exception as e:
            logger.error(f"HTML dump error: {e}")
