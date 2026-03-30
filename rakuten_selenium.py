"""
楽天市場 価格検索モジュール（楽天ウェブサービス API 版）
Selenium 不使用。requests のみ。

API: IchibaItem/Search/20170706
"""

import re
import time
import random
from dataclasses import dataclass, field
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

import selenium_config as cfg
from mercari_selenium import SoldItem


# ── メルカリタイトルのクリーニング ─────────────────────────────────────────────

_NOISE_PATTERNS = re.compile(
    r"【[^】]*】|[\[（(][^)\]）]*[\]）)]"   # 【美品】 (送料込み) [即購入OK] など
    r"|送料込み|送料無料|匿名配送|即購入OK|値下げ不可|値下げ交渉可"
    r"|美品|超美品|極美品|新品未使用|新品未開封|未使用|中古|ジャンク"
    r"|セット|まとめ売り|おまけ付き|限定|レア|入手困難|廃盤"
    r"|♪|★|☆|◆|●|■|▲|※|!+|♡|❤"
    r"|#\S+",
    re.IGNORECASE,
)

_WHITESPACE = re.compile(r"\s+")


def clean_title(title: str) -> str:
    """メルカリ商品タイトルから装飾語・記号を除去して楽天検索キーワードにする。"""
    cleaned = _NOISE_PATTERNS.sub(" ", title)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # 短すぎたら元タイトルの先頭30文字を返す
    return cleaned if len(cleaned) >= 3 else title[:30]


def shorten_keyword(keyword: str) -> str:
    """キーワードを短縮する（先頭3単語に絞る）。"""
    words = keyword.split()
    return " ".join(words[:3]) if len(words) > 3 else keyword[:20]


# ── データクラス ───────────────────────────────────────────────────────────────

@dataclass
class RakutenItem:
    """楽天 API の検索結果 1件分"""
    name:         str
    price:        int
    url:          str
    shop_name:    str
    image_url:    str
    review_count: int = 0


@dataclass
class ProfitResult:
    """利益計算結果"""
    mercari_item:      SoldItem
    rakuten_item:      RakutenItem
    amazon_sell_price: int     # 出品予定価格（Mercari 実績売価）
    profit:            int     # 純利益額（手数料+送料控除後）
    profit_rate:       float   # 利益率 0.0〜1.0

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
    @property
    def rakuten_review_count(self) -> int: return self.rakuten_item.review_count


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
            raise ValueError("RAKUTEN_APP_ID が未設定です。.env を確認してください。")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def _call(self, keyword: str, hits: int = 30) -> tuple[int, list[dict]]:
        """API を叩いて (count, Items) を返す。"""
        params: dict = {
            "applicationId": self._app_id,
            "keyword":        keyword,
            "hits":           hits,
            "sort":           "-reviewCount",  # レビュー数降順
            "minPrice":       500,             # 500円以下の無関係商品を除外
            "format":         "json",
            "formatVersion":  2,
        }
        if self._affiliate_id:
            params["affiliateId"] = self._affiliate_id

        resp = self._session.get(self.ENDPOINT, params=params, timeout=10)

        if resp.status_code == 400:
            logger.debug(
                f"楽天API 400: '{keyword[:30]}' "
                f"→ {resp.json().get('error_description', '')}"
            )
            return 0, []

        resp.raise_for_status()
        data = resp.json()
        return data.get("count", 0), data.get("Items", [])

    def _to_rakuten_item(self, raw: dict) -> Optional[RakutenItem]:
        """API レスポンスの 1件を RakutenItem に変換する。"""
        price = raw.get("itemPrice", 0)
        if price == 0:
            return None
        images    = raw.get("mediumImageUrls") or []
        image_url = images[0].get("imageUrl", "") if images else ""
        return RakutenItem(
            name         = raw.get("itemName", ""),
            price        = price,
            url          = raw.get("itemUrl", ""),
            shop_name    = raw.get("shopName", ""),
            image_url    = image_url,
            review_count = raw.get("reviewCount", 0),
        )

    def search(self, keyword: str) -> Optional[RakutenItem]:
        """
        キーワードで楽天市場を検索し、レビュー最多商品を返す。
        hitCount=0 なら短縮キーワードで再検索を 1回だけ試みる。
        """
        # ── 1回目: クリーニング済みキーワードでそのまま検索 ──
        try:
            count, items = self._call(keyword)
        except Exception as e:
            logger.warning(f"楽天API 失敗 '{keyword[:30]}': {e}")
            return None

        if count == 0 or not items:
            # ── 2回目: 短縮キーワードで再検索 ──
            short = shorten_keyword(keyword)
            if short == keyword:
                logger.debug(f"楽天API: '{keyword[:30]}' → hitCount=0 スキップ")
                return None
            logger.debug(f"楽天API: '{keyword[:25]}' hitCount=0 → 短縮再検索 '{short}'")
            try:
                count, items = self._call(short)
            except Exception as e:
                logger.warning(f"楽天API 再検索失敗 '{short}': {e}")
                return None
            if count == 0 or not items:
                logger.debug(f"楽天API: '{short}' → 再検索も hitCount=0")
                return None

        result = self._to_rakuten_item(items[0])
        if result:
            logger.debug(
                f"楽天: '{keyword[:20]}' → ¥{result.price:,} "
                f"({result.shop_name[:15]}) レビュー{result.review_count}件"
            )
        return result


# ── 利益計算 ──────────────────────────────────────────────────────────────────

class ProfitCalculator:
    def __init__(self):
        self.client = RakutenAPIClient()

    @staticmethod
    def _calc(sell_price: int, buy_price: int) -> tuple[int, float]:
        """
        純利益 = 売価 - メルカリ手数料(10%) - 送料(600) - 仕入れ価格
        利益率 = 純利益 / 売価
        """
        fee         = int(sell_price * cfg.MERCARI_FEE_RATE)
        profit      = sell_price - fee - cfg.SHIPPING_FEE - buy_price
        profit_rate = profit / sell_price if sell_price > 0 else 0.0
        return profit, profit_rate

    def evaluate(self, item: SoldItem) -> Optional[ProfitResult]:
        """利益率・純利益の両方が閾値を超えた場合のみ ProfitResult を返す。"""
        # タイトルから装飾語を除去して検索キーワードにする
        search_kw = clean_title(item.name)
        rakuten   = self.client.search(search_kw)
        if not rakuten:
            return None

        # ── 50% フィルタ: 楽天価格がメルカリの半額未満 → 別商品の可能性 ──
        if rakuten.price < item.price * 0.5:
            logger.debug(
                f"SKIP(50%): {item.name[:25]} | "
                f"楽天¥{rakuten.price:,} < メルカリ¥{item.price:,}×50%"
            )
            return None

        profit, profit_rate = self._calc(item.price, rakuten.price)

        # ── 純利益額フィルタ: ¥500 未満は割に合わない ──
        if profit < cfg.MIN_PROFIT_AMOUNT:
            logger.debug(
                f"SKIP(利益額): {item.name[:25]} | 純利益¥{profit:,} < ¥{cfg.MIN_PROFIT_AMOUNT:,}"
            )
            return None

        # ── 利益率フィルタ ──
        if profit_rate < cfg.MIN_PROFIT_RATE:
            logger.debug(
                f"SKIP(利益率): {item.name[:25]} | {profit_rate:.1%} < {cfg.MIN_PROFIT_RATE:.0%}"
            )
            return None

        logger.info(
            f"HIT: {item.name[:25]} | 仕入¥{rakuten.price:,} → "
            f"出品¥{item.price:,} | 純利益¥{profit:,} ({profit_rate:.1%}) "
            f"レビュー{rakuten.review_count}件"
        )

        return ProfitResult(
            mercari_item      = item,
            rakuten_item      = rakuten,
            amazon_sell_price = item.price,
            profit            = profit,
            profit_rate       = profit_rate,
        )

    def filter_all(self, items: list[SoldItem]) -> list[ProfitResult]:
        """全商品を評価して利益対象のみ返す。フィルタ別の落ち件数をログに出力。"""
        results: list[ProfitResult] = []
        skip_no_rakuten = 0   # 楽天ヒットなし
        skip_50pct      = 0   # 50% フィルタ
        skip_amount     = 0   # 純利益額不足
        skip_rate       = 0   # 利益率不足

        for item in items:
            search_kw = clean_title(item.name)
            rakuten   = self.client.search(search_kw)

            if not rakuten:
                skip_no_rakuten += 1
                time.sleep(random.uniform(1.0, 2.0))
                continue

            if rakuten.price < item.price * 0.5:
                skip_50pct += 1
                logger.debug(
                    f"SKIP(50%): {item.name[:25]} | "
                    f"楽天¥{rakuten.price:,} < メルカリ¥{item.price:,}×50%"
                )
                time.sleep(random.uniform(1.0, 2.0))
                continue

            profit, profit_rate = self._calc(item.price, rakuten.price)

            if profit < cfg.MIN_PROFIT_AMOUNT:
                skip_amount += 1
                logger.debug(
                    f"SKIP(利益額): {item.name[:25]} | 純利益¥{profit:,}"
                )
                time.sleep(random.uniform(1.0, 2.0))
                continue

            if profit_rate < cfg.MIN_PROFIT_RATE:
                skip_rate += 1
                logger.debug(
                    f"SKIP(利益率): {item.name[:25]} | {profit_rate:.1%}"
                )
                time.sleep(random.uniform(1.0, 2.0))
                continue

            logger.info(
                f"HIT: {item.name[:25]} | 仕入¥{rakuten.price:,} → "
                f"出品¥{item.price:,} | 純利益¥{profit:,} ({profit_rate:.1%}) "
                f"レビュー{rakuten.review_count}件"
            )
            results.append(ProfitResult(
                mercari_item      = item,
                rakuten_item      = rakuten,
                amazon_sell_price = item.price,
                profit            = profit,
                profit_rate       = profit_rate,
            ))
            time.sleep(random.uniform(1.0, 2.0))

        results.sort(key=lambda x: x.profit, reverse=True)
        matched = len(items) - skip_no_rakuten

        logger.info("── フィルタ集計 ──")
        logger.info(f"  メルカリ入力:     {len(items)} 件")
        logger.info(f"  楽天マッチ:       {matched} 件  （楽天ヒットなし: {skip_no_rakuten} 件）")
        logger.info(f"  50%価格フィルタ:  -{skip_50pct} 件")
        logger.info(f"  純利益額フィルタ: -{skip_amount} 件  （< ¥{cfg.MIN_PROFIT_AMOUNT:,}）")
        logger.info(f"  利益率フィルタ:   -{skip_rate} 件  （< {cfg.MIN_PROFIT_RATE:.0%}）")
        logger.info(f"  → 最終通過:       {len(results)} 件")

        return results
