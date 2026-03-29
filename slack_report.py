"""
Slack 日次レポート送信モジュール
利益ランキング・純利益額・レビュー数・メルカリURL 付き。
"""

from datetime import datetime
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from loguru import logger

import selenium_config as cfg
from rakuten_selenium import ProfitResult


class SlackReporter:
    def __init__(self):
        self.client  = WebClient(token=cfg.SLACK_BOT_TOKEN)
        self.channel = cfg.SLACK_CHANNEL

    # ── ブロック生成 ──────────────────────────────────────────────────────────

    def _header_block(self, date_str: str) -> dict:
        return {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":bar_chart: セドリ日次レポート  {date_str}",
            },
        }

    def _summary_block(self, results: list[ProfitResult]) -> dict:
        total_profit = sum(r.profit for r in results)
        avg_rate     = sum(r.profit_rate for r in results) / len(results) if results else 0
        top_profit   = results[0].profit if results else 0

        return {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*対象商品数*\n{len(results)} 件"},
                {"type": "mrkdwn", "text": f"*推定合計純利益*\n¥{total_profit:,}"},
                {"type": "mrkdwn", "text": f"*平均利益率*\n{avg_rate:.1%}"},
                {"type": "mrkdwn", "text": f"*最高純利益（1件）*\n¥{top_profit:,}"},
            ],
        }

    def _item_block(self, rank: int, r: ProfitResult) -> dict:
        medal = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}.get(rank, f"*{rank}.*")
        rate_bar = self._rate_bar(r.profit_rate)

        # メルカリ商品 URL（取れていれば表示）
        mercari_link = (
            f"<{r.mercari_item.item_url}|メルカリで見る>"
            if r.mercari_item.item_url
            else "URL なし"
        )

        text = (
            f"{medal}  *{r.mercari_item.name[:40]}*\n"
            f"  :label: キーワード: `{r.mercari_item.keyword}`　"
            f"メルカリ売れ数: *{r.mercari_item.sold_count} 件 / {cfg.DAYS_LOOKBACK}日*\n"
            f"  :link: {mercari_link}\n"
            f"  :shopping_trolley: 楽天仕入: *¥{r.rakuten_price:,}*  "
            f"（{r.rakuten_shop[:25] or '不明'}）"
            f"　:star: レビュー *{r.rakuten_review_count}* 件\n"
            f"  :package: メルカリ出品: *¥{r.amazon_sell_price:,}*\n"
            f"  :moneybag: *純利益: ¥{r.profit:,}*  "
            f"（手数料{cfg.MERCARI_FEE_RATE:.0%} + 送料¥{cfg.SHIPPING_FEE:,} 控除後）\n"
            f"  :chart_with_upwards_trend: 利益率: {rate_bar}  `{r.profit_rate:.1%}`"
        )

        block: dict = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }

        if r.rakuten_url:
            block["accessory"] = {
                "type": "button",
                "text":  {"type": "plain_text", "text": "楽天で確認"},
                "url":   r.rakuten_url[:3000],
                "style": "primary",
            }

        return block

    @staticmethod
    def _rate_bar(rate: float) -> str:
        filled = min(int(rate / 0.05), 10)
        return ":green_square:" * filled + ":white_square_button:" * (10 - filled)

    def _footer_block(self) -> dict:
        return {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"自動生成 by セドリBot  |  "
                        f"利益率 >= {cfg.MIN_PROFIT_RATE:.0%}  |  "
                        f"純利益 >= ¥{cfg.MIN_PROFIT_AMOUNT:,}  |  "
                        f"直近 {cfg.DAYS_LOOKBACK} 日 / {cfg.MIN_SALES_COUNT} 件以上"
                    ),
                }
            ],
        }

    # ── 公開メソッド ─────────────────────────────────────────────────────────

    def build_blocks(self, results: list[ProfitResult]) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d (%a)")
        blocks: list[dict] = [
            self._header_block(today),
            {"type": "divider"},
        ]

        if not results:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":zzz:  本日は利益対象商品が見つかりませんでした。",
                },
            })
        else:
            blocks.append(self._summary_block(results))
            blocks.append({"type": "divider"})

            for rank, r in enumerate(results[:20], start=1):
                blocks.append(self._item_block(rank, r))
                blocks.append({"type": "divider"})

            if len(results) > 20:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"_… 他 {len(results) - 20} 件_",
                    },
                })

        blocks.append(self._footer_block())
        return blocks

    def send(self, results: list[ProfitResult]) -> bool:
        if not cfg.SLACK_BOT_TOKEN or not cfg.SLACK_CHANNEL:
            logger.warning("Slack 未設定のためレポートをスキップします")
            return False

        total = sum(r.profit for r in results)
        fallback = f"セドリ日次レポート: {len(results)} 件、推定純利益 ¥{total:,}"

        try:
            self.client.chat_postMessage(
                channel=self.channel,
                text=fallback,
                blocks=self.build_blocks(results),
            )
            logger.info(f"Slack レポート送信完了: {len(results)} 件")
            return True
        except SlackApiError as e:
            logger.error(f"Slack 送信エラー: {e.response['error']}")
            return False

    def send_error(self, message: str):
        if not cfg.SLACK_BOT_TOKEN or not cfg.SLACK_CHANNEL:
            return
        try:
            self.client.chat_postMessage(
                channel=self.channel,
                text=f":rotating_light: セドリBot エラー\n```{message}```",
            )
        except SlackApiError:
            pass

    def print_report(self, results: list[ProfitResult]):
        """ターミナルへの出力（Slack 未設定 or ドライラン時）"""
        print("\n" + "=" * 80)
        print(f"  セドリ日次レポート  {datetime.now().strftime('%Y-%m-%d')}")
        print("=" * 80)
        if not results:
            print("  利益対象商品なし")
        else:
            total = sum(r.profit for r in results)
            print(f"  対象: {len(results)} 件  |  推定合計純利益: ¥{total:,}")
            print(f"  フィルタ: 利益率 >= {cfg.MIN_PROFIT_RATE:.0%}, "
                  f"純利益 >= ¥{cfg.MIN_PROFIT_AMOUNT:,}")
            print("-" * 80)
            for i, r in enumerate(results, 1):
                print(
                    f"  {i:2}. {r.mercari_item.name[:30]:<30} "
                    f"仕入¥{r.rakuten_price:>7,}  "
                    f"→ 出品¥{r.amazon_sell_price:>7,}  "
                    f"純利益¥{r.profit:>6,}  ({r.profit_rate:.1%})  "
                    f"レビュー{r.rakuten_review_count:>4}件"
                )
                if r.mercari_item.item_url:
                    print(f"      {r.mercari_item.item_url}")
        print("=" * 80 + "\n")
