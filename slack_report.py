"""
Slack 日次レポート送信モジュール
利益ランキング付きで毎朝 Block Kit 形式で送信する。
"""

from datetime import datetime
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from loguru import logger

import selenium_config as cfg
from rakuten_api import ProfitResult


class SlackReporter:
    def __init__(self):
        self.client  = WebClient(token=cfg.SLACK_BOT_TOKEN)
        self.channel = cfg.SLACK_CHANNEL   # "sedori" / "#sedori" / "C0XXXXX" すべて可

    # ── ブロック生成ヘルパー ──────────────────────────────────────────────────

    def _header_block(self, date_str: str) -> dict:
        return {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":bar_chart: セドリ日次レポート  {date_str}",
            },
        }

    def _summary_block(self, results: list[ProfitResult]) -> dict:
        total_profit   = sum(r.profit for r in results)
        avg_rate       = (
            sum(r.profit_rate for r in results) / len(results) if results else 0
        )
        top_profit     = results[0].profit if results else 0

        return {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*対象商品数*\n{len(results)} 件"},
                {"type": "mrkdwn", "text": f"*推定合計利益*\n¥{total_profit:,}"},
                {"type": "mrkdwn", "text": f"*平均利益率*\n{avg_rate:.1%}"},
                {"type": "mrkdwn", "text": f"*最高利益（1件）*\n¥{top_profit:,}"},
            ],
        }

    def _item_block(self, rank: int, r: ProfitResult) -> dict:
        medal = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}.get(rank, f"*{rank}.*")
        rate_bar = self._rate_bar(r.profit_rate)

        text = (
            f"{medal}  *{r.mercari_item.name[:40]}*\n"
            f"  :label: キーワード: `{r.mercari_item.keyword}`　"
            f"メルカリ売れ数: *{r.mercari_item.sold_count} 件 / {cfg.DAYS_LOOKBACK}日*\n"
            f"  :shopping_trolley: 楽天仕入: *¥{r.rakuten_price:,}*  "
            f"（{r.rakuten_shop[:25] or '不明'}）\n"
            f"  :amazon: Amazon 出品予定: *¥{r.amazon_sell_price:,}*\n"
            f"  :moneybag: 利益: *¥{r.profit:,}*  {rate_bar}  `{r.profit_rate:.1%}`"
        )

        block: dict = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }

        # 楽天リンクボタン（URL がある場合）
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
        """利益率を絵文字バーで可視化（0%〜50%）"""
        filled = min(int(rate / 0.05), 10)  # 5% = 1マス、最大 10マス
        return ":green_square:" * filled + ":white_square_button:" * (10 - filled)

    def _footer_block(self) -> dict:
        return {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"自動生成 by セドリBot  |  "
                        f"最低利益率フィルタ: {cfg.MIN_PROFIT_RATE:.0%}  |  "
                        f"直近 {cfg.DAYS_LOOKBACK} 日間  {cfg.MIN_SALES_COUNT} 件以上売れた商品"
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
            logger.warning("Slack 未設定のためレポートをスキップします（SLACK_BOT_TOKEN / SLACK_CHANNEL_ID）")
            return False

        total = sum(r.profit for r in results)
        fallback = (
            f"セドリ日次レポート: {len(results)} 件の利益商品、"
            f"推定合計利益 ¥{total:,}"
        )

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
        """エラー通知を Slack に送る。"""
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
        """Slack が未設定のときにターミナルへ出力する。"""
        print("\n" + "=" * 70)
        print(f"  セドリ日次レポート  {datetime.now().strftime('%Y-%m-%d')}")
        print("=" * 70)
        if not results:
            print("  利益対象商品なし")
        else:
            total = sum(r.profit for r in results)
            print(f"  対象: {len(results)} 件  |  推定合計利益: ¥{total:,}")
            print("-" * 70)
            for i, r in enumerate(results, 1):
                print(
                    f"  {i:2}. {r.mercari_item.name[:35]:<35} "
                    f"仕入¥{r.rakuten_price:>7,}  "
                    f"→ 出品¥{r.amazon_sell_price:>7,}  "
                    f"利益¥{r.profit:>6,}  ({r.profit_rate:.1%})"
                )
        print("=" * 70 + "\n")
