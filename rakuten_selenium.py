"""
楽天市場 価格スクレイパー（Selenium + BeautifulSoup）
API キー不要。楽天検索ページをブラウザ経由で取得して最安値を抽出する。
"""

import re
import time
import random
from urllib.parse import quote
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

import selenium_config as cfg
from browser import build_driver
from mercari_selenium import SoldItem


# ── データクラス ──────────────────────────────────────────────────────────────

@dataclass
class ProfitResult:
    """利益計算結果"""
    mercari_item:          SoldItem
    rakuten_name:          str
    rakuten_price:         int        # 仕入れ価格（円）
    rakuten_shop:          str
    rakuten_url:           str
    rakuten_image_url:     str
    amazon_sell_price:     int        # Amazon 出品予定価格（円）
    profit:                int        # 利益額（円）
    profit_rate:           float      # 利益率（0.0〜1.0）


# ── 楽天スクレイパー ──────────────────────────────────────────────────────────

class RakutenScraper:
    # s=2: 価格昇順。日本語キーワードは quote() で %XX エンコードする
    SEARCH_URL = "https://search.rakuten.co.jp/search/mall/{keyword}/?s=2"

    # 価格テキストから数字だけ抜き出す（¥・,・円・全角数字 に対応）
    _PRICE_RE = re.compile(r"[\d,．]+")

    def __init__(self):
        self.driver = build_driver()
        self.wait   = WebDriverWait(self.driver, cfg.PAGE_LOAD_TIMEOUT)

    # ── ページ取得 ────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=12))
    def _fetch_page(self, keyword: str) -> BeautifulSoup:
        """
        Selenium でページを開き、商品リストが描画されるまで待機して
        BeautifulSoup オブジェクトを返す。
        """
        # 日本語キーワードを URL エンコード（スペースは + ではなく %20）
        url = self.SEARCH_URL.format(keyword=quote(keyword, safe=""))
        logger.debug(f"楽天 GET {url}")
        self.driver.get(url)

        # 商品リストが 1 件以上出るまで最大 10 秒待つ
        # 複数のセレクターを OR 条件でまとめて待機
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     "div[data-rat-itemid], div.searchresultitems, li.item")
                )
            )
        except Exception:
            # タイムアウトしてもパースは試みる
            pass

        time.sleep(random.uniform(1.0, 2.5))
        return BeautifulSoup(self.driver.page_source, "lxml")

    # ── HTML パース ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_price(text: str) -> int:
        """
        価格文字列 → int 変換。
        例: "¥1,234" → 1234 / "1,234円" → 1234 / "1234" → 1234
        """
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else 0

    def _parse_cards(self, soup: BeautifulSoup) -> list[dict]:
        """
        楽天検索結果ページから全商品カードをパースして返す。
        楽天は定期的にレイアウトを変更するため、セレクターを優先順に試みる。
        """
        # ── カードコンテナ候補（優先順） ──────────────────────────────────
        card_selectors = [
            "div[data-rat-itemid]",             # 2023〜 新レイアウト
            "div.searchresultitems div.item",   # 旧レイアウト
            "li.item",                          # 一部カテゴリ
            "div.item-details",
        ]

        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                logger.debug(f"楽天カード '{sel}' → {len(cards)} 件")
                break

        items = []
        for card in cards[:30]:   # 先頭 30 件を見る
            try:
                item = self._parse_single_card(card)
                if item:
                    items.append(item)
            except Exception:
                continue

        return items

    def _parse_single_card(self, card) -> Optional[dict]:
        """1枚のカードから name / price / shop / url / image_url を抽出する。"""

        # ── 商品名 ────────────────────────────────────────────────────────
        name = ""
        for sel in [
            "a.content--2O6Gt",        # 新レイアウト
            ".title a",                # 旧レイアウト
            "h2 a",
            "a[data-rat-itemtitle]",
            ".item-name a",
        ]:
            el = card.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break

        # ── 価格 ──────────────────────────────────────────────────────────
        price = 0
        for sel in [
            "span.price--OGXaL",       # 新レイアウト（税込み価格）
            ".important",              # 旧レイアウト
            "span.price",              # 元のコードが参照していた class
            "span[class*='price']",
            ".price",
        ]:
            el = card.select_one(sel)
            if el:
                price = self._extract_price(el.get_text(strip=True))
                if price > 0:
                    break

        # price == 0 はスキップ
        if price == 0:
            return None

        # ── 店名 ──────────────────────────────────────────────────────────
        shop = ""
        for sel in [
            "a.shopName--2l95m",       # 新レイアウト
            ".shop-name a",
            ".shopname a",
            "a[href*='shop.rakuten']",
        ]:
            el = card.select_one(sel)
            if el:
                shop = el.get_text(strip=True)
                break

        # ── 商品ページ URL ────────────────────────────────────────────────
        url = ""
        for sel in [
            "a.content--2O6Gt",
            "a[data-rat-itemtitle]",
            ".title a",
            "h2 a",
        ]:
            el = card.select_one(sel)
            if el and el.get("href", "").startswith("http"):
                url = el["href"]
                break

        # ── サムネイル ────────────────────────────────────────────────────
        image_url = ""
        img = card.select_one("img")
        if img:
            image_url = img.get("src") or img.get("data-src") or ""

        if not name:
            return None

        return {
            "name":      name,
            "price":     price,
            "shop":      shop,
            "url":       url,
            "image_url": image_url,
        }

    def _fallback_parse(self, soup: BeautifulSoup) -> Optional[dict]:
        """
        カードが一切見つからない場合の最終手段。
        ページ内の "¥X,XXX" パターンから最初の有効価格を拾う。
        """
        for tag in soup.find_all(string=re.compile(r"[¥￥]\s*[\d,]+")):
            price = self._extract_price(tag)
            if price < 100:
                continue
            parent = tag.parent
            link = parent.find("a") if parent else None
            return {
                "name":      parent.get_text(strip=True)[:60] if parent else "",
                "price":     price,
                "shop":      "",
                "url":       link.get("href", "") if link else "",
                "image_url": "",
            }
        return None

    # ── 公開メソッド ─────────────────────────────────────────────────────────

    def get_cheapest(self, keyword: str) -> Optional[dict]:
        """
        キーワードで楽天を検索して最安値商品情報を返す。
        見つからない場合は None を返す。
        """
        try:
            soup  = self._fetch_page(keyword)
            items = self._parse_cards(soup)

            if not items:
                result = self._fallback_parse(soup)
            else:
                result = min(items, key=lambda x: x["price"])

            if result:
                logger.debug(
                    f"楽天最安値: '{keyword[:20]}' "
                    f"→ ¥{result['price']:,}  ({result['shop'][:20] or '不明'})"
                )
            else:
                logger.debug(f"楽天: '{keyword[:20]}' → 結果なし")

            return result

        except Exception as e:
            logger.warning(f"楽天スクレイピング失敗 '{keyword[:20]}': {e}")
            return None

    def close(self):
        self.driver.quit()


# ── 利益計算 ─────────────────────────────────────────────────────────────────

class ProfitCalculator:
    def __init__(self):
        self.scraper = RakutenScraper()

    def _calc(
        self, sell_price: int, buy_price: int
    ) -> tuple[int, float]:
        """
        楽天仕入れ → Mercari 出品モデルの利益計算。

          mercari_fee = sell_price × MERCARI_FEE_RATE   (10%)
          profit      = sell_price - mercari_fee - SHIPPING_FEE - buy_price
          profit_rate = profit / sell_price              (0.0〜1.0)
        """
        mercari_fee = int(sell_price * cfg.MERCARI_FEE_RATE)
        profit      = sell_price - mercari_fee - cfg.SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price if sell_price > 0 else 0.0
        return profit, profit_rate

    def evaluate(self, item: SoldItem) -> Optional[ProfitResult]:
        """
        メルカリ売れ筋商品を楽天で検索し、利益率が閾値を超えたら ProfitResult を返す。
        出品価格はメルカリの実績売価をそのまま使う。
        """
        rakuten = self.scraper.get_cheapest(item.name[:50])
        if not rakuten or rakuten["price"] == 0:
            return None

        profit, profit_rate = self._calc(item.price, rakuten["price"])

        logger.debug(
            f"{item.name[:25]} | 仕入¥{rakuten['price']:,} → "
            f"出品¥{item.price:,} | 利益¥{profit:,} ({profit_rate:.1%})"
        )

        if profit_rate < cfg.MIN_PROFIT_RATE:
            return None

        return ProfitResult(
            mercari_item      = item,
            rakuten_name      = rakuten["name"],
            rakuten_price     = rakuten["price"],
            rakuten_shop      = rakuten["shop"],
            rakuten_url       = rakuten["url"],
            rakuten_image_url = rakuten["image_url"],
            amazon_sell_price = item.price,   # Mercari 実績売価 = 出品予定価格
            profit            = profit,
            profit_rate       = profit_rate,
        )

    def filter_all(self, items: list[SoldItem]) -> list[ProfitResult]:
        """全商品を評価して利益対象のみ返す。"""
        results: list[ProfitResult] = []
        for item in items:
            r = self.evaluate(item)
            if r:
                results.append(r)
            time.sleep(random.uniform(0.8, 2.0))  # 礼儀正しい間隔

        results.sort(key=lambda x: x.profit, reverse=True)
        logger.info(
            f"利益対象: {len(results)} 件 / {len(items)} 件 "
            f"（閾値 {cfg.MIN_PROFIT_RATE:.0%}）"
        )
        return results
