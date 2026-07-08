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
