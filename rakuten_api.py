"""
楽天市場 価格検索モジュール（楽天ウェブサービス API 版）
Selenium / スクレイピング不要。公式 API で最安値を取得する。

API ドキュメント:
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


# ── データクラス（rakuten_selenium.py と同一インターフェース） ──────────────────

@dataclass
class ProfitResult:
    """利益計算結果"""
    mercari_item:      SoldItem
    rakuten_name:      str
    rakuten_price:     int        # 仕入れ価格（円）
    rakuten_shop:      str
    rakuten_url:       str
    rakuten_image_url: str
    amazon_sell_price: int        # 出品予定価格（Mercari 実績売価）
    profit:            int        # 利益額（円）
    profit_rate:       float      # 利益率 0.0〜1.0


# ── 楽天 API クライアント ─────────────────────────────────────────────────────

class RakutenAPIClient:
    """
    楽天市場商品検索 API ラッパー。
    applicationId / affiliateId を .env から読み込む。
    """
    ENDPOINT = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

    def __init__(self):
        self.app_id       = cfg.RAKUTEN_APP_ID
        self.affiliate_id = cfg.RAKUTEN_AFFILIATE_ID
        self._session     = requests.Session()
        self._session.headers.update({"Accept-Encoding": "gzip, deflate"})

        if not self.app_id:
            raise ValueError(
                "RAKUTEN_APP_ID が未設定です。.env を確認してください。"
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def search(self, keyword: str, hits: int = 30) -> list[dict]:
        """
        キーワードで楽天市場を検索し、商品リストを返す。

        Parameters
        ----------
        keyword : 検索キーワード（日本語可）
        hits    : 取得件数（最大 30）

        Returns
        -------
        list[dict]  楽天 API の Items[].Item 部分のリスト
        """
        params = {
            "applicationId": self.app_id,
            "affiliateId":   self.affiliate_id,
            "keyword":        keyword,
            "hits":           hits,
            "sort":           "+itemPrice",   # 価格昇順
            "minPrice":       100,
            "format":         "json",
            "formatVersion":  2,
        }
        # affiliateId が空の場合はパラメーターごと除外する
        if not self.affiliate_id:
            del params["affiliateId"]

        resp = self._session.get(self.ENDPOINT, params=params, timeout=10)

        # 400: キーワード無効など → リトライしない
        if resp.status_code == 400:
            logger.debug(f"楽天API 400: '{keyword[:30]}' → {resp.json().get('error_description','')}")
            return []

        resp.raise_for_status()
        data = resp.json()
        return data.get("Items", [])

    def get_cheapest(self, keyword: str) -> Optional[dict]:
        """
        キーワードで最安値の商品情報を返す。
        返り値キー: name / price / shop / url / image_url
        """
        try:
            items = self.search(keyword)
        except Exception as e:
            logger.warning(f"楽天API 検索失敗 '{keyword[:30]}': {e}")
            return None

        if not items:
            logger.debug(f"楽天API: '{keyword[:30]}' → 結果なし")
            return None

        # API は +itemPrice（昇順）で返ってくるが、念のため最安を選ぶ
        cheapest = min(items, key=lambda x: x.get("itemPrice", 999_999_999))
        price    = cheapest.get("itemPrice", 0)
        if price == 0:
            return None

        # 画像 URL（mediumImageUrls は [{"imageUrl": "..."}] 形式）
        images    = cheapest.get("mediumImageUrls") or []
        image_url = images[0].get("imageUrl", "") if images else ""

        result = {
            "name":      cheapest.get("itemName", ""),
            "price":     price,
            "shop":      cheapest.get("shopName", ""),
            "url":       cheapest.get("itemUrl", ""),
            "image_url": image_url,
        }
        logger.debug(
            f"楽天最安値: '{keyword[:20]}' → ¥{price:,}  ({result['shop'][:20] or '不明'})"
        )
        return result


# ── 利益計算（rakuten_selenium.py と同一ロジック） ─────────────────────────────

class ProfitCalculator:
    def __init__(self):
        self.client = RakutenAPIClient()

    def _calc(self, sell_price: int, buy_price: int) -> tuple[int, float]:
        """
        profit      = sell_price - (sell_price × MERCARI_FEE_RATE) - SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price
        """
        mercari_fee = int(sell_price * cfg.MERCARI_FEE_RATE)
        profit      = sell_price - mercari_fee - cfg.SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price if sell_price > 0 else 0.0
        return profit, profit_rate

    def evaluate(self, item: SoldItem) -> Optional[ProfitResult]:
        """メルカリ売れ筋商品を楽天 API で検索し、利益率 >= MIN_PROFIT_RATE なら返す。"""
        rakuten = self.client.get_cheapest(item.name[:50])
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
            # 楽天 API の利用規約に従いリクエスト間隔を空ける（推奨: 1 秒以上）
            time.sleep(random.uniform(1.0, 2.0))

        results.sort(key=lambda x: x.profit, reverse=True)
        logger.info(
            f"利益対象: {len(results)} 件 / {len(items)} 件 "
            f"（閾値 {cfg.MIN_PROFIT_RATE:.0%}）"
        )
        return results
