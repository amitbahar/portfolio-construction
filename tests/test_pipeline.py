import datetime as dt
import sqlite3

import numpy as np
import pandas as pd

from pipeline import refresh


def make_db(days=300, end=None, drop=None):
    end = pd.Timestamp(end or dt.date.today())
    idx = pd.bdate_range(end=end, periods=days)
    rng = np.random.default_rng(0)
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (days, 3)), axis=0),
                         index=idx, columns=["SPY", "IEF", "GLD"])
    if drop:
        close.loc[close.index[drop], "GLD"] = np.nan
    con = sqlite3.connect(":memory:")
    refresh.load(con, close, pd.Series(4.0, index=idx))
    return con


def test_clean_data_passes():
    fails, report, _ = refresh.quality(make_db(), ["SPY", "IEF", "GLD"])
    assert fails == []
    assert report["missing_days"] == {}


def test_stale_data_fails():
    fails, _, _ = refresh.quality(make_db(end="2020-01-01"), ["SPY", "IEF", "GLD"])
    assert any("days old" in f for f in fails)


def test_foreign_prices_are_converted_to_usd():
    idx = pd.bdate_range(end=dt.date.today(), periods=50)
    close = pd.DataFrame({"SPY": 100.0, "IEF": 100.0, "KSM-F74.TA": 36000.0}, index=idx)
    fx = pd.Series(3.6, index=idx[::2])  # rates on alternate days: the view uses the latest one
    con = sqlite3.connect(":memory:")
    refresh.load(con, close, pd.Series(4.0, index=idx), {"ILS": fx})
    usd = pd.read_sql("SELECT ticker, MIN(adj_close) lo, MAX(adj_close) hi FROM prices_usd GROUP BY ticker",
                      con).set_index("ticker")
    assert usd.loc["KSM-F74.TA", "lo"] == usd.loc["KSM-F74.TA", "hi"] == 10000.0
    fails, _, _ = refresh.quality(con, ["SPY", "IEF", "KSM-F74.TA"])
    assert fails == []


def test_missing_fx_fails():
    idx = pd.bdate_range(end=dt.date.today(), periods=50)
    close = pd.DataFrame({"SPY": 100.0, "IEF": 100.0, "KSM-F74.TA": 36000.0}, index=idx)
    con = sqlite3.connect(":memory:")
    refresh.load(con, close, pd.Series(4.0, index=idx))
    fails, _, _ = refresh.quality(con, ["SPY", "IEF", "KSM-F74.TA"])
    assert any("exchange rate" in f for f in fails)


def test_missing_required_and_gaps_fail():
    con = make_db(drop=slice(100, 130))
    fails, report, _ = refresh.quality(con, ["SPY", "IEF", "GLD", "TLT", "QQQ"])
    assert report["missing_days"]["GLD"] == 30
    assert any("GLD is missing" in f for f in fails)
    assert any("tickers downloaded" in f for f in fails)
