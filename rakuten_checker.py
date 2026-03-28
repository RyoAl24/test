"""
Rakuten Ichiba API price checker.
Searches for items on Rakuten and calculates profit margin vs Mercari selling price.
"""

import time
import urllib.parse
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from pydantic import BaseModel

import config
from mercari_scraper import MercariItem


class RakutenItem(BaseModel):
    item_code: str
    name: str
    price: int
    shop_name: str
    item_url: str
    image_url: str
    mercari_item: MercariItem
    profit: int
    profit_rate: float
    suggested_amazon_price: int


class RakutenChecker:
    SEARCH_URL = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

    def __init__(self):
        self.session = requests.Session()
        self.app_id = config.RAKUTEN_APP_ID
        self.affiliate_id = config.RAKUTEN_AFFILIATE_ID

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def search(self, keyword: str, hits: int = 10) -> list[dict]:
        """Search Rakuten Ichiba for items."""
        params = {
            "applicationId": self.app_id,
            "affiliateId": self.affiliate_id,
            "keyword": keyword,
            "hits": hits,
            "sort": "+itemPrice",  # cheapest first
            "minPrice": 100,
            "format": "json",
            "formatVersion": 2,
        }
        resp = self.session.get(self.SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("Items", [])

    def _calculate_profit(
        self, mercari_sell_price: int, rakuten_buy_price: int
    ) -> tuple[int, float]:
        """
        profit = mercari_price - rakuten_price - amazon_fees
        profit_rate = profit / mercari_price
        """
        amazon_fee = int(mercari_sell_price * config.AMAZON_FEE_RATE)
        total_cost = rakuten_buy_price + amazon_fee + config.AMAZON_FBA_FEE
        profit = mercari_sell_price - total_cost
        profit_rate = profit / mercari_sell_price if mercari_sell_price > 0 else 0
        return profit, profit_rate

    def _suggested_price(self, rakuten_price: int) -> int:
        """Suggest Amazon listing price based on Rakuten purchase price."""
        mid = (config.PRICE_MULTIPLIER_MIN + config.PRICE_MULTIPLIER_MAX) / 2
        return int(rakuten_price * mid)

    def find_profitable_source(self, mercari_item: MercariItem) -> Optional[RakutenItem]:
        """
        Find the cheapest Rakuten listing for the item and check profitability.
        Returns RakutenItem if profit_rate >= MIN_PROFIT_RATE, else None.
        """
        # Use item name trimmed to key words for search
        search_query = mercari_item.name[:50]
        logger.debug(f"Rakuten search: '{search_query}'")

        try:
            rakuten_items = self.search(search_query)
        except Exception as e:
            logger.warning(f"Rakuten search failed for '{search_query}': {e}")
            return None

        if not rakuten_items:
            logger.debug(f"No Rakuten results for '{search_query}'")
            return None

        # Pick cheapest item
        best = min(rakuten_items, key=lambda x: x.get("itemPrice", 999_999_999))
        rakuten_price = best.get("itemPrice", 0)

        if rakuten_price == 0:
            return None

        # We sell on Amazon at Mercari's proven price (or slightly above)
        target_sell_price = int(mercari_item.price * 1.05)  # 5% above Mercari price
        profit, profit_rate = self._calculate_profit(target_sell_price, rakuten_price)

        logger.debug(
            f"Item: {mercari_item.name[:30]} | "
            f"Rakuten: ¥{rakuten_price:,} | "
            f"Target sell: ¥{target_sell_price:,} | "
            f"Profit rate: {profit_rate:.1%}"
        )

        if profit_rate < config.MIN_PROFIT_RATE:
            return None

        suggested_price = self._suggested_price(rakuten_price)

        return RakutenItem(
            item_code=best.get("itemCode", ""),
            name=best.get("itemName", ""),
            price=rakuten_price,
            shop_name=best.get("shopName", ""),
            item_url=best.get("itemUrl", ""),
            image_url=(best.get("mediumImageUrls") or [""])[0],
            mercari_item=mercari_item,
            profit=profit,
            profit_rate=profit_rate,
            suggested_amazon_price=suggested_price,
        )

    def filter_profitable(self, mercari_items: list[MercariItem]) -> list[RakutenItem]:
        """Filter all Mercari items to find profitable Rakuten sources."""
        profitable = []
        for item in mercari_items:
            result = self.find_profitable_source(item)
            if result:
                profitable.append(result)
            time.sleep(0.5)  # Polite delay

        logger.info(f"Profitable items found: {len(profitable)} / {len(mercari_items)}")
        return profitable
