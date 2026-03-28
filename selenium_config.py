"""
設定ファイル - Selenium ブラウザ自動操作版
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Mercari ログイン情報 ─────────────────────────────────
MERCARI_EMAIL    = os.getenv("MERCARI_EMAIL", "")
MERCARI_PASSWORD = os.getenv("MERCARI_PASSWORD", "")

# ── Slack ────────────────────────────────────────────────
SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# ── ビジネスロジック ──────────────────────────────────────
MIN_PROFIT_RATE      = float(os.getenv("MIN_PROFIT_RATE",      "0.20"))
PRICE_MULTIPLIER_MIN = float(os.getenv("PRICE_MULTIPLIER_MIN", "1.5"))
PRICE_MULTIPLIER_MAX = float(os.getenv("PRICE_MULTIPLIER_MAX", "2.0"))
MIN_SALES_COUNT      = int(os.getenv("MIN_SALES_COUNT",        "3"))
DAYS_LOOKBACK        = int(os.getenv("DAYS_LOOKBACK",          "7"))

# ── Amazon 手数料（利益計算用） ───────────────────────────
AMAZON_FEE_RATE = 0.10   # 販売手数料 10%
AMAZON_FBA_FEE  = 500    # FBA 手数料（円）

# ── Selenium ─────────────────────────────────────────────
HEADLESS          = os.getenv("HEADLESS", "true").lower() == "true"
PAGE_LOAD_TIMEOUT = int(os.getenv("PAGE_LOAD_TIMEOUT", "30"))
IMPLICIT_WAIT     = int(os.getenv("IMPLICIT_WAIT",     "10"))

# セッションクッキー保管パス
MERCARI_COOKIE_FILE = "mercari_cookies.json"

# ── スケジュール ──────────────────────────────────────────
DAILY_RUN_TIME = os.getenv("DAILY_RUN_TIME", "06:00")

# ── 検索キーワード（30種類） ──────────────────────────────
SEARCH_KEYWORDS = [
    "DVD廃盤",        "Blu-ray初回限定",  "ゲームボーイ",     "ニンテンドー64",   "ゲームソフトレトロ",
    "推し活グッズ",    "アイドルグッズ",   "ビンテージスピーカー", "アンプ",          "プロジェクター",
    "オーディオ機器",  "ビンテージラジオ", "ヘッドフォン",     "イヤホン",         "Nikonカメラ",
    "Canonカメラ",    "Sonyカメラ",       "レンズ",           "電動工具",         "ドリル",
    "掘削機",         "懐中電灯",         "照明",             "ルーター",         "ネットワーク機器",
    "キーボード",     "マウス",           "バッテリー",        "充電器",           "モバイルバッテリー",
]
