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


def test_missing_required_and_gaps_fail():
    con = make_db(drop=slice(100, 130))
    fails, report, _ = refresh.quality(con, ["SPY", "IEF", "GLD", "TLT", "QQQ"])
    assert report["missing_days"]["GLD"] == 30
    assert any("GLD is missing" in f for f in fails)
    assert any("tickers downloaded" in f for f in fails)
