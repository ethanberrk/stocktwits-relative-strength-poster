# Stocktwits Relative-Strength Poster — Design

**Date:** 2026-07-08
**Status:** Approved (pending spec review)

## Goal

Every 30 minutes during market hours, post the **least-watched** US stocks
over $1B market cap that printed a **new 52-week high today** to Stocktwits,
each with a 1-year chart, framed as *undiscovered breakouts*. The ranking axis
is Stocktwits watcher count ascending — the fewer watchers, the earlier you are
to the move (the "relative strength" edge from the `stocktwits-relative-strength`
prototype).

This project combines two prior projects:

- **`stocktwits-relative-strength`** — the data pipeline: WSJ new-52wk-highs
  universe → Yahoo enrichment → Stocktwits watchers → filter >$1B → rank
  ascending by watchers. (Previously rendered a static Vercel page.)
- **`stocktwits-52wk-poster`** — the posting engine: source → select → chart-img
  1yr PNG → Stocktwits API publisher, with write-ahead intent, cooldown/caps,
  and at-most-once safety, on a 30-minute market-hours cron.

## Account isolation (critical)

This poster publishes to a **different Stocktwits account** than the
`stocktwits-52wk-poster`. Isolation is structural: this is a **separate repo
with its own GitHub Secrets**, so its `STOCKTWITS_ACCESS_TOKEN` holds the new
account's token and nothing is shared with the other project (no shared state,
no shared token, no cross-posting risk). During setup, the token secret in
THIS repo must be minted from the new account.

## Architecture

Mirrors the `stocktwits-52wk-poster` engine. Two pieces change: the **Source**
(WSJ + watchers RS pipeline instead of the yfinance screen) and the **ranking**
(ascending by watchers instead of descending by market cap). Everything else —
chart fetch, publishers, write-ahead state, at-most-once safety, the `run.py`
tick loop — is ported as-is.

```
WSJ 52wk-highs feed ─┐
Yahoo bulk quotes  ──┼─► RSSource ─► select.pick() ─► chart-img 1yr PNG ─► Publisher ─► state/posted.json
Stocktwits watchers ─┘   (Candidate)   (fewest          (session=regular)   (API / dry-run)
                                        watchers first)
```

### One tick (`run.py`)

1. If outside market hours and not `--force`: no-op.
2. `RSSource.fetch_candidates()` → list of `Candidate` (each carries watcher count).
3. `select.validate(candidates)` → abort if the feed looks broken.
4. `select.pick(candidates, posted, today)` → up to N names, fewest watchers first,
   after cooldown/caps.
5. For each pick: verify the Stocktwits symbol resolves, fetch its chart PNG.
   Any name that fails either step is skipped and stays eligible.
6. **Write-ahead:** record `pending` intent for every ready name and (in CI)
   git-push it *before* any post. A crash or block after this can only lose a
   post, never duplicate one.
7. Publish each; on success mark `posted` with the returned message id; on an
   expected publish failure leave it `pending` (blocked from re-selection today).

## Components

| Module | Role | Origin |
|---|---|---|
| `src/source/base.py` | `Candidate` dataclass **+ new `watchers: int` field**; `HighsSource` ABC; `SourceError` | poster + edit |
| `src/source/rs_source.py` | Port of `fetch_wsj.py` as a `HighsSource`: WSJ highs → Yahoo v7 crumb quotes → Stocktwits watchers → keep >$1B names with a resolved watcher count; returns `Candidate`s | ported from RS |
| `src/select.py` | `validate()` (implausible-count gate) + `pick()` **ranked ascending by watchers**, applies caps/cooldown | poster, re-sorted |
| `src/chart.py` | chart-img v2 1yr PNG, `session=regular`; exchange→TradingView prefix mapping extended for Stocktwits exchange strings (`NYSEArca`, `NYSEAmerican`, `BATS`, `AMEX`, …) | poster + edit |
| `src/stocktwits.py` | `symbol_exists()` pre-post check + `st_symbol()` cashtag symbology | poster as-is |
| `src/publish/base.py` | `Publisher` ABC, `PostResult`, `compose_post_text()` → new RS copy | poster + edit |
| `src/publish/stocktwits_pub.py` | `StocktwitsPublisher` — multipart POST to the Stocktwits create endpoint | poster as-is |
| `src/publish/dryrun.py` | `DryRunPublisher` — writes intended post + chart to `output/YYYY-MM-DD/` | poster as-is |
| `src/publish/record.py` | shared record-writing helper for publishers | poster as-is |
| `src/state.py` | `load_posted`, `append_posted`, `mark_posted`, `is_blocked`, `daily_count`, `is_market_hours` | poster as-is |
| `run.py` | the tick orchestration + `--force` / `--live` / `--sync-state` flags | poster as-is |
| `config.py` | all knobs | poster + edit |

## Data model

`Candidate` (frozen dataclass) gains one field vs. the poster:

```python
@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str
    exchange: str            # TradingView-style prefix source ("NASDAQ" | "NYSE" | "AMEX" | ...)
    price: float
    pct_change_today: float
    market_cap: float
    week52_high: float
    security_type: str
    watchers: int            # NEW — Stocktwits watchlist_count; the ranking axis
```

## Source: `RSSource` (ported from `fetch_wsj.py`)

- **Universe:** WSJ Market Data Center async feed for New 52-Week Highs. Drops
  dotted tickers (preferreds/units) and names matching the non-common-equity
  regex (ETF/Fund/Pfd/Notes/Units/Warrants/Rights/Acquisition Corp).
- **Enrichment:** Yahoo v7 bulk quote via the cookie+crumb handshake (market cap,
  price, %chg, 52wk levels), batched ≤40 symbols/request with retry/backoff.
- **Watchers + logo + exchange + title:** Stocktwits streams endpoint, fetched
  concurrently (thread pool) with retry/backoff.
- **Filter:** keep rows with `market_cap > $1B` **and** a resolved watcher count.
  Because the WSJ feed *is* today's new-highs list, freshness is inherent (no
  per-quote "traded today" gate needed); the validation gate below guards
  against a stale/broken feed.
- **Return:** `list[Candidate]`, each with `watchers` populated.

## Selection: `select.pick()` (re-sorted)

```python
eligible = [c for c in candidates
            if c.market_cap >= MIN_MARKET_CAP
            and not state.is_blocked(c.ticker, posted, today)]
eligible.sort(key=lambda c: c.watchers)          # fewest watchers first — NO floor
remaining_today = MAX_PER_DAY - state.daily_count(posted, today)
n = max(0, min(MAX_PER_TICK, remaining_today))
return eligible[:n]
```

- **Order:** pure ascending watchers, **no watcher floor** (per decision).
- **Caps:** `MAX_PER_TICK = 2`, `MAX_PER_DAY = 20`, never the same ticker on
  consecutive trading days (`is_blocked`).
- **Validation gate:** `validate()` aborts the tick if the candidate count
  exceeds `MAX_PLAUSIBLE_HIGHS` (500) — a broken feed never posts.

## Post copy

```python
def compose_post_text(c: Candidate) -> str:
    return f"${st_symbol(c.ticker)} undiscovered breakout with {c.watchers} watchers"
```

No price/%chg/market-cap in the copy — those go stale between the tick and the
reader; the attached chart carries the quantitative story. Watcher count is
stable enough to include. Cashtag uses Stocktwits symbology via `st_symbol()`.

## Chart

chart-img v2 advanced-chart, 1-day interval / 1-year range, 800×450, light
theme, `session=regular` (drops pre/post-market so an opening-minute capture
never freezes an extended-hours price line). Symbol is `EXCHANGE:TICKER` using
the TradingView prefix; the exchange map is extended to cover the exchange
strings Stocktwits returns.

## Publishing & safety (ported unchanged)

- **Dry-run by default.** Each tick writes what *would* be posted (text + chart)
  to `output/YYYY-MM-DD/` and can commit it. Phase 1 runs this way to verify
  selections and copy before going live.
- **`--live`** posts for real and **requires** `STOCKTWITS_ACCESS_TOKEN` — a
  hard error if the token is missing, never a silent downgrade to dry-run.
- **Write-ahead + at-most-once:** pending intents recorded (and pushed in CI)
  before any post; a crash/block loses a post at worst, never duplicates.

## Config (`config.py`)

```
MIN_MARKET_CAP      = 1_000_000_000
MAX_PER_TICK        = 2
MAX_PER_DAY         = 20
MAX_PLAUSIBLE_HIGHS = 500
MARKET_TZ           = "America/New_York"
MARKET_OPEN         = (9, 30)
MARKET_CLOSE        = (16, 0)
CHART_IMG_URL, STOCKTWITS_SYMBOL_URL, STOCKTWITS_CREATE_URL, STOCKTWITS_USER_AGENT
NAME_EXCLUDE_RE     # non-common-equity name filter
WSJ_MDC_URL         # New 52-Week Highs feed (NEW)
```

## Ops

- **Cron:** `.github/workflows/tick.yml`, every 30 minutes during market hours;
  a failed tick emails via GitHub and missing one tick is harmless.
- **Secrets (this repo's Actions):**
  - `CHART_IMG_API_KEY` — chart-img.
  - `STOCKTWITS_ACCESS_TOKEN` — **the new Stocktwits account's** token (distinct
    from the 52wk-poster's).
- **Rollout:** Phase 1 dry-run → verify → flip `tick.yml` to `--live` with the
  token secret present, ramping posts up as the poster did.

## Testing

- Unit tests for `select.pick()` ordering (ascending watchers), caps, cooldown,
  and the validation gate — fixture candidates, no network.
- Unit tests for `compose_post_text()` copy and `st_symbol()` symbology.
- `RSSource` parsing tests against a captured WSJ feed + Yahoo + Stocktwits
  fixture (no live calls in unit runs).
- Optional live contract tests (marked, opt-in) hitting the real feeds, like the
  poster's `-m contract` suite.

## Durability caveats (inherited)

The WSJ MDC feed and the Yahoo crumb handshake are **unofficial endpoints** —
they work today but can change or rate-limit without notice. The validation gate
protects the posting stream when the feed breaks (abort, don't post garbage). For
a long-lived version, swap them for keyed/official market-data APIs.

## Explicitly out of scope (YAGNI)

- The static Vercel leaderboard page (that stays in `stocktwits-relative-strength`).
- Watcher floors / bands (decided against — pure fewest-first).
- Backfilling or migrating any state from the other two repos.
