"""
Amazon SP-API auto-listing module.
Lists profitable items on Amazon JP marketplace.
"""

import time
import json
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from pydantic import BaseModel

import config
from rakuten_checker import RakutenItem


class AmazonListingResult(BaseModel):
    success: bool
    asin: Optional[str]
    sku: str
    listing_price: int
    error_message: Optional[str]
    rakuten_item: RakutenItem


class AmazonSPClient:
    """Minimal Amazon SP-API client with LWA token management."""

    LWA_URL = "https://api.amazon.com/auth/o2/token"
    SP_API_BASE = "https://sellingpartnerapi-fe.amazon.com"

    def __init__(self):
        self.client_id = config.AMAZON_CLIENT_ID
        self.client_secret = config.AMAZON_CLIENT_SECRET
        self.refresh_token = config.AMAZON_REFRESH_TOKEN
        self.marketplace_id = config.AMAZON_MARKETPLACE_ID
        self.seller_id = config.AMAZON_SELLER_ID
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

    def _get_access_token(self) -> str:
        """Get or refresh LWA access token."""
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        if not self.refresh_token:
            raise RuntimeError(
                "AMAZON_REFRESH_TOKEN is not set. "
                "Please complete SP-API OAuth flow first."
            )

        resp = requests.post(
            self.LWA_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        logger.debug("Amazon LWA token refreshed.")
        return self._access_token

    def _headers(self) -> dict:
        token = self._get_access_token()
        return {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def search_catalog(self, keywords: str) -> Optional[str]:
        """Search Amazon catalog for ASIN by keywords."""
        url = f"{self.SP_API_BASE}/catalog/2022-04-01/items"
        params = {
            "keywords": keywords,
            "marketplaceIds": self.marketplace_id,
            "includedData": "identifiers,summaries",
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                return items[0].get("asin")
        elif resp.status_code == 400:
            logger.debug(f"No catalog result for '{keywords}'")
        else:
            resp.raise_for_status()
        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def create_listing(self, sku: str, asin: str, price: int, title: str) -> dict:
        """Create or update a listing via Listings Items API."""
        url = (
            f"{self.SP_API_BASE}/listings/2021-08-01/items"
            f"/{self.seller_id}/{sku}"
        )
        params = {"marketplaceIds": self.marketplace_id}
        body = {
            "productType": "PRODUCT",
            "requirements": "LISTING_OFFER_ONLY",
            "attributes": {
                "condition_type": [{"value": "new_new", "marketplace_id": self.marketplace_id}],
                "item_name": [{"value": title, "marketplace_id": self.marketplace_id}],
                "purchasable_offer": [
                    {
                        "currency": "JPY",
                        "our_price": [{"schedule": [{"value_with_tax": price}]}],
                        "marketplace_id": self.marketplace_id,
                    }
                ],
                "merchant_suggested_asin": [
                    {"value": asin, "marketplace_id": self.marketplace_id}
                ],
            },
        }
        resp = requests.put(
            url,
            headers=self._headers(),
            params=params,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


class AmazonLister:
    def __init__(self):
        self.client = AmazonSPClient()

    def _generate_sku(self, item_name: str, price: int) -> str:
        """Generate a unique SKU based on item name + price."""
        raw = f"{item_name[:20]}-{price}"
        return "SEDORI-" + hashlib.md5(raw.encode()).hexdigest()[:10].upper()

    def list_item(self, rakuten_item: RakutenItem) -> AmazonListingResult:
        """Attempt to list a single item on Amazon."""
        name = rakuten_item.mercari_item.name
        price = rakuten_item.suggested_amazon_price
        sku = self._generate_sku(name, price)

        # Step 1: Find ASIN
        asin = None
        try:
            asin = self.client.search_catalog(name[:100])
        except Exception as e:
            logger.warning(f"ASIN search failed for '{name[:30]}': {e}")

        if not asin:
            logger.warning(f"No ASIN found for '{name[:30]}', skipping listing.")
            return AmazonListingResult(
                success=False,
                asin=None,
                sku=sku,
                listing_price=price,
                error_message="ASIN not found",
                rakuten_item=rakuten_item,
            )

        # Step 2: Create listing
        try:
            result = self.client.create_listing(sku, asin, price, name)
            logger.info(
                f"Listed: '{name[:30]}' | ASIN: {asin} | SKU: {sku} | Price: ¥{price:,}"
            )
            return AmazonListingResult(
                success=True,
                asin=asin,
                sku=sku,
                listing_price=price,
                error_message=None,
                rakuten_item=rakuten_item,
            )
        except Exception as e:
            logger.error(f"Listing failed for '{name[:30]}': {e}")
            return AmazonListingResult(
                success=False,
                asin=asin,
                sku=sku,
                listing_price=price,
                error_message=str(e),
                rakuten_item=rakuten_item,
            )

    def list_all(self, rakuten_items: list[RakutenItem]) -> list[AmazonListingResult]:
        """List all profitable items on Amazon."""
        results = []
        for item in rakuten_items:
            result = self.list_item(item)
            results.append(result)
            time.sleep(1.0)  # SP-API rate limit

        success = sum(1 for r in results if r.success)
        logger.info(f"Amazon listings: {success} succeeded / {len(results)} total")
        return results
