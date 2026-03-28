import os
from dotenv import load_dotenv

load_dotenv()

# Rakuten API
RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID", "bdda00e4-dc6c-40d0-89f3-00b8c6db9624")
RAKUTEN_AFFILIATE_ID = os.getenv("RAKUTEN_AFFILIATE_ID", "51adacff.41a8a9f8.51adad00.ce40ae5d")

# Amazon SP-API
AMAZON_CLIENT_ID = os.getenv("AMAZON_CLIENT_ID", "")
AMAZON_CLIENT_SECRET = os.getenv("AMAZON_CLIENT_SECRET", "")
AMAZON_APP_ID = os.getenv("AMAZON_APP_ID", "")
AMAZON_REFRESH_TOKEN = os.getenv("AMAZON_REFRESH_TOKEN", "")
AMAZON_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "A1VC38T7YXB528")  # JP
AMAZON_SELLER_ID = os.getenv("AMAZON_SELLER_ID", "")

# Slack
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# Proxy settings
PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")

# Business logic
MIN_PROFIT_RATE = float(os.getenv("MIN_PROFIT_RATE", "0.20"))
PRICE_MULTIPLIER_MIN = float(os.getenv("PRICE_MULTIPLIER_MIN", "1.5"))
PRICE_MULTIPLIER_MAX = float(os.getenv("PRICE_MULTIPLIER_MAX", "2.0"))
MIN_SALES_COUNT = int(os.getenv("MIN_SALES_COUNT", "3"))
DAYS_LOOKBACK = int(os.getenv("DAYS_LOOKBACK", "7"))

# Amazon fees (Japan)
AMAZON_FEE_RATE = 0.10        # 販売手数料 10%
AMAZON_FBA_FEE = 500          # FBA手数料（円、商品による）
AMAZON_SHIPPING_FEE = 0       # FBAなので0

# Search keywords
SEARCH_KEYWORDS = [
    "DVD廃盤", "Blu-ray初回限定", "ゲームボーイ", "ニンテンドー64", "ゲームソフトレトロ",
    "推し活グッズ", "アイドルグッズ", "ビンテージスピーカー", "アンプ", "プロジェクター",
    "オーディオ機器", "ビンテージラジオ", "ヘッドフォン", "イヤホン", "Nikonカメラ",
    "Canonカメラ", "Sonyカメラ", "レンズ", "電動工具", "ドリル",
    "掘削機", "懐中電灯", "照明", "ルーター", "ネットワーク機器",
    "キーボード", "マウス", "バッテリー", "充電器", "モバイルバッテリー",
]
