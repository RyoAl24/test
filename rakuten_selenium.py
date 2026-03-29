"""
楽天市場 価格検索モジュール（楽天ウェブサービス API 版）
Selenium / スクレイピング不要。requests のみ使用。

API: IchibaItem/Search/20170706
  https://webservice.rakuten.co.jp/documentation/ichiba-item-search
"""

import time
import random
from dataclasses import dataclass
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

import selenium_config as cfg
from mercari_selenium import SoldItem


# ── データクラス ───────────────────────────────────────────────────────────────

@dataclass
class RakutenItem:
    """楽天 API の検索結果 1件分"""
    name:      str
    price:     int
    url:       str
    shop_name: str
    image_url: str


@dataclass
class ProfitResult:
    """利益計算結果"""
    mercari_item:      SoldItem
    rakuten_item:      RakutenItem
    amazon_sell_price: int     # 出品予定価格（Mercari 実績売価）
    profit:            int     # 利益額（円）
    profit_rate:       float   # 利益率 0.0〜1.0

    # slack_report.py が参照するプロパティ（後方互換）
    @property
    def rakuten_name(self)      -> str:   return self.rakuten_item.name
    @property
    def rakuten_price(self)     -> int:   return self.rakuten_item.price
    @property
    def rakuten_shop(self)      -> str:   return self.rakuten_item.shop_name
    @property
    def rakuten_url(self)       -> str:   return self.rakuten_item.url
    @property
    def rakuten_image_url(self) -> str:   return self.rakuten_item.image_url


# ── 楽天 API クライアント ──────────────────────────────────────────────────────

class RakutenAPIClient:
    ENDPOINT = (
        "https://app.rakuten.co.jp/services/api"
        "/IchibaItem/Search/20170706"
    )

    def __init__(self):
        self._app_id       = cfg.RAKUTEN_APP_ID
        self._affiliate_id = cfg.RAKUTEN_AFFILIATE_ID
        self._session      = requests.Session()

        if not self._app_id:
            raise ValueError(
                "RAKUTEN_APP_ID が未設定です。.env を確認してください。"
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def _call(self, keyword: str, hits: int = 30) -> tuple[int, list[dict]]:
        """API を叩いて (hitCount, Items リスト) を返す。"""
        params: dict = {
            "applicationId": self._app_id,
            "keyword":        keyword,
            "hits":           hits,
            "sort":           "-reviewCount",  # レビュー数降順（実績ある商品優先）
            "minPrice":       500,             # 明らかに無関係な激安品を除外
            "format":         "json",
            "formatVersion":  2,
        }
        if self._affiliate_id:
            params["affiliateId"] = self._affiliate_id

        resp = self._session.get(self.ENDPOINT, params=params, timeout=10)

        # 400 は無効キーワードなどの恒久エラー → リトライしない
        if resp.status_code == 400:
            logger.debug(
                f"楽天API 400: '{keyword[:30]}' "
                f"→ {resp.json().get('error_description', '')}"
            )
            return 0, []

        resp.raise_for_status()
        data = resp.json()
        return data.get("count", 0), data.get("Items", [])

    def get_best(self, keyword: str) -> Optional[RakutenItem]:
        """
        キーワードで楽天市場を検索し、レビュー数最多の RakutenItem を返す。
        hitCount が 0 の場合・エラー時は None を返す。
        """
        try:
            hit_count, items = self._call(keyword)
        except Exception as e:
            logger.warning(f"楽天API 失敗 '{keyword[:30]}': {e}")
            return None

        # hitCount == 0 → 関連商品なし
        if hit_count == 0 or not items:
            logger.debug(f"楽天API: '{keyword[:30]}' → hitCount=0 スキップ")
            return None

        top = items[0]  # -reviewCount ソート済みなので先頭が最多レビュー
        price = top.get("itemPrice", 0)
        if price == 0:
            return None

        images    = top.get("mediumImageUrls") or []
        image_url = images[0].get("imageUrl", "") if images else ""

        result = RakutenItem(
            name      = top.get("itemName", ""),
            price     = price,
            url       = top.get("itemUrl", ""),
            shop_name = top.get("shopName", ""),
            image_url = image_url,
        )
        logger.debug(
            f"楽天ベスト: '{keyword[:20]}' "
            f"→ ¥{price:,}  ({result.shop_name[:20] or '不明'})"
        )
        return result


# ── 利益計算 ──────────────────────────────────────────────────────────────────

class ProfitCalculator:
    def __init__(self):
        self.client = RakutenAPIClient()

    def _calc(self, sell_price: int, buy_price: int) -> tuple[int, float]:
        """
        profit      = sell_price - (sell_price × MERCARI_FEE_RATE) - SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price
        """
        fee         = int(sell_price * cfg.MERCARI_FEE_RATE)
        profit      = sell_price - fee - cfg.SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price if sell_price > 0 else 0.0
        return profit, profit_rate

    def evaluate(self, item: SoldItem) -> Optional[ProfitResult]:
        """利益率 >= MIN_PROFIT_RATE なら ProfitResult を返す。"""
        rakuten = self.client.get_best(item.name[:50])
        if not rakuten:
            return None

        # 楽天価格がメルカリ価格の 50% 未満 → キーワードがズレた別商品と判断してスキップ
        if rakuten.price < item.price * 0.5:
            logger.debug(
                f"{item.name[:25]} | 楽天¥{rakuten.price:,} < メルカリ¥{item.price:,}×50% → スキップ"
            )
            return None

        profit, profit_rate = self._calc(item.price, rakuten.price)

        logger.debug(
            f"{item.name[:25]} | 仕入¥{rakuten.price:,} → "
            f"出品¥{item.price:,} | 利益¥{profit:,} ({profit_rate:.1%})"
        )

        if profit_rate < cfg.MIN_PROFIT_RATE:
            return None

        return ProfitResult(
            mercari_item      = item,
            rakuten_item      = rakuten,
            amazon_sell_price = item.price,
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
            time.sleep(random.uniform(1.0, 2.0))  # 楽天 API 推奨: 1秒以上

        results.sort(key=lambda x: x.profit, reverse=True)
        logger.info(
            f"利益対象: {len(results)} 件 / {len(items)} 件 "
            f"（閾値 {cfg.MIN_PROFIT_RATE:.0%}）"
        )
        return results
