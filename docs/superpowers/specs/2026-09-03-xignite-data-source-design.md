# Xignite data source + shadow mode + one-setting revert

**Goal.** Replace every scraped feed (Yahoo screener, Yahoo v7 quotes,
stockanalysis.com, WSJ list) with Ethan's licensed Xignite subscription,
without changing what gets posted, and with a switch that can be flipped back
in seconds if the new data misbehaves.

## Data mapping

| Need | Today (scraped) | Xignite |
|---|---|---|
| US-listed universe | WSJ new-52wk-highs list | Nasdaq Trader `nasdaqlisted.txt` + `otherlisted.txt` (NYSE/NYSE American; keyless, official, ETFs dropped) |
| Day high/low, 52wk high/low, price, %chg, volume, exchange, name, freshness | Yahoo v7 / stockanalysis.com quotes | `GlobalQuotes.GetGlobalDelayedQuotes`, 500 symbols/call, 15-min delayed |
| Market cap | Yahoo v7 / stockanalysis.com page data | `FactSetFundamentals.GetFundamentals` (MarketCapitalization, Millions), only for names that pass the 52-wk test |
| 1Y daily candles | stockanalysis.com | `GlobalHistorical.GetGlobalHistoricalQuotesRange` (SplitOnly) + today's candle from the delayed quote |
| Cashtag check, watchers (ranking axis) | Stocktwits public endpoint | unchanged (fetched only for names at a new high) |

Symbology: Nasdaq Trader and Xignite use dots (`BRK.B`); the
Candidate keeps the dash form so state/cooldown files stay compatible.
Preferreds/warrants/units are dropped by symbol shape (`-P?`, `-WT/-RT/-UN`,
5-letter `W/R/U`) before quoting.

Freshness gate: quote `Date` must equal today (ET). 52-wk test is
day-cumulative: `High >= High52Weeks` (Xignite's 52-wk figure already
includes today, verified 2026-09-03).

## The switch (revert path)

`DATA_SOURCE` env var, values `legacy` (default) | `xignite`. Read once in
`config.py`; picks both the candidate source AND the chart-history source.
In CI it is `${{ vars.DATA_SOURCE || 'legacy' }}` — a GitHub **repository
variable** changed in Settings → Secrets and variables → Actions → Variables.
No code change, no deploy: flip to `xignite` to go live, back to `legacy` to
revert. `--source` CLI flag overrides for local runs.

`xignite` without `XIGNITE_TOKEN` is a hard SourceError (tick fails red, never
silently posts from a different feed).

## Shadow mode (watch before switching)

After every live tick, a separate step runs `scripts/shadow.py`: it fetches
candidates from the source that is NOT active, compares against the active
source's candidates (dumped by the tick to `shadow/<date>/<HHMM>.active.json`),
and writes `shadow/<date>/<HHMM>.json`:

```
{ "active": "legacy", "shadow": "xignite", "counts": {...},
  "only_in_active": [...], "only_in_shadow": [...],
  "would_pick": {"legacy": [...], "xignite": [...]} }
```

`would_pick` replays `select.ranked_eligible` / `slot_count` (fewest watchers first) with the same posted-state, so we can see
whether the two feeds would have chosen the same tickers. The step is
`continue-on-error`, touches no state, and is committed with the tick's
state/output. `scripts/shadow_report.py [date]` prints the day's agreement.
Shadow keeps running after the switch (roles reversed) until `vars.SHADOW=off`.

## Out of scope

Real-time (SuperQuotes) quotes; Firestream; changing caps, copy, ranking or
any owner-locked rule. This is the relative-strength port of the highs poster design.
