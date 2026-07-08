# Stocktwits Relative-Strength Poster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post the least-watched >$1B US stocks at a new 52-week high to a dedicated Stocktwits account every 30 minutes, each with a 1-year chart, framed as undiscovered breakouts.

**Architecture:** Ports the proven `stocktwits-52wk-poster` engine (chart fetch, publishers, write-ahead state, at-most-once tick loop) verbatim, and swaps two pieces: a new `RSSource` (WSJ new-highs → Yahoo quotes → Stocktwits watchers, ported from the `stocktwits-relative-strength` `fetch_wsj.py`) and an ascending-by-watchers ranking in `select.pick()`. The post copy changes to the RS "undiscovered breakout" framing.

**Tech Stack:** Python 3.12, `requests` (chart-img), `urllib` (Stocktwits + WSJ + Yahoo, on purpose — Stocktwits' CDN 403s the `requests` TLS fingerprint), `pytest`.

## Global Constraints

- Python `>=3.12`.
- Runtime deps: `requests>=2.31` only (Yahoo/WSJ/Stocktwits use stdlib `urllib`; **no `yfinance`** — this project does not use the yfinance screen).
- `MIN_MARKET_CAP = 1_000_000_000` (USD floor, `>=`; matches the WSJ pipeline and `select.pick`).
- Caps: `MAX_PER_TICK = 2`, `MAX_PER_DAY = 20`; env-overridable. Never the same ticker on consecutive trading days.
- `MAX_PLAUSIBLE_HIGHS = 500` validation gate.
- Ranking: **ascending by watcher count, no floor**.
- Post copy EXACTLY: `f"${st_symbol(c.ticker)} undiscovered breakout with {c.watchers} watchers"`.
- Market hours: 9:30–16:00 ET, weekdays.
- Dry-run by default; `--live` requires `STOCKTWITS_ACCESS_TOKEN` (hard error if missing — never a silent downgrade). This repo's `STOCKTWITS_ACCESS_TOKEN` secret is the **new/dedicated Stocktwits account's** token, distinct from the 52wk-poster's.
- Reference sources (read-only, for verbatim ports and logic): `/Users/ethanberk/stocktwits-52wk-poster/` (engine) and `/Users/ethanberk/stocktwits-relative-strength/fetch_wsj.py` (RS pipeline).
- Repo root for all paths below: `/Users/ethanberk/stocktwits-relative-strength-poster/`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `src/__init__.py`, `src/source/__init__.py`, `src/publish/__init__.py` | scaffolding | 1 |
| `config.py` | all knobs + `WSJ_MDC_URL` | 1 |
| `src/source/base.py` | `Candidate` (+`watchers`), `HighsSource`, `SourceError` | 1 |
| `src/state.py` | posted-state store, cooldown, market hours | 2 |
| `src/stocktwits.py` | `st_symbol`, `symbol_exists` | 3 |
| `src/chart.py` | chart-img 1yr PNG | 4 |
| `src/publish/base.py` | `Publisher`, `PostResult`, `compose_post_text` (new copy) | 5 |
| `src/publish/record.py`, `src/publish/dryrun.py`, `src/publish/stocktwits_pub.py` | publishers | 5 |
| `src/select.py` | `validate`, `pick` (ascending watchers) | 6 |
| `src/source/rs_source.py` | WSJ+Yahoo+watchers → `Candidate`s | 7 |
| `run.py` | tick orchestration | 8 |
| `.github/workflows/tick.yml`, `README.md` | ops | 9 |
| `tests/**` | tests per task | each |

---

### Task 1: Foundation — scaffolding, config, `Candidate` with `watchers`

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`
- Create: `src/__init__.py`, `src/source/__init__.py`, `src/publish/__init__.py` (empty)
- Create: `config.py`, `src/source/base.py`
- Create: `tests/__init__.py`, `tests/test_config.py`, `tests/test_source_base.py`

**Interfaces:**
- Produces: `Candidate(ticker, name, exchange, price, pct_change_today, market_cap, week52_high, security_type, watchers)` frozen dataclass; `HighsSource` ABC with `fetch_candidates() -> list[Candidate]`; `SourceError`. `config.MIN_MARKET_CAP`, `MAX_PER_TICK`, `MAX_PER_DAY`, `MAX_PLAUSIBLE_HIGHS`, `MARKET_TZ`, `MARKET_OPEN`, `MARKET_CLOSE`, `CHART_IMG_URL`, `STOCKTWITS_SYMBOL_URL`, `STOCKTWITS_CREATE_URL`, `STOCKTWITS_USER_AGENT`, `NAME_EXCLUDE_RE`, `WSJ_MDC_URL`.

- [ ] **Step 1: Create scaffolding files**

`pyproject.toml`:
```toml
[project]
name = "stocktwits-relative-strength-poster"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
pythonpath = ["."]
addopts = "-m 'not contract'"
markers = ["contract: live external-API tests, run manually only"]
```

`requirements.txt`:
```
requests>=2.31
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
```

Create empty `src/__init__.py`, `src/source/__init__.py`, `src/publish/__init__.py`, `tests/__init__.py`:
```bash
mkdir -p src/source src/publish tests
touch src/__init__.py src/source/__init__.py src/publish/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write `config.py`**

```python
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
```

- [ ] **Step 3: Write the failing test for `Candidate` + config**

`tests/test_source_base.py`:
```python
import dataclasses
import pytest
from src.source.base import Candidate, HighsSource, SourceError


def _candidate(**over):
    base = dict(ticker="ABCD", name="Abcd Inc.", exchange="NASDAQ", price=10.0,
                pct_change_today=1.5, market_cap=2e9, week52_high=10.5,
                security_type="EQUITY", watchers=42)
    base.update(over)
    return Candidate(**base)


def test_candidate_carries_watchers():
    c = _candidate(watchers=7)
    assert c.watchers == 7


def test_candidate_is_frozen():
    c = _candidate()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.watchers = 9


def test_source_is_abstract():
    with pytest.raises(TypeError):
        HighsSource()
    assert issubclass(SourceError, Exception)
```

`tests/test_config.py`:
```python
import config


def test_min_market_cap_is_one_billion():
    assert config.MIN_MARKET_CAP == 1_000_000_000


def test_default_caps():
    assert config.MAX_PER_TICK == 2
    assert config.MAX_PER_DAY == 20


def test_wsj_url_present():
    assert "newfiftytwoweekhighsandlows" in config.WSJ_MDC_URL


def test_name_exclude_matches_etf_and_acquisition():
    assert config.NAME_EXCLUDE_RE.search("Some ETF")
    assert config.NAME_EXCLUDE_RE.search("Foo Acquisition Corp")
    assert not config.NAME_EXCLUDE_RE.search("Acadian Asset Management Inc.")
```

- [ ] **Step 4: Run tests — verify they fail**

Run: `python -m pytest tests/test_source_base.py tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: src.source.base` / `config` values may pass but base import fails).

- [ ] **Step 5: Write `src/source/base.py`**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str
    exchange: str            # TradingView-style prefix: "NASDAQ" | "NYSE" | "AMEX" | ""
    price: float
    pct_change_today: float
    market_cap: float
    week52_high: float
    security_type: str       # Yahoo quoteType, e.g. "EQUITY"
    watchers: int            # Stocktwits watchlist_count; the ranking axis


class SourceError(Exception):
    """The source itself looks broken (not merely 'no highs right now')."""


class HighsSource(ABC):
    @abstractmethod
    def fetch_candidates(self) -> list[Candidate]:
        """All US equities on today's 52-week-high list, each with watchers set."""
```

- [ ] **Step 6: Run tests — verify they pass**

Run: `python -m pytest tests/test_source_base.py tests/test_config.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements.txt requirements-dev.txt src config.py tests
git commit -m "feat: foundation — config, Candidate with watchers, HighsSource"
```

---

### Task 2: State store (ported verbatim)

**Files:**
- Create: `src/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Produces: `load_posted(path) -> list[dict]`, `append_posted(path, ticker, day, post_id, status="posted")`, `mark_posted(path, ticker, day, post_id)`, `previous_trading_day(d) -> date`, `is_blocked(ticker, posted, today) -> bool`, `daily_count(posted, today) -> int`, `is_market_hours(now_utc) -> bool`.

- [ ] **Step 1: Copy the proven state module verbatim**

```bash
cp /Users/ethanberk/stocktwits-52wk-poster/src/state.py src/state.py
```

Verify it begins with `import json` and defines `is_market_hours` (no edits needed — it depends only on `config`, which Task 1 provides with identical names).

- [ ] **Step 2: Write tests**

`tests/test_state.py`:
```python
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src import state


def test_append_and_load_roundtrip(tmp_path):
    p = tmp_path / "posted.json"
    state.append_posted(p, "AAA", date(2026, 7, 8), "123", status="posted")
    posts = state.load_posted(p)
    assert posts == [{"ticker": "AAA", "date": "2026-07-08",
                      "post_id": "123", "status": "posted"}]


def test_mark_posted_promotes_pending(tmp_path):
    p = tmp_path / "posted.json"
    state.append_posted(p, "BBB", date(2026, 7, 8), None, status="pending")
    state.mark_posted(p, "BBB", date(2026, 7, 8), "999")
    e = state.load_posted(p)[0]
    assert e["status"] == "posted" and e["post_id"] == "999"


def test_is_blocked_today_and_prev_trading_day():
    posted = [{"ticker": "CCC", "date": "2026-07-07", "post_id": "1", "status": "posted"}]
    # 2026-07-08 is a Wednesday; prev trading day is Tuesday 07-07
    assert state.is_blocked("CCC", posted, date(2026, 7, 8))
    assert not state.is_blocked("DDD", posted, date(2026, 7, 8))


def test_daily_count():
    posted = [{"ticker": "A", "date": "2026-07-08"},
              {"ticker": "B", "date": "2026-07-08"},
              {"ticker": "C", "date": "2026-07-07"}]
    assert state.daily_count(posted, date(2026, 7, 8)) == 2


def test_market_hours_gate():
    # 2026-07-08 14:00 UTC = 10:00 ET Wednesday -> open
    open_utc = datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc)
    assert state.is_market_hours(open_utc)
    # 2026-07-08 02:00 UTC -> closed
    closed_utc = datetime(2026, 7, 8, 2, 0, tzinfo=timezone.utc)
    assert not state.is_market_hours(closed_utc)
    # Saturday 2026-07-11 14:00 UTC -> closed
    sat = datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc)
    assert not state.is_market_hours(sat)
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_state.py -v`
Expected: PASS (5 tests).

- [ ] **Step 4: Commit**

```bash
git add src/state.py tests/test_state.py
git commit -m "feat: port state store (cooldown, market hours, write-ahead)"
```

---

### Task 3: Stocktwits symbology (ported verbatim)

**Files:**
- Create: `src/stocktwits.py`
- Create: `tests/test_stocktwits.py`

**Interfaces:**
- Consumes: `Candidate` (Task 1), `config.STOCKTWITS_SYMBOL_URL`.
- Produces: `st_symbol(ticker: str) -> str`, `symbol_exists(candidate, timeout=15) -> bool`.

- [ ] **Step 1: Copy verbatim**

```bash
cp /Users/ethanberk/stocktwits-52wk-poster/src/stocktwits.py src/stocktwits.py
```

No edits: it imports `config` and `Candidate` with the same names this repo defines.

- [ ] **Step 2: Write tests**

`tests/test_stocktwits.py`:
```python
import urllib.error
from src import stocktwits
from src.source.base import Candidate


def _c(ticker):
    return Candidate(ticker=ticker, name="x", exchange="NYSE", price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=1)


def test_st_symbol_maps_dash_to_dot():
    assert stocktwits.st_symbol("BRK-B") == "BRK.B"
    assert stocktwits.st_symbol("AAPL") == "AAPL"


def test_symbol_exists_true_on_200(monkeypatch):
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(stocktwits.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert stocktwits.symbol_exists(_c("AAPL")) is True


def test_symbol_exists_false_on_404(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    monkeypatch.setattr(stocktwits.urllib.request, "urlopen", boom)
    assert stocktwits.symbol_exists(_c("NOPE")) is False


def test_symbol_exists_allows_on_403(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "blocked", {}, None)
    monkeypatch.setattr(stocktwits.urllib.request, "urlopen", boom)
    assert stocktwits.symbol_exists(_c("AAPL")) is True
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_stocktwits.py -v`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add src/stocktwits.py tests/test_stocktwits.py
git commit -m "feat: port Stocktwits symbology + pre-post symbol check"
```

---

### Task 4: Chart fetch (ported verbatim)

**Files:**
- Create: `src/chart.py`
- Create: `tests/test_chart.py`

**Interfaces:**
- Consumes: `Candidate` (Task 1), `config.CHART_IMG_URL`.
- Produces: `fetch_chart_png(candidate, api_key) -> bytes`, `ChartError`.

Note: `chart.py` uses `candidate.exchange` directly as the TradingView prefix. The exchange→prefix mapping lives in `RSSource` (Task 7), so `chart.py` is ported unchanged.

- [ ] **Step 1: Copy verbatim**

```bash
cp /Users/ethanberk/stocktwits-52wk-poster/src/chart.py src/chart.py
```

- [ ] **Step 2: Write tests**

`tests/test_chart.py`:
```python
import pytest
import requests
from src import chart
from src.source.base import Candidate


def _c(ticker="ABCD", exchange="NASDAQ"):
    return Candidate(ticker=ticker, name="x", exchange=exchange, price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=1)


def test_request_args_builds_exchange_prefixed_symbol():
    url, body, headers = chart._request_args(_c("ABCD", "NASDAQ"), "KEY")
    assert body["symbol"] == "NASDAQ:ABCD"
    assert body["session"] == "regular"
    assert body["range"] == "1Y"
    assert headers["x-api-key"] == "KEY"


def test_fetch_chart_png_returns_bytes(monkeypatch):
    class Resp:
        status_code = 200
        content = b"PNGDATA"
    monkeypatch.setattr(chart.requests, "post", lambda *a, **k: Resp())
    assert chart.fetch_chart_png(_c(), "KEY") == b"PNGDATA"


def test_fetch_chart_png_raises_on_non_200(monkeypatch):
    class Resp:
        status_code = 500
        content = b""
    monkeypatch.setattr(chart.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(chart.ChartError):
        chart.fetch_chart_png(_c(), "KEY")
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_chart.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add src/chart.py tests/test_chart.py
git commit -m "feat: port chart-img 1yr PNG fetch"
```

---

### Task 5: Publishers + new post copy

**Files:**
- Create: `src/publish/base.py` (new `compose_post_text`), `src/publish/record.py`, `src/publish/dryrun.py`, `src/publish/stocktwits_pub.py`
- Create: `tests/test_publish.py`

**Interfaces:**
- Consumes: `Candidate` (Task 1), `st_symbol` (Task 3), `config`.
- Produces: `Publisher` ABC with `post(candidate, text, image_png) -> PostResult`; `PostResult(post_id, dry_run)`; `compose_post_text(c) -> str`; `write_post_artifacts(out_dir, today, ticker, text, image_png)`; `DryRunPublisher(out_dir, today)`; `StocktwitsPublisher(access_token, out_dir, today, ...)`; `PublishError`.

- [ ] **Step 1: Copy the three unchanged publisher files verbatim**

```bash
cp /Users/ethanberk/stocktwits-52wk-poster/src/publish/record.py src/publish/record.py
cp /Users/ethanberk/stocktwits-52wk-poster/src/publish/dryrun.py src/publish/dryrun.py
cp /Users/ethanberk/stocktwits-52wk-poster/src/publish/stocktwits_pub.py src/publish/stocktwits_pub.py
```

- [ ] **Step 2: Write `src/publish/base.py` with the NEW copy**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.source.base import Candidate
from src.stocktwits import st_symbol


@dataclass(frozen=True)
class PostResult:
    post_id: str | None
    dry_run: bool


class Publisher(ABC):
    @abstractmethod
    def post(self, candidate: Candidate, text: str, image_png: bytes) -> PostResult: ...


def compose_post_text(c: Candidate) -> str:
    # No price/%chg/mcap in the copy: those go stale between the tick and the
    # reader; the attached chart carries the quantitative story. Watcher count
    # is stable enough to include and is the whole point of this feed.
    # Cashtag uses Stocktwits symbology (BRK.B, not Yahoo's BRK-B).
    return f"${st_symbol(c.ticker)} undiscovered breakout with {c.watchers} watchers"
```

- [ ] **Step 3: Write the failing tests**

`tests/test_publish.py`:
```python
import json
from datetime import date

import pytest

from src.publish.base import compose_post_text, PostResult
from src.publish.dryrun import DryRunPublisher
from src.publish import stocktwits_pub
from src.publish.stocktwits_pub import StocktwitsPublisher, PublishError
from src.source.base import Candidate


def _c(ticker="ABCD", watchers=9):
    return Candidate(ticker=ticker, name="x", exchange="NASDAQ", price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=watchers)


def test_compose_post_text_exact():
    assert compose_post_text(_c("AAMI", 15)) == \
        "$AAMI undiscovered breakout with 15 watchers"


def test_compose_post_text_uses_stocktwits_symbology():
    assert compose_post_text(_c("BRK-B", 100)) == \
        "$BRK.B undiscovered breakout with 100 watchers"


def test_dryrun_writes_artifacts_and_returns_dry_result(tmp_path):
    pub = DryRunPublisher(tmp_path, date(2026, 7, 8))
    res = pub.post(_c("ABCD"), "hello", b"PNG")
    assert res == PostResult(post_id=None, dry_run=True)
    day = tmp_path / "2026-07-08"
    assert (day / "ABCD.png").read_bytes() == b"PNG"
    assert (day / "ABCD.txt").read_text() == "hello"


def test_stocktwits_publisher_success(tmp_path):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"response": {"status": 200},
                               "message": {"id": 555}}).encode()
    pub = StocktwitsPublisher("tok", tmp_path, date(2026, 7, 8),
                              urlopen=lambda *a, **k: Resp())
    res = pub.post(_c("ABCD"), "hello", b"PNG")
    assert res.post_id == "555" and res.dry_run is False
    assert (tmp_path / "2026-07-08" / "ABCD.png").read_bytes() == b"PNG"


def test_stocktwits_publisher_raises_on_error_status(tmp_path):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"response": {"status": 429}}).encode()
    pub = StocktwitsPublisher("tok", tmp_path, date(2026, 7, 8),
                              urlopen=lambda *a, **k: Resp())
    with pytest.raises(PublishError):
        pub.post(_c("ABCD"), "hello", b"PNG")
```

- [ ] **Step 4: Run tests — verify they fail then pass**

Run: `python -m pytest tests/test_publish.py -v`
Expected: after Steps 1–2, PASS (5 tests). (Run before Step 2's file exists to see the import failure if desired.)

- [ ] **Step 5: Commit**

```bash
git add src/publish tests/test_publish.py
git commit -m "feat: publishers + RS 'undiscovered breakout' post copy"
```

---

### Task 6: Selection — validate + ascending-watchers pick

**Files:**
- Create: `src/select.py`
- Create: `tests/test_select.py`

**Interfaces:**
- Consumes: `Candidate` (Task 1), `state.is_blocked`/`daily_count` (Task 2), `config`.
- Produces: `validate(candidates) -> None` (raises `ValidationError`), `pick(candidates, posted, today) -> list[Candidate]`, `ValidationError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_select.py`:
```python
from datetime import date

import pytest

from src import select
from src.source.base import Candidate


def _c(ticker, watchers, mcap=2e9):
    return Candidate(ticker=ticker, name=ticker, exchange="NASDAQ", price=1.0,
                     pct_change_today=0.0, market_cap=mcap, week52_high=1.0,
                     security_type="EQUITY", watchers=watchers)


def test_validate_raises_over_gate():
    many = [_c(f"T{i}", i) for i in range(config_max() + 1)]
    with pytest.raises(select.ValidationError):
        select.validate(many)


def config_max():
    import config
    return config.MAX_PLAUSIBLE_HIGHS


def test_pick_orders_by_fewest_watchers(monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_PER_TICK", 2)
    monkeypatch.setattr(config, "MAX_PER_DAY", 20)
    cands = [_c("HIGH", 5000), _c("LOW", 3), _c("MID", 400)]
    picks = select.pick(cands, posted=[], today=date(2026, 7, 8))
    assert [c.ticker for c in picks] == ["LOW", "MID"]


def test_pick_respects_per_tick_cap(monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_PER_TICK", 1)
    monkeypatch.setattr(config, "MAX_PER_DAY", 20)
    cands = [_c("A", 1), _c("B", 2)]
    picks = select.pick(cands, posted=[], today=date(2026, 7, 8))
    assert [c.ticker for c in picks] == ["A"]


def test_pick_excludes_blocked_and_below_cap(monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_PER_TICK", 5)
    monkeypatch.setattr(config, "MAX_PER_DAY", 20)
    posted = [{"ticker": "A", "date": "2026-07-08"}]  # A already posted today
    cands = [_c("A", 1), _c("B", 2), _c("SMALL", 0, mcap=5e8)]  # SMALL below $1B
    picks = select.pick(cands, posted, today=date(2026, 7, 8))
    assert [c.ticker for c in picks] == ["B"]


def test_pick_respects_daily_remaining(monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_PER_TICK", 5)
    monkeypatch.setattr(config, "MAX_PER_DAY", 3)
    posted = [{"ticker": "X", "date": "2026-07-08"},
              {"ticker": "Y", "date": "2026-07-08"}]  # 2 already today, 1 left
    cands = [_c("A", 1), _c("B", 2), _c("C", 3)]
    picks = select.pick(cands, posted, today=date(2026, 7, 8))
    assert [c.ticker for c in picks] == ["A"]
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `python -m pytest tests/test_select.py -v`
Expected: FAIL (`ModuleNotFoundError: src.select`).

- [ ] **Step 3: Write `src/select.py`**

```python
from datetime import date

import config
from src import state
from src.source.base import Candidate


class ValidationError(Exception):
    """Feed output looks broken; abort the tick before posting anything."""


def validate(candidates: list[Candidate]) -> None:
    if len(candidates) > config.MAX_PLAUSIBLE_HIGHS:
        raise ValidationError(
            f"{len(candidates)} '52-week highs' is implausible "
            f"(gate: {config.MAX_PLAUSIBLE_HIGHS}); refusing to post")


def pick(candidates: list[Candidate], posted: list[dict], today: date) -> list[Candidate]:
    eligible = [c for c in candidates
                if c.market_cap >= config.MIN_MARKET_CAP
                and not state.is_blocked(c.ticker, posted, today)]
    eligible.sort(key=lambda c: c.watchers)          # fewest watchers first — no floor
    remaining_today = config.MAX_PER_DAY - state.daily_count(posted, today)
    n = max(0, min(config.MAX_PER_TICK, remaining_today))
    return eligible[:n]
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `python -m pytest tests/test_select.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/select.py tests/test_select.py
git commit -m "feat: select — validation gate + ascending-watchers pick"
```

---

### Task 7: `RSSource` — WSJ + Yahoo + watchers → Candidates

**Files:**
- Create: `src/source/rs_source.py`
- Create: `tests/test_rs_source.py`

**Interfaces:**
- Consumes: `Candidate`, `HighsSource`, `SourceError` (Task 1), `config`.
- Produces: `RSSource()` implementing `fetch_candidates() -> list[Candidate]`; helper `_build_candidate(ticker, name, quote, watch) -> Candidate | None` (pure, unit-testable, no network).

**Design notes (from `fetch_wsj.py`):** WSJ feed gives `[(ticker, name), ...]` of today's new highs (dotted tickers and non-common-equity names already dropped). Yahoo v7 bulk quote (cookie+crumb) gives market cap / price / %chg / 52wk high / quoteType. Stocktwits streams gives watcher count + exchange string. Keep a row only if `market_cap > MIN_MARKET_CAP`, watcher count present, and the Stocktwits exchange maps to a TradingView prefix chart-img can resolve.

- [ ] **Step 1: Write the failing test for the pure builder**

`tests/test_rs_source.py`:
```python
from src.source.rs_source import RSSource, _build_candidate, _EXCHANGE_PREFIX


def test_exchange_prefix_covers_stocktwits_strings():
    assert _EXCHANGE_PREFIX["NYSE"] == "NYSE"
    assert _EXCHANGE_PREFIX["NASDAQ"] == "NASDAQ"
    assert _EXCHANGE_PREFIX["NYSEAmerican"] == "AMEX"
    assert _EXCHANGE_PREFIX["AMEX"] == "AMEX"


def test_build_candidate_happy_path():
    quote = {"marketCap": 2_000_000_000, "regularMarketPrice": 84.99,
             "regularMarketChangePercent": 3.2, "fiftyTwoWeekHigh": 85.08,
             "quoteType": "EQUITY"}
    watch = {"watchlist_count": 15, "exchange": "NYSE"}
    c = _build_candidate("AAMI", "Acadian Asset Management Inc.", quote, watch)
    assert c is not None
    assert c.ticker == "AAMI" and c.watchers == 15
    assert c.exchange == "NYSE" and c.market_cap == 2_000_000_000


def test_build_candidate_drops_below_one_billion():
    quote = {"marketCap": 500_000_000, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("SMALL", "Small Co", quote, {"watchlist_count": 1,
                            "exchange": "NYSE"}) is None


def test_build_candidate_drops_missing_watchers():
    quote = {"marketCap": 2e9, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("NOWATCH", "No Watch", quote,
                            {"watchlist_count": None, "exchange": "NYSE"}) is None


def test_build_candidate_drops_unmappable_exchange():
    quote = {"marketCap": 2e9, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("OTCX", "Otc Co", quote,
                            {"watchlist_count": 5, "exchange": "OTC"}) is None


def test_build_candidate_drops_missing_marketcap():
    quote = {"regularMarketPrice": 10.0, "fiftyTwoWeekHigh": 10.0,
             "quoteType": "EQUITY"}
    assert _build_candidate("NOMC", "No MC", quote,
                            {"watchlist_count": 5, "exchange": "NYSE"}) is None


def test_fetch_candidates_wires_stages(monkeypatch):
    src = RSSource()
    monkeypatch.setattr(src, "_wsj_universe",
                        lambda: [("AAMI", "Acadian Asset Management Inc."),
                                 ("SMALL", "Small Co")])
    monkeypatch.setattr(src, "_yahoo_quotes", lambda tks: {
        "AAMI": {"marketCap": 3e9, "regularMarketPrice": 85.0,
                 "regularMarketChangePercent": 3.2, "fiftyTwoWeekHigh": 85.1,
                 "quoteType": "EQUITY"},
        "SMALL": {"marketCap": 5e8, "regularMarketPrice": 10.0,
                  "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}})
    monkeypatch.setattr(src, "_watchers", lambda tks: {
        "AAMI": {"watchlist_count": 15, "exchange": "NYSE"},
        "SMALL": {"watchlist_count": 2, "exchange": "NYSE"}})
    cands = src.fetch_candidates()
    assert [c.ticker for c in cands] == ["AAMI"]  # SMALL dropped (<$1B)
    assert cands[0].watchers == 15


def test_fetch_candidates_raises_on_empty_universe(monkeypatch):
    import pytest
    src = RSSource()
    monkeypatch.setattr(src, "_wsj_universe", lambda: [])
    with pytest.raises(Exception):  # SourceError
        src.fetch_candidates()
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `python -m pytest tests/test_rs_source.py -v`
Expected: FAIL (`ModuleNotFoundError: src.source.rs_source`).

- [ ] **Step 3: Write `src/source/rs_source.py`**

```python
"""Relative-Strength source: WSJ new-52wk-highs universe, ranked-later by
watchers. Ported from stocktwits-relative-strength/fetch_wsj.py.

Universe:  WSJ Market Data Center "New 52 Week Highs" feed.
Enrich:    Yahoo v7 bulk quote (cookie+crumb) — mcap/price/%chg/52wk high/type.
Watchers:  Stocktwits streams endpoint (the ranking axis) + exchange.
Keep:      market_cap > $1B, watcher count present, chart-resolvable exchange.

urllib on purpose (see src/stocktwits.py): Stocktwits' CDN 403s the requests
TLS fingerprint but passes urllib; the WSJ prototype relied on the same.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from src.source.base import Candidate, HighsSource, SourceError

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Stocktwits exchange string -> TradingView prefix chart-img resolves.
# Anything not here can't be charted reliably, so the row is dropped.
_EXCHANGE_PREFIX = {
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "NYSEAmerican": "AMEX",
    "NYSEArca": "AMEX",
    "AMEX": "AMEX",
    "BATS": "BATS",
}


def _get_json(url, opener=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            fh = (opener.open(req, timeout=12) if opener
                  else urllib.request.urlopen(req, timeout=12))
            with fh as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(1.5 * (i + 1)); continue
            return None
        except Exception:
            time.sleep(0.5 * (i + 1))
    return None


def _build_candidate(ticker: str, name: str, quote: dict,
                     watch: dict) -> Candidate | None:
    mcap = (quote or {}).get("marketCap")
    wc = (watch or {}).get("watchlist_count")
    prefix = _EXCHANGE_PREFIX.get((watch or {}).get("exchange"))
    price = (quote or {}).get("regularMarketPrice")
    if not mcap or mcap < config.MIN_MARKET_CAP:    # >= $1B, matches select.pick
        return None
    if wc is None:                                  # need a ranking axis
        return None
    if prefix is None:                              # can't chart it
        return None
    if not price:
        return None
    return Candidate(
        ticker=ticker,
        name=name,
        exchange=prefix,
        price=float(price),
        pct_change_today=float(quote.get("regularMarketChangePercent") or 0.0),
        market_cap=float(mcap),
        week52_high=float(quote.get("fiftyTwoWeekHigh") or 0.0),
        security_type=quote.get("quoteType") or "",
        watchers=int(wc),
    )


class RSSource(HighsSource):
    def _wsj_universe(self) -> list[tuple[str, str]]:
        d = _get_json(config.WSJ_MDC_URL)
        data = (d or {}).get("data") or {}
        seen, pairs = set(), []
        for _section, payload in data.items():
            if not isinstance(payload, dict):
                continue
            for r in payload.get("highs") or []:
                tk = (r.get("ticker") or "").strip()
                nm = (r.get("name") or "").strip()
                if not tk or tk in seen:
                    continue
                if "." in tk or config.NAME_EXCLUDE_RE.search(nm):
                    continue
                seen.add(tk)
                pairs.append((tk, nm))
        return pairs

    def _yahoo_quotes(self, tickers: list[str]) -> dict:
        # cookie then crumb
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        try:
            opener.open(urllib.request.Request(
                "https://fc.yahoo.com", headers={"User-Agent": _UA}), timeout=12)
        except Exception:
            pass
        try:
            crumb = opener.open(urllib.request.Request(
                "https://query1.finance.yahoo.com/v1/test/getcrumb",
                headers={"User-Agent": _UA}), timeout=12).read().decode()
        except Exception:
            return {}
        out = {}
        for i in range(0, len(tickers), 40):
            chunk = tickers[i:i + 40]
            url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                   + ",".join(chunk) + "&crumb=" + urllib.parse.quote(crumb))
            d = _get_json(url, opener=opener)
            for q in ((d or {}).get("quoteResponse", {}) or {}).get("result", []) or []:
                out[q.get("symbol")] = q
        return out

    def _watchers(self, tickers: list[str]) -> dict:
        def one(tk):
            d = _get_json(config.STOCKTWITS_SYMBOL_URL.format(symbol=tk))
            sym = (d or {}).get("symbol") or {}
            return tk, ({"watchlist_count": sym.get("watchlist_count"),
                         "exchange": sym.get("exchange")} if sym else {})
        out = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for fut in as_completed([ex.submit(one, t) for t in tickers]):
                tk, info = fut.result()
                out[tk] = info
        return out

    def fetch_candidates(self) -> list[Candidate]:
        pairs = self._wsj_universe()
        if not pairs:
            raise SourceError("WSJ feed returned zero new highs; feed looks broken")
        names = {t: n for t, n in pairs}
        tickers = [t for t, _ in pairs]
        quotes = self._yahoo_quotes(tickers)
        watch = self._watchers(tickers)
        out = []
        for tk in tickers:
            c = _build_candidate(tk, names[tk], quotes.get(tk) or {}, watch.get(tk) or {})
            if c is not None:
                out.append(c)
        return out
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `python -m pytest tests/test_rs_source.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/source/rs_source.py tests/test_rs_source.py
git commit -m "feat: RSSource — WSJ highs + Yahoo quotes + Stocktwits watchers"
```

---

### Task 8: Tick orchestration (`run.py`)

**Files:**
- Create: `run.py`
- Create: `tests/test_run.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `tick(source, publisher, chart_fetch, state_path, now_utc, force=False, symbol_check=..., state_sync=None) -> list[str]`; `build_publisher(live, out_dir, today) -> Publisher`; `main() -> int`.

- [ ] **Step 1: Copy `run.py` from the poster, then update identity strings**

```bash
cp /Users/ethanberk/stocktwits-52wk-poster/run.py run.py
```

Then apply these edits (the logic is identical — only the source class and git bot identity differ):

Replace the import line
```python
from src.source.yfinance_source import YFinanceSource
```
with
```python
from src.source.rs_source import RSSource
```

In `main()`, replace
```python
        tick(YFinanceSource(), publisher,
```
with
```python
        tick(RSSource(), publisher,
```

In `_git_sync_state()`, replace both occurrences of
```python
    git = ["git", "-c", "user.name=52wk-poster-bot",
```
with
```python
    git = ["git", "-c", "user.name=rs-poster-bot",
```

- [ ] **Step 2: Write the tick test**

`tests/test_run.py`:
```python
from datetime import date, datetime, timezone
from pathlib import Path

import run
from src import state
from src.publish.base import PostResult
from src.source.base import Candidate


def _c(ticker, watchers=1):
    return Candidate(ticker=ticker, name=ticker, exchange="NASDAQ", price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=watchers)


class FakeSource:
    def __init__(self, cands): self._c = cands
    def fetch_candidates(self): return self._c


class FakePublisher:
    def __init__(self): self.posted = []
    def post(self, candidate, text, image_png):
        self.posted.append((candidate.ticker, text))
        return PostResult(post_id="id-" + candidate.ticker, dry_run=False)


def test_tick_posts_fewest_watched_and_records_state(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_PER_TICK", 1)
    monkeypatch.setattr(config, "MAX_PER_DAY", 20)
    sp = tmp_path / "posted.json"
    pub = FakePublisher()
    now = datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc)  # 10:00 ET Wed
    done = run.tick(FakeSource([_c("HIGH", 900), _c("LOW", 3)]), pub,
                    chart_fetch=lambda c: b"PNG", state_path=sp, now_utc=now)
    assert done == ["LOW"]
    assert pub.posted == [("LOW", "$LOW undiscovered breakout with 3 watchers")]
    e = [p for p in state.load_posted(sp) if p["ticker"] == "LOW"][0]
    assert e["status"] == "posted" and e["post_id"] == "id-LOW"


def test_tick_noop_outside_market_hours(tmp_path):
    sp = tmp_path / "posted.json"
    pub = FakePublisher()
    now = datetime(2026, 7, 8, 2, 0, tzinfo=timezone.utc)  # 22:00 ET prev day
    done = run.tick(FakeSource([_c("LOW", 3)]), pub,
                    chart_fetch=lambda c: b"PNG", state_path=sp, now_utc=now)
    assert done == [] and pub.posted == []


def test_build_publisher_live_without_token_exits(monkeypatch, tmp_path):
    import pytest
    monkeypatch.delenv("STOCKTWITS_ACCESS_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        run.build_publisher(live=True, out_dir=tmp_path, today=date(2026, 7, 8))
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_run.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS (all tasks' tests green).

- [ ] **Step 5: Commit**

```bash
git add run.py tests/test_run.py
git commit -m "feat: tick orchestration (RSSource wired, RS bot identity)"
```

---

### Task 9: Ops — workflow + README

**Files:**
- Create: `.github/workflows/tick.yml`, `README.md`

**Interfaces:** none (ops only).

- [ ] **Step 1: Write `.github/workflows/tick.yml`**

```yaml
# .github/workflows/tick.yml
name: tick
on:
  schedule:
    # Every 30 min, 13:00-21:59 UTC weekdays. Covers 9:30am-4pm ET in both
    # EDT (13:30-20:00 UTC) and EST (14:30-21:00 UTC); run.py gates precisely.
    - cron: "*/30 13-21 * * 1-5"
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: tick
  cancel-in-progress: false

jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Run tick
        env:
          CHART_IMG_API_KEY: ${{ secrets.CHART_IMG_API_KEY }}
          # The DEDICATED relative-strength Stocktwits account's token —
          # distinct from the 52wk-poster's account.
          STOCKTWITS_ACCESS_TOKEN: ${{ secrets.STOCKTWITS_ACCESS_TOKEN }}
          # Launch ramp: 1 post/tick (a gentle 30-min trickle that dodges the
          # duplicate filter), 12/day. Remove these two to restore 2/20 defaults.
          MAX_PER_TICK: "1"
          MAX_PER_DAY: "12"
          PYTHONUNBUFFERED: "1"
        run: python run.py --sync-state --live
      - name: Commit state + output
        if: always()
        run: |
          git config user.name "rs-poster-bot"
          git config user.email "actions@users.noreply.github.com"
          git add state output
          if git diff --cached --quiet; then
            echo "nothing posted this tick"; exit 0
          fi
          git commit -m "state: tick $(date -u +'%Y-%m-%dT%H:%M')"
          git pull --rebase origin main
          git push
```

Note: Phase-1 dry-run rollout — before going live, change `run: python run.py --sync-state --live` to `run: python run.py --sync-state` (drops `--live`) and the token is not required. Flip back to `--live` once dry-run output looks right.

- [ ] **Step 2: Write `README.md`**

```markdown
# stocktwits-relative-strength-poster

Posts the **least-watched** US common stocks >$1B that printed a new 52-week
high today to a **dedicated Stocktwits account**, each with a 1-year chart,
framed as *undiscovered breakouts*. Every 30 minutes the fewest-watched
eligible names (max 2/tick, 20/day, never on consecutive trading days) get a
`$TICKER undiscovered breakout with {N} watchers` post.

Combines two prior projects: the **relative-strength** data pipeline (WSJ new
highs → Yahoo enrichment → Stocktwits watchers → rank ascending by watchers)
and the **52wk-poster** chart+publish engine (chart-img PNG → Stocktwits API,
write-ahead intent, at-most-once safety).

## Pipeline (one tick)

WSJ new-52wk-highs feed → Yahoo v7 bulk quotes → Stocktwits watchers
(`src/source/rs_source.py`) → filter >$1B + rank fewest-watched (`src/select.py`)
→ chart-img 1-yr PNG (`src/chart.py`) → publisher (`src/publish/`) →
`state/posted.json`.

## Run locally

    pip install -r requirements-dev.txt
    python -m pytest                              # unit tests
    CHART_IMG_API_KEY=... python run.py --force   # one dry-run tick, any time

## Ops

- Cron: `.github/workflows/tick.yml`, every 30 min during market hours.
- Secrets (this repo → Settings → Secrets → Actions):
  - `CHART_IMG_API_KEY`
  - `STOCKTWITS_ACCESS_TOKEN` — **the dedicated RS account's token** (NOT the
    52wk-poster's account).
- Dry-run by default; `--live` requires `STOCKTWITS_ACCESS_TOKEN`.
- Spec + plan: `docs/superpowers/`.

## Durability

The WSJ feed and Yahoo crumb handshake are unofficial endpoints — they work
today but can rate-limit. The validation gate aborts a tick on a broken feed so
the account never posts garbage.
```

- [ ] **Step 3: Verify the workflow file parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/tick.yml')); print('yaml ok')"`
Expected: `yaml ok` (if PyYAML absent, skip — GitHub validates on push).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tick.yml README.md
git commit -m "chore: tick workflow + README (dedicated RS account)"
```

---

## Self-Review

**Spec coverage:**
- Account isolation → Global Constraints + Task 9 secret comments. ✓
- WSJ+Yahoo+watchers source → Task 7. ✓
- `Candidate.watchers` → Task 1. ✓
- Ascending-watchers, no floor → Task 6. ✓
- Caps 2/20, consecutive-day cooldown, validation gate → Tasks 2, 6. ✓
- chart-img 1yr `session=regular` → Task 4. ✓
- Post copy exact string → Task 5. ✓
- Write-ahead / at-most-once / dry-run default / `--live` hard error → Tasks 2, 5, 8. ✓
- Cron 30-min market hours → Task 9. ✓
- Unit + optional contract tests → each task; `contract` marker configured in Task 1 `pyproject.toml`. ✓

**Type consistency:** `Candidate` field order/names identical everywhere; `RSSource` / `RSSource().fetch_candidates()`; `compose_post_text(c)`; `pick`/`validate`/`ValidationError`; `PostResult(post_id, dry_run)`; `build_publisher(live, out_dir, today)` — all consistent across tasks.

**Placeholder scan:** none — every code step has complete code; ported files use exact `cp` from named source paths.
