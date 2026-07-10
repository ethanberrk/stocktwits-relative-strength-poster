# Min-history filter (skip recent IPOs)

**Date:** 2026-07-10
**Status:** approved (Ethan, in-session)

## Problem

The poster charts every pick as a 1-year candlestick. A name public for
less than a year (e.g. GMRS on 2026-07-10, ~6 weeks of candles) renders a
sparse, misleading "1Y" chart. Recent IPOs also naturally have few
watchers, so the fewest-watched ranking actively selects them.

## Decision

Gate on the depth of the price history we already fetch for the chart —
no new data source or API call.

- In `src/chart.py::_fetch_history`, after history is assembled: if the
  earliest candle is younger than `config.MIN_HISTORY_DAYS` (330 days,
  ~11 months — slack so a barely-year-old name isn't wrongly rejected),
  raise `ChartError` naming the first-candle date.
- `run.py` already treats `ChartError` as "skip this name, try the next
  eligible one", so a recent IPO can never consume a post slot, and the
  next least-watched name takes its place within the same tick.
- Knob lives in `config.py` (`MIN_HISTORY_DAYS = 330`) per the "all knobs
  in one place" rule.

## Alternative rejected

IPO-date lookup from an external service: new dependency + extra calls
for information the chart history already carries.

## Tests

- History starting ~60 days ago → `ChartError` (recent-IPO case).
- History starting ≥330 days ago → passes.
- Boundary: first candle exactly 330 days old → passes.
- Existing `_fetch_history` fixtures updated to include a year-old first
  candle so the append-today tests still exercise their own behavior.
