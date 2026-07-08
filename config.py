"""All knobs in one place. Nothing else defines numbers or thresholds."""
import os
import re
import urllib.parse

MIN_MARKET_CAP = 1_000_000_000          # USD floor (>= applied in source + select)
MAX_PER_TICK = int(os.environ.get("MAX_PER_TICK", "2"))   # posts per 30-min tick
MAX_PER_DAY = int(os.environ.get("MAX_PER_DAY", "20"))    # posts per trading day
MAX_PLAUSIBLE_HIGHS = 500               # validation gate: more = broken feed

MARKET_TZ = "America/New_York"
MARKET_OPEN = (9, 30)                   # ET
MARKET_CLOSE = (16, 0)                  # ET

# v2 (POST + JSON body): the only version exposing `session`, pinned to
# "regular" so a chart captured at the open never shows a pre-market line.
CHART_IMG_URL = "https://api.chart-img.com/v2/tradingview/advanced-chart"
STOCKTWITS_SYMBOL_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
STOCKTWITS_CREATE_URL = "https://api.stocktwits.com/api/2/messages/create.json"
STOCKTWITS_USER_AGENT = "stocktwits-relative-strength-poster/1.0"

# WSJ Market Data Center async feed for New 52-Week Highs (refreshes ~5 min).
WSJ_MDC_URL = ("https://www.wsj.com/market-data/stocks/newfiftytwoweekhighsandlows?id="
               + urllib.parse.quote('{"application":"WSJ","refreshInterval":300000}')
               + "&type=mdc_fiftytwoweek")

# Drop non-common-equity by name (same rule the WSJ prototype proved out).
NAME_EXCLUDE_RE = re.compile(
    r"\b(ETF|Fund|Pfd|Preferred|Notes?|Units?|Un|Warrants?|Wt|Bond|Rt|Rights)\b"
    r"|Acquisition Corp",
    re.I,
)
