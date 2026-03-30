"""
Mercari スクレイパー（Selenium + BeautifulSoup）
- メルカリにログインしてセッションクッキーを保管
- キーワードごとに「売り切れ」商品を検索
- 直近 N 日間に同一商品が MIN_SALES_COUNT 件以上売れているものだけ返す
"""

import json
import math
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tenacity import retry, stop_after_attempt, wait_exponential

import selenium_config as cfg
from browser import build_driver


# ── データクラス ──────────────────────────────────────────────────────────────

class SoldItem:
    """メルカリで売れた商品 1件分の情報"""

    def __init__(
        self,
        name: str,
        price: int,
        sold_count: int,
        keyword: str,
        item_id: str = "",
        image_url: str = "",
        item_url: str = "",
    ):
        self.name       = name
        self.price      = price
        self.sold_count = sold_count
        self.keyword    = keyword
        self.item_id    = item_id
        self.image_url  = image_url
        self.item_url   = item_url

    def __repr__(self):
        return f"SoldItem({self.name[:30]!r}, ¥{self.price:,}, x{self.sold_count})"


# ── スクレイパー本体 ──────────────────────────────────────────────────────────

class MercariSeleniumScraper:
    BASE_URL        = "https://jp.mercari.com"
    LOGIN_URL       = "https://jp.mercari.com/login"
    SEARCH_URL      = "https://jp.mercari.com/search"

    # 売り切れフィルター（実際に売れた実績価格を使うため sold_out 限定）
    STATUS_SOLD_OUT = "sold_out"
    # 設定値から読み込み（sold_out / on_sale を env で切替可能）
    STATUS = cfg.MERCARI_STATUS

    def __init__(self):
        self.driver = build_driver()
        self.wait   = WebDriverWait(self.driver, cfg.PAGE_LOAD_TIMEOUT)
        self._logged_in = False

    # ── クッキー管理 ────────────────────────────────────────────────────────

    def _save_cookies(self):
        cookies = self.driver.get_cookies()
        Path(cfg.MERCARI_COOKIE_FILE).write_text(json.dumps(cookies, ensure_ascii=False))
        logger.debug(f"クッキー保存: {len(cookies)} 件")

    def _load_cookies(self) -> bool:
        path = Path(cfg.MERCARI_COOKIE_FILE)
        if not path.exists():
            return False
        cookies = json.loads(path.read_text())
        self.driver.get(self.BASE_URL)
        for c in cookies:
            # selenium では sameSite などの未知属性を除外する必要がある
            c.pop("sameSite", None)
            try:
                self.driver.add_cookie(c)
            except Exception:
                pass
        logger.debug(f"クッキー読み込み: {len(cookies)} 件")
        return True

    def _is_logged_in(self) -> bool:
        """ログイン済みかどうかをページ内容で判定"""
        try:
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            # ログイン後はマイページへのリンクやアイコンが存在する
            return bool(
                self.driver.find_elements(By.CSS_SELECTOR, "[data-testid='mypage-link']")
                or self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/mypage']")
            )
        except Exception:
            return False

    # ── ログイン ────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """メルカリにログイン。クッキーが有効なら再利用。"""
        # 既存クッキーでログイン済みか確認
        if self._load_cookies() and self._is_logged_in():
            logger.info("クッキーでログイン済みを確認")
            self._logged_in = True
            return True

        if not cfg.MERCARI_EMAIL or not cfg.MERCARI_PASSWORD:
            logger.error(
                "MERCARI_EMAIL / MERCARI_PASSWORD が未設定です。"
                ".env を確認してください。"
            )
            return False

        logger.info("メルカリ ログイン開始")
        self.driver.get(self.LOGIN_URL)
        time.sleep(random.uniform(2, 4))

        try:
            # ── メールアドレス入力 ──
            email_input = self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='email'], input[type='email']")
                )
            )
            email_input.clear()
            self._human_type(email_input, cfg.MERCARI_EMAIL)
            time.sleep(random.uniform(0.5, 1.2))

            # ── パスワード入力 ──
            pw_input = self.driver.find_element(
                By.CSS_SELECTOR, "input[name='password'], input[type='password']"
            )
            pw_input.clear()
            self._human_type(pw_input, cfg.MERCARI_PASSWORD)
            time.sleep(random.uniform(0.5, 1.0))

            # ── ログインボタン ──
            login_btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "button[type='submit'], button[data-testid='login-button']",
            )
            login_btn.click()
            time.sleep(random.uniform(3, 5))

            # SMS 認証など 2FA が要求される場合はここで止まる
            if "verify" in self.driver.current_url or "two" in self.driver.current_url:
                logger.warning(
                    "2段階認証が要求されています。"
                    "ヘッドレスを無効（HEADLESS=false）にして手動で認証してください。"
                )
                input("2段階認証を完了したら Enter を押してください...")

            if self._is_logged_in():
                self._save_cookies()
                self._logged_in = True
                logger.info("メルカリ ログイン成功")
                return True
            else:
                logger.error("ログイン後の確認に失敗しました")
                return False

        except Exception as e:
            logger.error(f"ログイン中にエラー: {e}")
            return False

    # ── 人間らしいタイピング ─────────────────────────────────────────────────

    @staticmethod
    def _human_type(element, text: str):
        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.18))

    # ── 検索・スクレイピング ────────────────────────────────────────────────

    def _build_search_url(self, keyword: str, page: int = 1) -> str:
        from urllib.parse import quote
        return (
            f"{self.SEARCH_URL}"
            f"?keyword={quote(keyword)}"
            f"&status={self.STATUS}"
            f"&sort=created_time"
            f"&order=desc"
            f"&page={page}"
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _fetch_search_page(self, keyword: str, page: int = 1) -> BeautifulSoup:
        url = self._build_search_url(keyword, page)
        logger.debug(f"GET {url}")
        self.driver.get(url)
        time.sleep(random.uniform(2.5, 4.5))

        # 無限スクロールではなくページネーションの場合はそのまま返す
        return BeautifulSoup(self.driver.page_source, "lxml")

    def _parse_items(self, soup: BeautifulSoup, keyword: str) -> list[dict]:
        """
        検索結果 HTML から商品リストをパース。
        メルカリの HTML 構造は変更されることがあるため、
        複数のセレクターをフォールバック付きで試みる。
        """
        items = []

        # ── セレクター候補（構造変更に備えて複数用意） ──
        card_selectors = [
            "li[data-testid='item-cell']",
            "li[data-testid='item-card']",
            "div[data-testid='item-card']",
            "mer-item-thumbnail",
            "ul.items-box-content > li",
        ]

        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                logger.debug(f"カードセレクター '{sel}' で {len(cards)} 件ヒット")
                break

        if not cards:
            logger.warning(
                f"商品カードが見つかりませんでした（ページが空か構造が変更された可能性）"
                f" URL: {self.driver.current_url[:80] if hasattr(self, 'driver') else 'N/A'}"
            )
            # HTML断片をデバッグ出力（最初の500文字）
            body = soup.find("body")
            snippet = body.get_text(separator=" ", strip=True)[:200] if body else "(no body)"
            logger.debug(f"  ページ内容(先頭200文字): {snippet}")
            return []

        cutoff = datetime.now() - timedelta(days=cfg.DAYS_LOOKBACK)

        for card in cards:
            try:
                # ── 商品名 ──
                name = ""
                for name_sel in [
                    "[data-testid='item-name']",
                    ".item-name",
                    "span.merText",
                    "p.merText",
                ]:
                    el = card.select_one(name_sel)
                    if el:
                        name = el.get_text(strip=True)
                        break

                # ── 価格 ──
                price = 0
                for price_sel in [
                    "[data-testid='item-price']",
                    ".item-price",
                    "span.merPrice",
                    "div.price",
                ]:
                    el = card.select_one(price_sel)
                    if el:
                        raw = el.get_text(strip=True)
                        digits = re.sub(r"[^\d]", "", raw)
                        price = int(digits) if digits else 0
                        break

                # ── 商品 ID・URL ──
                item_id = ""
                item_url = ""
                link = card.select_one("a[href*='/item/']")
                if link:
                    href = link.get("href", "")
                    item_url = href if href.startswith("http") else self.BASE_URL + href
                    m = re.search(r"/item/(m\w+)", href)
                    if m:
                        item_id = m.group(1)

                # ── サムネイル ──
                image_url = ""
                img = card.select_one("img")
                if img:
                    image_url = img.get("src") or img.get("data-src") or ""

                # ── 販売日時（sold_out 商品のみ付く場合がある） ──
                sold_date: Optional[datetime] = None
                for date_sel in [
                    "[data-testid='sold-date']",
                    "time",
                    ".sold-date",
                ]:
                    el = card.select_one(date_sel)
                    if el:
                        dt_str = el.get("datetime") or el.get_text(strip=True)
                        try:
                            sold_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        except Exception:
                            pass
                        break

                # 日時が取れた場合は期間フィルタ
                if sold_date and sold_date.replace(tzinfo=None) < cutoff:
                    continue

                if name and price > 0:
                    items.append(
                        {
                            "name":      name,
                            "price":     price,
                            "item_id":   item_id,
                            "item_url":  item_url,
                            "image_url": image_url,
                            "keyword":   keyword,
                        }
                    )

            except Exception as e:
                logger.debug(f"カードのパース中にスキップ: {e}")
                continue

        return items

    def _count_pages(self, soup: BeautifulSoup) -> int:
        """ページ数を取得（最大 5 ページまで）"""
        for sel in [
            "[data-testid='pagination'] li:last-child",
            "nav.pagination li:last-child",
            ".pager a:last-child",
        ]:
            el = soup.select_one(sel)
            if el:
                digits = re.sub(r"[^\d]", "", el.get_text())
                if digits:
                    return min(int(digits), 5)
        return 1

    def scrape_keyword(self, keyword: str) -> list[SoldItem]:
        """
        1キーワードに対して売り切れ商品をスクレイピングし、
        同一商品名が MIN_SALES_COUNT 件以上あるものを返す。
        """
        logger.info(f"Mercari スクレイピング: '{keyword}'")
        raw_items: list[dict] = []

        # メルカリは 1 ページ約 30 件。MAX_ITEMS_PER_KEYWORD から必要ページ数を計算
        items_per_page = 30
        max_pages = max(1, math.ceil(cfg.MAX_ITEMS_PER_KEYWORD / items_per_page))

        # ── ページ 1 を取得 ──
        soup1 = self._fetch_search_page(keyword, 1)
        raw_items.extend(self._parse_items(soup1, keyword))
        available_pages = min(self._count_pages(soup1), max_pages)

        # ── 2 ページ目以降 ──
        for page in range(2, available_pages + 1):
            if len(raw_items) >= cfg.MAX_ITEMS_PER_KEYWORD:
                break
            soup = self._fetch_search_page(keyword, page)
            raw_items.extend(self._parse_items(soup, keyword))
            time.sleep(random.uniform(1.5, 3.0))

        if not raw_items:
            logger.info(f"  '{keyword}': HTML取得 0件 → スキップ")
            return []

        # ── 商品名で集計（先頭 20 文字で正規化） ──
        groups: defaultdict[str, list[dict]] = defaultdict(list)
        for item in raw_items:
            key = item["name"][:20].lower()
            groups[key].append(item)

        # グループサイズ分布をログに出す（デバッグ用）
        size_dist: dict[int, int] = {}
        for g in groups.values():
            s = len(g)
            size_dist[s] = size_dist.get(s, 0) + 1
        logger.debug(
            f"  '{keyword}': グループサイズ分布 {dict(sorted(size_dist.items()))} "
            f"(MIN_SALES_COUNT={cfg.MIN_SALES_COUNT})"
        )

        skipped_by_count = 0
        results: list[SoldItem] = []
        for key, group in groups.items():
            if len(group) < cfg.MIN_SALES_COUNT:
                skipped_by_count += len(group)
                logger.debug(
                    f"  '{keyword}' SKIP(販売数不足): '{group[0]['name'][:30]}' → {len(group)}件 < {cfg.MIN_SALES_COUNT}"
                )
                continue
            rep = group[0]
            prices = sorted(g["price"] for g in group if g["price"] > 0)
            median_price = prices[len(prices) // 2] if prices else 0
            if median_price == 0:
                continue

            results.append(
                SoldItem(
                    name       = rep["name"],
                    price      = median_price,
                    sold_count = len(group),
                    keyword    = keyword,
                    item_id    = rep["item_id"],
                    image_url  = rep["image_url"],
                    item_url   = rep["item_url"],
                )
            )

        logger.info(
            f"  '{keyword}' [{self.STATUS}]: HTML取得 {len(raw_items)}件 → "
            f"グループ {len(groups)}種 → "
            f"{cfg.MIN_SALES_COUNT}件以上: {len(results)}件 "
            f"（除外 {skipped_by_count}件）"
        )
        return results

    def scrape_all(self) -> list[SoldItem]:
        """全キーワードをスクレイピングして結果を返す。"""
        if not self._logged_in and not self.login():
            logger.error("ログインに失敗したためスクレイピングを中断します")
            return []

        all_items: list[SoldItem] = []
        for kw in cfg.SEARCH_KEYWORDS:
            items = self.scrape_keyword(kw)
            all_items.extend(items)
            time.sleep(random.uniform(3.0, 6.0))  # キーワード間の待機

        logger.info(f"Mercari 合計: {len(all_items)} 件の売れ筋商品")
        return all_items

    def close(self):
        self.driver.quit()
        logger.debug("WebDriver 終了")
