import json
import math

import numpy as np
import pandas as pd
import pytest

from engine.api import DEFAULT_CONFIG, LOOKBACKS, lookback_grid, normalize_config, prepare, run_all
from engine.covariance import ledoit_wolf, shrunk_cov
from engine.simulate import net_returns, run
from engine.stats import deflated_sharpe, sharpe_moments
from engine.strategies import BENCHMARKS, STRATEGIES, equal_weight

TICKERS = ["AAA", "BBB", "CCC", "DDD", "SPY", "IEF"]


@pytest.fixture(scope="module")
def market():
    """Six correlated synthetic assets over ~7 years of business days, plus a T-bill series."""
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2010-01-01", periods=252 * 7)
    vols = np.array([0.25, 0.18, 0.12, 0.06, 0.16, 0.05]) / np.sqrt(252)
    corr = 0.5 * np.ones((6, 6)) + 0.5 * np.eye(6)
    corr[5, :5] = corr[:5, 5] = -0.2
    cov = corr * np.outer(vols, vols)
    rets = rng.multivariate_normal(np.full(6, 0.0003), cov, size=len(idx))
    prices = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=TICKERS)
    rf = pd.Series(0.02, index=idx)
    return prices, *prepare(prices, rf)


def test_ledoit_wolf_matches_sklearn():
    sk = pytest.importorskip("sklearn.covariance")
    X = np.random.default_rng(0).normal(size=(252, 8)) * 0.01
    assert np.allclose(ledoit_wolf(X), sk.LedoitWolf().fit(X).covariance_, rtol=1e-10, atol=1e-14)


def test_equal_weight_matches_manual_share_count(market):
    prices, rets, rf = market
    rets = rets.dropna()
    res = run(equal_weight, rets, rf.reindex(rets.index), lookback=252)
    t = res["targets"]
    for d0, d1 in zip(t.index[1:4], t.index[2:5]):
        shares = t.loc[d0] / prices.loc[d0]
        manual = (shares * prices.loc[d1]).sum() - 1
        engine = (1 + res["gross"].loc[d0:d1].iloc[1:]).prod() - 1
        assert manual == pytest.approx(engine, abs=1e-12)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_no_look_ahead(market, name):
    """Scrambling returns after a date must not change any target set on or before it."""
    _, rets, rf = market
    rets = rets.dropna()
    cut = rets.index[len(rets) // 2]
    future = rets.copy()
    future.loc[future.index > cut] *= -3
    a = run(STRATEGIES[name], rets, rf, lookback=252, cap=0.5)["targets"].loc[:cut]
    b = run(STRATEGIES[name], future, rf, lookback=252, cap=0.5)["targets"].loc[:cut]
    pd.testing.assert_frame_equal(a, b)
    # And the first simulated return is the day after the first rebalance.
    assert run(STRATEGIES[name], rets, rf, lookback=252)["gross"].index[0] > a.index[0]


@pytest.mark.parametrize("name", list(STRATEGIES))
@pytest.mark.parametrize("cap", [0.25, 0.4, 1.0])
def test_weights_valid(market, name, cap):
    _, rets, rf = market
    rets = rets.dropna()
    t = run(STRATEGIES[name], rets, rf, lookback=252, cap=cap)["targets"]
    assert np.allclose(t.sum(axis=1), 1, atol=1e-8)
    assert (t.values >= -1e-10).all()
    if name != "Equal weight":
        assert (t.values <= cap + 1e-6).all()


def test_risk_parity_equalizes_risk(market):
    _, rets, _ = market
    hist = rets.dropna().iloc[-252:]
    w = STRATEGIES["Risk parity"](hist, cap=1.0).values
    cov = shrunk_cov(hist)
    rc = w * (cov @ w)
    assert np.allclose(rc / rc.sum(), 1 / len(w), atol=1e-4)


def test_costs_reduce_returns_by_turnover(market):
    _, rets, rf = market
    rets = rets.dropna()
    res = run(STRATEGIES["Min variance"], rets, rf, lookback=252)
    diff = res["gross"] - net_returns(res, 10)
    assert (diff >= -1e-15).all()
    assert diff.sum() > 0


def test_deflated_sharpe_falls_with_more_trials(market):
    _, rets, rf = market
    r = rets["AAA"].dropna()
    m = sharpe_moments(r, rf)
    one = deflated_sharpe(m, [m["sr"]])
    many = deflated_sharpe(m, [m["sr"]] + list(np.linspace(-0.05, 0.05, 50)))
    assert 0 <= many < one <= 1


def test_config_validation():
    avail = DEFAULT_CONFIG["assets"]
    with pytest.raises(ValueError, match="infeasible"):
        normalize_config({"assets": ["SPY", "IEF"], "cap": 0.4}, avail)
    with pytest.raises(ValueError, match="band"):
        normalize_config({"band": 0.5}, avail)
    with pytest.raises(ValueError, match="Unknown"):
        normalize_config({"assets": ["SPY", "NOPE"]}, avail)
    c = normalize_config({"assets": ["TLT", "SPY", "GLD"], "strategies": ["HRP"]}, avail)
    assert c["assets"] == ["GLD", "SPY", "TLT"]
    assert c["strategies"] == ["Equal weight", "HRP"]  # equal weight is always the bar to beat


def test_run_all_end_to_end(market):
    _, rets, rf = market
    cfg = {"assets": ["AAA", "BBB", "CCC", "DDD"], "cap": 0.5, "n_boot": 50}
    out = run_all(cfg, rets, rf, trials={"old|Equal weight": 0.01}, config_key="k")
    json.dumps(out)  # must be JSON-serializable for the browser
    assert set(out["strategies"]) == set(STRATEGIES) | set(BENCHMARKS)
    assert out["n_trials"] == 1 + len(STRATEGIES)
    assert "Equal weight" not in out["vs_ew"]
    lo, hi = out["vs_ew"]["Min variance"]["d_sharpe_ci"]
    assert lo <= out["vs_ew"]["Min variance"]["d_sharpe"] <= hi
    alo, ahi = out["vs_ew"]["Min variance"]["d_sharpe_ci_adj"]
    assert ahi - alo > hi - lo  # 6 trials: the adjusted interval is wider than the raw 95% one
    assert math.isclose(out["vol_matched"]["rows"]["Equal weight"]["leverage"], 1.0)
    # Click order must not matter.
    again = run_all({**cfg, "assets": cfg["assets"][::-1]}, rets, rf)
    assert again["table"] == run_all(cfg, rets, rf)["table"]


def test_mixed_exchanges_use_weekly_covariance(market):
    _, rets, rf = market
    cfg = {"assets": ["AAA", "BBB", "CCC", "DDD"], "cap": 0.5, "n_boot": 20, "strategies": ["Min variance"]}
    same = run_all(cfg, rets, rf)
    mixed = run_all(cfg, rets, rf, exchanges={"AAA": "TASE"})
    assert same["config"]["cov_freq"] == "daily" and mixed["config"]["cov_freq"] == "weekly"
    assert same["table"]["Min variance"] != mixed["table"]["Min variance"]
    assert same["table"]["Equal weight"] == mixed["table"]["Equal weight"]


def test_too_short_period_is_rejected(market):
    _, rets, rf = market
    with pytest.raises(ValueError, match="years"):
        run_all({"assets": ["AAA", "BBB", "CCC"], "cap": 0.5, "start": "2015-06-01", "n_boot": 10}, rets, rf)


def test_no_trade_band(market):
    _, rets, rf = market
    rets = rets.dropna()
    turnover = {b: run(equal_weight, rets, rf, lookback=252, band=b)["turnover"] for b in (0.0, 0.02, 1.0)}
    assert turnover[0.0].sum() > turnover[0.02].sum() > 0
    assert turnover[1.0].sum() == 0  # a band wider than any drift never trades: buy and hold
    # A band of zero is the plain monthly rebalance.
    pd.testing.assert_series_equal(run(equal_weight, rets, rf, lookback=252)["gross"],
                                   run(equal_weight, rets, rf, lookback=252, band=0.0)["gross"])
    # When it does trade, it trades fully to the target.
    res = run(STRATEGIES["Min variance"], rets, rf, lookback=252, band=0.02)
    traded = res["turnover"][res["turnover"] > 0].index
    pos = res["weights"].index.get_indexer(traded) + 1
    pos = pos[pos < len(res["weights"])]
    after = res["weights"].iloc[pos]
    pd.testing.assert_frame_equal(after.reset_index(drop=True),
                                  res["targets"].loc[traded[:len(pos)]].reset_index(drop=True), atol=1e-12)


def test_band_applies_to_run_all(market):
    _, rets, rf = market
    cfg = {"assets": ["AAA", "BBB", "CCC", "DDD"], "cap": 0.5, "n_boot": 20, "strategies": ["Risk parity"]}
    plain, banded = run_all(cfg, rets, rf), run_all({**cfg, "band": 0.05}, rets, rf)
    for k in ("Equal weight", "Risk parity", "60/40"):
        assert banded["table"][k]["net"]["ann_turnover"] < plain["table"][k]["net"]["ann_turnover"]


def test_lookback_grid(market):
    _, rets, rf = market
    cfg = {"assets": ["AAA", "BBB", "CCC", "DDD"], "cap": 0.5, "n_boot": 20, "strategies": ["Min variance", "HRP"]}
    keep = {}
    run_all(cfg, rets, rf, keep=keep)
    g = lookback_grid(cfg, rets, rf, base=keep)
    json.dumps(g)
    assert g["lookbacks"] == list(LOOKBACKS) and g["selected"] == 252
    assert set(g["rows"]) == {"Equal weight", "Min variance", "HRP"}
    for lb in LOOKBACKS:
        assert g["rows"]["Equal weight"][str(lb)]["d_sharpe"] == 0
    # Reusing run_all's returns gives the same answer as simulating every lookback from scratch.
    assert lookback_grid(cfg, rets, rf)["rows"] == g["rows"]
    # Lookbacks that leave too short a common period are dropped, and with fewer than two left, all are.
    short = lookback_grid({**cfg, "lookback": 126, "end": "2014-03-31"}, rets, rf)
    assert short["lookbacks"] == [126, 252] and short["skipped"] == [504] and short["years"] >= 3
    none = lookback_grid({**cfg, "start": "2014-01-01"}, rets, rf)
    assert none["lookbacks"] == [] and "years" in none["note"]
