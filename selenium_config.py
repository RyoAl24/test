"""
設定ファイル - Selenium ブラウザ自動操作版
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Mercari ログイン情報 ─────────────────────────────────
MERCARI_EMAIL    = os.getenv("MERCARI_EMAIL", "")
MERCARI_PASSWORD = os.getenv("MERCARI_PASSWORD", "")

# ── 楽天ウェブサービス API ────────────────────────────────
RAKUTEN_APP_ID       = os.getenv("RAKUTEN_APP_ID", "")
RAKUTEN_AFFILIATE_ID = os.getenv("RAKUTEN_AFFILIATE_ID", "")

# ── Slack ────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
# SLACK_CHANNEL はチャンネル名（sedori）・ID（C0XXXXX）どちらでも可
# 名前のみ（# なし）の場合は自動で # を付加する
_raw_channel    = os.getenv("SLACK_CHANNEL", os.getenv("SLACK_CHANNEL_ID", ""))
SLACK_CHANNEL   = (
    f"#{_raw_channel}" if _raw_channel and not _raw_channel.startswith(("#", "C"))
    else _raw_channel
)

# ── ビジネスロジック ──────────────────────────────────────
MIN_PROFIT_RATE = float(os.getenv("MIN_PROFIT_RATE", "0.20"))  # 0.20 = 20%
MIN_SALES_COUNT = int(os.getenv("MIN_SALES_COUNT",   "3"))
DAYS_LOOKBACK   = int(os.getenv("DAYS_LOOKBACK",     "7"))

# ── 利益計算（楽天仕入れ → Mercari 出品モデル） ───────────
#   profit = sell_price - mercari_fee - shipping - buy_price
MERCARI_FEE_RATE = float(os.getenv("MERCARI_FEE_RATE", "0.10"))  # 10%
SHIPPING_FEE     = int(os.getenv("SHIPPING_FEE",       "600"))   # 円

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
