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
