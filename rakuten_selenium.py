"""
楽天市場 価格スクレイパー（requests + BeautifulSoup）
API キー不要。楽天検索ページから最安値を直接取得する。
"""

import re
import time
import random
from urllib.parse import quote
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

import selenium_config as cfg
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

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class RakutenScraper:
    SEARCH_URL = "https://search.rakuten.co.jp/search/mall/{keyword}/?s=2"  # s=2: 価格昇順

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
        )

    def _get_headers(self) -> dict:
        return {"User-Agent": random.choice(_USER_AGENTS)}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch_html(self, keyword: str) -> str:
        url = self.SEARCH_URL.format(keyword=quote(keyword))
        logger.debug(f"楽天 GET {url}")
        resp = self.session.get(url, headers=self._get_headers(), timeout=15)
        resp.raise_for_status()
        return resp.text

    def _parse_cheapest(self, html: str) -> Optional[dict]:
        """
        楽天検索結果ページから最安値商品の情報を取得。
        複数のセレクター候補をフォールバック付きで試みる。
        """
        soup = BeautifulSoup(html, "lxml")

        # ── 商品カード候補 ──
        card_selectors = [
            "div.searchresultitems div.item",   # 旧レイアウト
            "div[data-rat-itemid]",              # 新レイアウト
            "li.item",
            "div.item-details",
        ]

        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                break

        if not cards:
            # フォールバック: 価格テキストを直接探す
            return self._parse_fallback(soup)

        items = []
        for card in cards[:20]:  # 先頭 20件だけ見る
            try:
                # 商品名
                name = ""
                for name_sel in [".title", ".item-name", "h2", "a.title"]:
                    el = card.select_one(name_sel)
                    if el:
                        name = el.get_text(strip=True)
                        break

                # 価格
                price = 0
                for price_sel in [
                    ".price span",
                    ".important",
                    ".price",
                    "span[class*='price']",
                ]:
                    el = card.select_one(price_sel)
                    if el:
                        raw = el.get_text(strip=True)
                        digits = re.sub(r"[^\d]", "", raw)
                        if digits:
                            price = int(digits)
                            break

                # 店名
                shop = ""
                for shop_sel in [".shop-name", ".shopname", ".by-text a", "a[href*='shop']"]:
                    el = card.select_one(shop_sel)
                    if el:
                        shop = el.get_text(strip=True)
                        break

                # URL
                url = ""
                link = card.select_one("a.title, a[href*='item.rakuten']")
                if link:
                    url = link.get("href", "")

                # 画像
                image_url = ""
                img = card.select_one("img")
                if img:
                    image_url = img.get("src") or img.get("data-src") or ""

                if name and price > 0:
                    items.append(
                        {
                            "name":      name,
                            "price":     price,
                            "shop":      shop,
                            "url":       url,
                            "image_url": image_url,
                        }
                    )
            except Exception:
                continue

        if not items:
            return None

        # 最安値を返す
        return min(items, key=lambda x: x["price"])

    def _parse_fallback(self, soup: BeautifulSoup) -> Optional[dict]:
        """
        構造が変わった場合のフォールバックパース。
        ページ内で最初に見つかる価格テキストと近傍リンクを返す。
        """
        # "¥X,XXX" のパターンを探す
        price_tags = soup.find_all(string=re.compile(r"[¥￥]\s*[\d,]+"))
        for tag in price_tags:
            raw = re.sub(r"[^\d]", "", tag)
            if not raw:
                continue
            price = int(raw)
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

    def get_cheapest(self, keyword: str) -> Optional[dict]:
        """キーワードで楽天を検索して最安値商品情報を返す。"""
        try:
            html = self._fetch_html(keyword)
            result = self._parse_cheapest(html)
            if result:
                logger.debug(
                    f"楽天最安値: '{keyword[:20]}' → ¥{result['price']:,} ({result['shop'][:20]})"
                )
            return result
        except Exception as e:
            logger.warning(f"楽天スクレイピング失敗 '{keyword[:20]}': {e}")
            return None


# ── 利益計算 ─────────────────────────────────────────────────────────────────

class ProfitCalculator:
    def __init__(self):
        self.scraper = RakutenScraper()

    def _calc(
        self, mercari_price: int, rakuten_price: int
    ) -> tuple[int, int, float]:
        """
        Amazon 出品価格 = 楽天仕入れ値 × PRICE_MULTIPLIER_MIN〜MAX の中間値
        利益 = Amazon 出品価格 - 楽天仕入れ値 - Amazon 手数料 - FBA 手数料
        """
        mid_multiplier = (cfg.PRICE_MULTIPLIER_MIN + cfg.PRICE_MULTIPLIER_MAX) / 2.0
        amazon_price   = int(rakuten_price * mid_multiplier)

        amazon_fee     = int(amazon_price * cfg.AMAZON_FEE_RATE)
        profit         = amazon_price - rakuten_price - amazon_fee - cfg.AMAZON_FBA_FEE
        profit_rate    = profit / amazon_price if amazon_price > 0 else 0.0

        return amazon_price, profit, profit_rate

    def evaluate(self, item: SoldItem) -> Optional[ProfitResult]:
        """
        メルカリ売れ筋商品を楽天で検索し、利益が閾値を超えたら ProfitResult を返す。
        """
        rakuten = self.scraper.get_cheapest(item.name[:50])
        if not rakuten or rakuten["price"] == 0:
            return None

        amazon_price, profit, profit_rate = self._calc(item.price, rakuten["price"])

        logger.debug(
            f"{item.name[:25]} | 仕入¥{rakuten['price']:,} → "
            f"出品¥{amazon_price:,} | 利益¥{profit:,} ({profit_rate:.1%})"
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
            amazon_sell_price = amazon_price,
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
