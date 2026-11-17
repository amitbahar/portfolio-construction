import json
import math

import numpy as np
import pandas as pd
import pytest

from engine.api import DEFAULT_CONFIG, normalize_config, prepare, run_all
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


def test_too_short_period_is_rejected(market):
    _, rets, rf = market
    with pytest.raises(ValueError, match="years"):
        run_all({"assets": ["AAA", "BBB", "CCC"], "cap": 0.5, "start": "2015-06-01", "n_boot": 10}, rets, rf)
