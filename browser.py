"""
共通 Chrome ドライバーファクトリー。
ヘッドレス・プロキシ・UA ローテーション対応。
"""
import random

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from loguru import logger

import selenium_config as cfg

# よく使われる Windows/Mac の UA を複数用意してランダム選択
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]


def build_driver() -> webdriver.Chrome:
    """設定に従って Chrome WebDriver を起動して返す。"""
    options = Options()

    if cfg.HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"--user-agent={random.choice(_USER_AGENTS)}")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--lang=ja-JP")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # navigator.webdriver フラグを消してボット検知を回避
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )

    driver.set_page_load_timeout(cfg.PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(cfg.IMPLICIT_WAIT)

    logger.debug("Chrome WebDriver 起動完了")
    return driver
