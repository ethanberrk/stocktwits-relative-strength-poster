"""Unit tests for the Xignite relative-strength source: universe parsing, the
day-cumulative test, candidate hygiene (incl. watchers), and the fetch flow."""
from datetime import date

import pytest

import config
from src import xignite
from src.source import xignite_source as xs
from src.source.base import SourceError

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
AAAP|Pacer Barings CLO Market Flex ETF|G|N|N|100|Y|N
BFRGW|Bullfrog AI Holdings, Inc. - Warrants|S|N|N|100|N|N
BRKHU|Burtech Acquisition Corp II - Units|S|N|N|100|N|N
ZTST|Test Issue Inc|Q|Y|N|100|N|N
File Creation Time: 0903202614:01|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
A|Agilent Technologies, Inc. Common Stock|N|A|N|100|N|A
BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK B|N|100|N|BRK.B
BAC$B|Bank of America Depositary Shares Preferred Series GG|N|BACpB|N|100|N|BAC-B
AAC.U|Ares Acquisition Corporation III Units|N|AAC.U|N|100|N|AAC=
SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY
BTG|B2Gold Corp Common Shares|A|BTG|N|100|N|BTG
UAMY|United States Antimony Corp|A|UAMY|N|100|N|UAMY
"""


def _fetch(url):
    return NASDAQ if "nasdaqlisted" in url else OTHER


def test_canonical_ticker_shapes():
    assert xs.canonical_ticker("AAPL") == "AAPL"
    assert xs.canonical_ticker("BRK.B") == "BRK-B"
    assert xs.canonical_ticker("BAC$B") is None          # preferred
    assert xs.canonical_ticker("AAC.U") is None          # unit
    assert xs.canonical_ticker("ACHR.W") is None         # warrant
    assert xs.canonical_ticker("BFRGW") is None          # Nasdaq warrant shape
    assert xs.canonical_ticker("") is None


def test_listed_universe_filters_and_dash_form(monkeypatch):
    monkeypatch.setattr(config, "MIN_UNIVERSE_SIZE", 1)
    pairs = xs.listed_universe(_fetch)
    assert [t for t, _ in pairs] == ["AAPL", "A", "BRK-B", "BTG", "UAMY"]


def test_listed_universe_floor_trips_on_tiny_list():
    with pytest.raises(SourceError, match="look broken"):
        xs.listed_universe(_fetch)


def test_listed_universe_empty_files_fail():
    with pytest.raises(SourceError):
        xs.listed_universe(lambda url: "")


def _q(**kw):
    base = {"Identifier": "DELL", "Outcome": "Success", "Date": "9/3/2026",
            "Open": 500, "High": 530.78, "Low": 499, "Last": 528.0,
            "High52Weeks": 530.78, "PercentChangeFromPreviousClose": 2.1,
            "Security": {"Name": "Dell Technologies Inc", "Market": "NYSE"}}
    base.update(kw)
    return base


TODAY = date(2026, 9, 3)
W = {"watchlist_count": 30701, "exchange": "NYSE"}


def test_is_new_high_day_cumulative_and_fresh():
    assert xs.is_new_high(_q(), TODAY)
    assert xs.is_new_high(_q(High=530.78, High52Weeks=530.7800001), TODAY)   # float slack
    assert not xs.is_new_high(_q(High=530.0), TODAY)                        # below
    assert not xs.is_new_high(_q(Date="9/2/2026"), TODAY)                   # stale / holiday
    assert not xs.is_new_high(_q(High=0, High52Weeks=0), TODAY)             # no data


def test_build_candidate_maps_fields():
    c = xs.build_candidate("DELL", "Dell (list name)", _q(), 3.6e11, W)
    assert (c.ticker, c.exchange, c.price, c.market_cap, c.week52_high, c.watchers) == \
        ("DELL", "NYSE", 528.0, 3.6e11, 530.78, 30701)
    assert c.pct_change_today == 2.1 and c.security_type == "EQUITY"
    assert c.name == "Dell Technologies Inc"


def test_build_candidate_hygiene():
    assert xs.build_candidate("X", "n", _q(Security={"Name": "X", "Market": "OTC"}), 5e9, W) is None
    assert xs.build_candidate("X", "n", _q(Security={"Name": "X Warrants", "Market": "NYSE"}), 5e9, W) is None
    assert xs.build_candidate("X", "n", _q(), None, W) is None                       # no mcap
    assert xs.build_candidate("X", "n", _q(), config.MIN_MARKET_CAP - 1, W) is None  # below floor
    assert xs.build_candidate("X", "n", _q(Last=0), 5e9, W) is None
    assert xs.build_candidate("X", "n", _q(), 5e9, {}) is None                       # no watchers
    assert xs.build_candidate("X", "n", _q(), 5e9, {"watchlist_count": None}) is None


def test_fetch_candidates_only_enriches_hits(monkeypatch):
    universe = [("DELL", "Dell"), ("AAPL", "Apple"), ("SMALL", "Small Co"), ("NOWATCH", "No Watch")]
    quotes = {"DELL": _q(), "AAPL": _q(Identifier="AAPL", High=300, High52Weeks=344),
              "SMALL": _q(Identifier="SMALL"), "NOWATCH": _q(Identifier="NOWATCH")}
    asked_caps, asked_watch = [], []

    def caps(tickers):
        asked_caps.extend(tickers)
        return {"DELL": 3.6e11, "SMALL": 5e8, "NOWATCH": 7e9}

    def watchers(tickers):
        asked_watch.extend(tickers)
        return {"DELL": W, "SMALL": W, "NOWATCH": {}}
    monkeypatch.setattr(xignite, "quotes", lambda tks: quotes)
    monkeypatch.setattr(xignite, "market_caps", caps)
    monkeypatch.setattr(xs, "datetime", _FakeDT)
    out = xs.XigniteSource(universe=lambda: universe, watchers=watchers).fetch_candidates()
    assert asked_caps == ["DELL", "SMALL", "NOWATCH"]     # AAPL not at a high -> never priced
    assert asked_watch == ["DELL", "SMALL", "NOWATCH"]    # nor watcher-counted
    assert [c.ticker for c in out] == ["DELL"]            # SMALL < $1B, NOWATCH lacks watchers


def test_fetch_candidates_zero_quotes_is_broken_feed(monkeypatch):
    monkeypatch.setattr(xignite, "quotes", lambda tks: {})
    with pytest.raises(SourceError, match="zero quotes"):
        xs.XigniteSource(universe=lambda: [("AAPL", "Apple")],
                         watchers=lambda t: {}).fetch_candidates()


class _FakeDT:
    @staticmethod
    def now(tz=None):
        from datetime import datetime
        return datetime(2026, 9, 3, 14, 0, tzinfo=tz)
