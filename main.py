"""
セドリ自動化ツール - メインオーケストレーター
毎朝実行: Mercari売れ筋 → Rakuten仕入れ価格確認 → Amazon出品 → Slack通知
"""

import sys
import schedule
import time
from datetime import datetime

from loguru import logger

from mercari_scraper import MercariScraper
from rakuten_checker import RakutenChecker
from amazon_lister import AmazonLister
from slack_reporter import SlackReporter
import config


def setup_logger():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}",
        level="INFO",
    )
    logger.add(
        "logs/sedori_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="DEBUG",
    )


def run_pipeline():
    """Main pipeline: scrape → check → list → report."""
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("セドリ自動化パイプライン 開始")
    logger.info(f"実行時刻: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    reporter = SlackReporter()

    try:
        # Step 1: Mercari売れ筋スクレイピング
        logger.info("[Step 1/3] Mercari スクレイピング開始")
        scraper = MercariScraper()
        mercari_items = scraper.scrape_all_keywords()
        logger.info(f"  -> {len(mercari_items)}件の売れ筋商品を取得")

        if not mercari_items:
            logger.warning("Mercari商品なし。パイプライン終了。")
            reporter.send_error("Mercariスクレイピング結果が0件でした")
            return

        # Step 2: Rakuten価格確認 + 利益フィルタ
        logger.info(f"[Step 2/3] Rakuten 価格確認 ({len(mercari_items)}件)")
        checker = RakutenChecker()
        profitable_items = checker.filter_profitable(mercari_items)
        logger.info(f"  -> {len(profitable_items)}件が利益率{config.MIN_PROFIT_RATE:.0%}+")

        if not profitable_items:
            logger.warning("利益対象商品なし。Slack通知のみ送信。")
            reporter.send_report([], [])
            return

        # Print top items
        logger.info("--- Top 5 利益商品 ---")
        sorted_items = sorted(profitable_items, key=lambda x: x.profit, reverse=True)
        for item in sorted_items[:5]:
            logger.info(
                f"  {item.mercari_item.name[:30]} | "
                f"仕入¥{item.price:,} → 出品¥{item.suggested_amazon_price:,} | "
                f"利益¥{item.profit:,} ({item.profit_rate:.1%})"
            )

        # Step 3: Amazon出品
        logger.info(f"[Step 3/3] Amazon 出品 ({len(profitable_items)}件)")
        lister = AmazonLister()
        listing_results = lister.list_all(profitable_items)
        success = sum(1 for r in listing_results if r.success)
        logger.info(f"  -> {success}/{len(listing_results)}件 出品成功")

        # Step 4: Slack通知
        reporter.send_report(profitable_items, listing_results)

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(f"パイプライン完了 ({elapsed:.1f}秒)")

    except Exception as e:
        logger.exception(f"パイプラインエラー: {e}")
        reporter.send_error(f"パイプライン実行エラー: {e}")
        raise


def main():
    setup_logger()
    import os
    os.makedirs("logs", exist_ok=True)

    args = sys.argv[1:]

    if "--run-now" in args or "-r" in args:
        # 即時実行
        logger.info("即時実行モード")
        run_pipeline()
    else:
        # スケジューラモード（毎朝8時）
        run_time = "08:00"
        logger.info(f"スケジューラ起動: 毎日 {run_time} に実行")
        schedule.every().day.at(run_time).do(run_pipeline)

        # 起動時に1回実行するオプション
        if "--run-on-start" in args:
            run_pipeline()

        while True:
            schedule.run_pending()
            time.sleep(60)


if __name__ == "__main__":
    main()
