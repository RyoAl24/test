"""
セドリ自動化ツール - Selenium 版 メインエントリーポイント

使い方:
  python run.py              # スケジューラ起動（毎朝 DAILY_RUN_TIME=06:00 に実行）
  python run.py --now        # 即時実行
  python run.py --now --dry  # ドライラン（Slack 未送信、ターミナル出力のみ）

cron での自動実行（毎朝 6時）:
  crontab -e  で以下を追記:
  0 6 * * * cd /path/to/project && /usr/bin/python3 /path/to/project/run.py --now >> /path/to/project/logs/cron.log 2>&1
"""

import os
import sys
import time
import traceback
from datetime import datetime

import schedule
from loguru import logger

import selenium_config as cfg
from mercari_selenium import MercariSeleniumScraper
from rakuten_selenium import ProfitCalculator
from slack_report import SlackReporter


# ── ログ設定 ─────────────────────────────────────────────────────────────────

def _setup_logger(verbose: bool = False):
    logger.remove()
    # --verbose/-v 指定時は標準出力にも DEBUG レベルで出す
    stdout_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | {message}"
        ),
        level=stdout_level,
    )
    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/sedori_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        encoding="utf-8",
        level="DEBUG",
    )


# ── パイプライン ──────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False):
    """
    Mercari スクレイピング → 楽天価格確認 → 利益計算 → Slack 配信
    """
    start = datetime.now()
    reporter = SlackReporter()

    logger.info("=" * 65)
    logger.info("  セドリ自動化 パイプライン 開始")
    logger.info(f"  実行時刻: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  ドライラン: {dry_run}")
    logger.info("=" * 65)

    scraper = MercariSeleniumScraper()

    try:
        # ── Step 1: Mercari スクレイピング ───────────────────────────────
        logger.info(f"[1/3]  Mercari スクレイピング開始（{len(cfg.SEARCH_KEYWORDS)} キーワード, 各最大 {cfg.MAX_ITEMS_PER_KEYWORD} 件）")
        sold_items = scraper.scrape_all()
        logger.info(f"       Mercari 合計: {len(sold_items)} 件の売れ筋商品")

        if not sold_items:
            logger.warning("       売れ筋商品がゼロです。終了します。")
            if not dry_run:
                reporter.send_error("Mercari スクレイピング結果が 0 件でした")
            return

        # ── Step 2: 楽天価格確認 + 利益フィルタ ─────────────────────────
        logger.info(f"[2/3]  楽天 API 価格確認（{len(sold_items)} 件）")
        calculator   = ProfitCalculator()
        profit_items = calculator.filter_all(sold_items)
        logger.info(f"       最終通過: {len(profit_items)} 件")

        # ── Step 3: Slack レポート送信 ───────────────────────────────────
        logger.info("[3/3]  レポート送信")
        if dry_run or not cfg.SLACK_BOT_TOKEN:
            reporter.print_report(profit_items)
        else:
            reporter.send(profit_items)

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(f"       パイプライン完了（{elapsed:.1f} 秒）")

    except KeyboardInterrupt:
        logger.info("中断されました")
    except Exception as e:
        logger.error(f"パイプラインエラー: {e}")
        logger.debug(traceback.format_exc())
        if not dry_run:
            reporter.send_error(f"パイプライン実行エラー:\n{e}")
        raise
    finally:
        scraper.close()


# ── エントリーポイント ────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    run_now = "--now" in args or "-n" in args
    dry_run = "--dry" in args or "-d" in args
    verbose = "--verbose" in args or "-v" in args

    _setup_logger(verbose=verbose)

    if run_now:
        logger.info("即時実行モード")
        run_pipeline(dry_run=dry_run)
        return
    else:
        run_time = cfg.DAILY_RUN_TIME
        logger.info(f"スケジューラ起動  毎日 {run_time} に実行（Ctrl+C で停止）")

        schedule.every().day.at(run_time).do(run_pipeline)

        # 起動直後に1回実行したい場合は --run-on-start を追加
        if "--run-on-start" in args:
            run_pipeline(dry_run=dry_run)

        while True:
            schedule.run_pending()
            time.sleep(30)


if __name__ == "__main__":
    main()
