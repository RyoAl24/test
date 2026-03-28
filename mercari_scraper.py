"""
Mercari scraper module.
Extracts sold items from the past N days with sales count >= MIN_SALES_COUNT.
Uses requests + BeautifulSoup with rotation to avoid IP blocks.
"""

import time
import random
import hashlib
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from pydantic import BaseModel

import config


class MercariItem(BaseModel):
    item_id: str
    name: str
    price: int
    sold_count: int
    category: str
    image_url: str
    item_url: str
    keyword: str
    sold_within_days: int


class MercariScraper:
    BASE_URL = "https://api.mercari.jp/v2/entities:search"
    ITEM_URL = "https://jp.mercari.com/item/"

    def __init__(self):
        self.ua = UserAgent()
        self.session = requests.Session()
        self._setup_proxy()

    def _setup_proxy(self):
        if config.PROXY_HOST and config.PROXY_PORT:
            proxy_url = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"
            if config.PROXY_USER and config.PROXY_PASS:
                proxy_url = (
                    f"http://{config.PROXY_USER}:{config.PROXY_PASS}"
                    f"@{config.PROXY_HOST}:{config.PROXY_PORT}"
                )
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info(f"Proxy configured: {config.PROXY_HOST}:{config.PROXY_PORT}")

    def _get_headers(self) -> dict:
        return {
            "User-Agent": self.ua.random,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://jp.mercari.com",
            "Referer": "https://jp.mercari.com/",
            "DPoP": self._generate_dpop(),
            "X-Platform": "web",
        }

    def _generate_dpop(self) -> str:
        """Generate a simple DPoP token stub (real impl would use JWK)."""
        ts = str(int(time.time()))
        return hashlib.sha256(ts.encode()).hexdigest()[:32]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _search_sold_items(self, keyword: str, page_token: str = "") -> dict:
        """Call Mercari search API for SOLD items."""
        params = {
            "search_condition": {
                "keyword": keyword,
                "status": ["ITEM_STATUS_SOLD_OUT"],
                "sort": "SORT_CREATED_TIME",
                "order": "ORDER_DESC",
            },
            "pageSize": 120,
            "pageToken": page_token,
        }

        # Mercari uses a JSON body POST for v2 search
        resp = self.session.post(
            self.BASE_URL,
            json=params,
            headers=self._get_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _is_within_days(self, updated_ts: int, days: int) -> bool:
        cutoff = datetime.now() - timedelta(days=days)
        item_dt = datetime.fromtimestamp(updated_ts)
        return item_dt >= cutoff

    def _count_recent_sales(self, keyword: str, item_name: str) -> int:
        """
        Estimate how many times a similar item sold recently
        by counting name-similar sold items in search results.
        """
        try:
            data = self._search_sold_items(keyword)
        except Exception as e:
            logger.warning(f"Search failed for '{keyword}': {e}")
            return 0

        items = data.get("items", [])
        name_lower = item_name.lower()
        count = 0
        for item in items:
            item_name_lower = item.get("name", "").lower()
            # Simple overlap check using words
            words = [w for w in name_lower.split() if len(w) > 2]
            if any(w in item_name_lower for w in words):
                updated = item.get("updated", 0)
                if self._is_within_days(updated, config.DAYS_LOOKBACK):
                    count += 1
        return count

    def scrape_keyword(self, keyword: str) -> list[MercariItem]:
        """Scrape sold items for a single keyword."""
        logger.info(f"Scraping Mercari for keyword: {keyword}")
        results = []
        seen_names: dict[str, dict] = {}  # name -> {count, item}

        page_token = ""
        pages_fetched = 0
        max_pages = 5

        while pages_fetched < max_pages:
            try:
                data = self._search_sold_items(keyword, page_token)
            except Exception as e:
                logger.error(f"Failed to fetch page {pages_fetched} for '{keyword}': {e}")
                break

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                updated = item.get("updated", 0)
                if not self._is_within_days(updated, config.DAYS_LOOKBACK):
                    continue

                name = item.get("name", "")
                price = item.get("price", 0)
                item_id = item.get("id", "")

                if not name or not price or not item_id:
                    continue

                # Deduplicate by normalized name
                key = name[:30].lower()
                if key in seen_names:
                    seen_names[key]["count"] += 1
                else:
                    seen_names[key] = {
                        "count": 1,
                        "item": {
                            "item_id": item_id,
                            "name": name,
                            "price": price,
                            "category": item.get("category", ""),
                            "image_url": (item.get("thumbnails") or [""])[0],
                            "item_url": f"{self.ITEM_URL}{item_id}",
                            "keyword": keyword,
                        },
                    }

            next_token = data.get("nextPageToken", "")
            if not next_token:
                break
            page_token = next_token
            pages_fetched += 1
            time.sleep(random.uniform(1.5, 3.0))

        # Filter by min sales count
        for key, val in seen_names.items():
            if val["count"] >= config.MIN_SALES_COUNT:
                d = val["item"]
                results.append(
                    MercariItem(
                        item_id=d["item_id"],
                        name=d["name"],
                        price=d["price"],
                        sold_count=val["count"],
                        category=d["category"],
                        image_url=d["image_url"],
                        item_url=d["item_url"],
                        keyword=keyword,
                        sold_within_days=config.DAYS_LOOKBACK,
                    )
                )

        logger.info(f"Found {len(results)} qualifying items for keyword: {keyword}")
        return results

    def scrape_all_keywords(self) -> list[MercariItem]:
        """Scrape all configured keywords."""
        all_items = []
        for kw in config.SEARCH_KEYWORDS:
            items = self.scrape_keyword(kw)
            all_items.extend(items)
            # Polite delay between keywords
            time.sleep(random.uniform(2.0, 5.0))

        logger.info(f"Total qualifying Mercari items: {len(all_items)}")
        return all_items
