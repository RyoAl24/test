"""
Slack daily report module.
Sends formatted report of profitable items and estimated profits.
"""

from datetime import datetime
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from loguru import logger

import config
from rakuten_checker import RakutenItem
from amazon_lister import AmazonListingResult


class SlackReporter:
    def __init__(self):
        self.client = WebClient(token=config.SLACK_BOT_TOKEN)
        self.channel = config.SLACK_CHANNEL_ID

    def _format_item_block(self, r: RakutenItem, listing: Optional[AmazonListingResult]) -> dict:
        listed = listing and listing.success
        status_emoji = ":white_check_mark:" if listed else ":warning:"
        asin_text = f"ASIN: `{listing.asin}`" if listed and listing.asin else "出品失敗"

        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{status_emoji} *{r.mercari_item.name[:40]}*\n"
                    f"  • Mercari売価: *¥{r.mercari_item.price:,}*  "
                    f"（{config.DAYS_LOOKBACK}日間 {r.mercari_item.sold_count}件売）\n"
                    f"  • 楽天仕入: ¥{r.price:,}  ({r.shop_name[:20]})\n"
                    f"  • Amazon予定価格: ¥{r.suggested_amazon_price:,}\n"
                    f"  • 利益: *¥{r.profit:,}* ({r.profit_rate:.1%})\n"
                    f"  • {asin_text}"
                ),
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "楽天で見る"},
                "url": r.item_url,
            },
        }

    def _build_message(
        self,
        rakuten_items: list[RakutenItem],
        listing_results: list[AmazonListingResult],
    ) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        total_profit = sum(r.profit for r in rakuten_items)
        listed_count = sum(1 for r in listing_results if r.success)

        listing_map = {r.rakuten_item.item_code: r for r in listing_results}

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":bar_chart: セドリ自動化 日次レポート ({today})",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*対象商品数*\n{len(rakuten_items)}件",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Amazon出品成功*\n{listed_count}件",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*推定合計利益*\n¥{total_profit:,}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*最小利益率*\n{config.MIN_PROFIT_RATE:.0%}",
                    },
                ],
            },
            {"type": "divider"},
        ]

        # Sort by profit descending
        sorted_items = sorted(rakuten_items, key=lambda x: x.profit, reverse=True)

        for r in sorted_items[:20]:  # max 20 items in Slack
            listing = listing_map.get(r.item_code)
            blocks.append(self._format_item_block(r, listing))
            blocks.append({"type": "divider"})

        if len(sorted_items) > 20:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_...他 {len(sorted_items) - 20} 件_",
                },
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"自動生成 by セドリBot | {today}",
                }
            ],
        })

        return blocks

    def send_report(
        self,
        rakuten_items: list[RakutenItem],
        listing_results: list[AmazonListingResult],
    ) -> bool:
        if not config.SLACK_BOT_TOKEN or not config.SLACK_CHANNEL_ID:
            logger.warning("Slack not configured. Skipping report.")
            return False

        blocks = self._build_message(rakuten_items, listing_results)
        total_profit = sum(r.profit for r in rakuten_items)

        try:
            self.client.chat_postMessage(
                channel=self.channel,
                text=f"セドリ日次レポート: {len(rakuten_items)}件、推定利益¥{total_profit:,}",
                blocks=blocks,
            )
            logger.info("Slack report sent successfully.")
            return True
        except SlackApiError as e:
            logger.error(f"Slack report failed: {e.response['error']}")
            return False

    def send_error(self, error_msg: str):
        """Send an error notification to Slack."""
        if not config.SLACK_BOT_TOKEN or not config.SLACK_CHANNEL_ID:
            return
        try:
            self.client.chat_postMessage(
                channel=self.channel,
                text=f":rotating_light: セドリBot エラー: {error_msg}",
            )
        except SlackApiError:
            pass
