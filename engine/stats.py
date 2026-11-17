"""Performance metrics and the statistics used to judge whether a difference is real."""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329


def drawdown(r):
    wealth = (1 + r).cumprod()
    return wealth / wealth.cummax() - 1


def max_dd_and_recovery(r):
    """Max drawdown, its trough date, and trading days from trough back to the prior peak (None if
    not recovered by the end of the sample)."""
    dd = drawdown(r)
    trough = dd.idxmin()
    after = dd.loc[trough:]
    recovered = after[after >= 0]
    days = len(dd.loc[trough:recovered.index[0]]) - 1 if len(recovered) else None
    return dd.min(), trough, days


def metrics(r, rf, turnover, targets):
    rf = rf.reindex(r.index)
    years = len(r) / 252
    ex = r - rf
    mdd, trough, rec = max_dd_and_recovery(r)
    return {
        "ann_return": (1 + r).prod() ** (1 / years) - 1,
        "ann_vol": r.std() * np.sqrt(252),
        "sharpe": ex.mean() / ex.std() * np.sqrt(252),
        "max_dd": mdd,
        "max_dd_trough": trough.strftime("%Y-%m-%d"),
        "recovery_days": rec,
        "ann_turnover": turnover.sum() / years,
        "avg_max_weight": targets.max(axis=1).mean(),
    }


def _sharpe(ex, axis=0):
    return ex.mean(axis=axis) / ex.std(axis=axis, ddof=1) * np.sqrt(252)


def _max_dd_paths(r):
    """Max drawdown of each row of a (paths x days) array of returns."""
    wealth = np.cumprod(1 + r, axis=1)
    return (wealth / np.maximum.accumulate(wealth, axis=1) - 1).min(axis=1)


def block_bootstrap_vs(r_a, r_b, rf, block=21, n_boot=2000, seed=0, chunk=250):
    """Circular block bootstrap of the differences a - b in Sharpe, annual mean return and max
    drawdown. Both series are resampled with the same blocks so their correlation is preserved.

    The drawdown interval is computed on resampled paths, which scramble the ordering of crises
    beyond the block length: read it as a rough guide to how fragile the drawdown gap is.
    """
    rng = np.random.default_rng(seed)
    a = r_a.values
    b = r_b.values
    f = rf.reindex(r_a.index).values
    n = len(a)
    n_blocks = math.ceil(n / block)
    d_s, d_r, d_dd = [], [], []
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        starts = rng.integers(0, n, size=(m, n_blocks))
        idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(m, -1)[:, :n]
        A, B, F = a[idx], b[idx], f[idx]
        d_s.append(_sharpe(A - F, axis=1) - _sharpe(B - F, axis=1))
        d_r.append((A.mean(axis=1) - B.mean(axis=1)) * 252)
        d_dd.append(_max_dd_paths(A) - _max_dd_paths(B))
    d_s, d_r, d_dd = (np.concatenate(x) for x in (d_s, d_r, d_dd))
    ci = lambda x: [float(v) for v in np.percentile(x, [2.5, 97.5])]
    return {
        "d_sharpe": float(_sharpe(a - f) - _sharpe(b - f)),
        "d_sharpe_ci": ci(d_s),
        "d_sharpe_se": float(d_s.std(ddof=1)),
        "p_not_better": float((d_s <= 0).mean()),
        "d_ann_return": float((a.mean() - b.mean()) * 252),
        "d_ann_return_ci": ci(d_r),
        "d_max_dd": float(drawdown(r_a).min() - drawdown(r_b).min()),
        "d_max_dd_ci": ci(d_dd),
    }


def vol_matched(r, rf, target_vol):
    """Scale a strategy's excess return to `target_vol`, borrowing or lending at the risk-free rate:
    r' = rf + k (r - rf). k uses full-sample volatility, so this is a comparison aid, not a strategy
    that could have been run in real time. Borrowing costs above the T-bill rate are ignored."""
    rf = rf.reindex(r.index)
    k = target_vol / (r.std() * np.sqrt(252))
    scaled = rf + k * (r - rf)
    years = len(r) / 252
    return {"leverage": float(k), "ann_return": float((1 + scaled).prod() ** (1 / years) - 1),
            "max_dd": float(drawdown(scaled).min())}


def sharpe_moments(r, rf):
    """Non-annualized Sharpe ratio, skewness, kurtosis and length of daily excess returns."""
    ex = (r - rf.reindex(r.index)).values
    sd = ex.std(ddof=1)
    z = (ex - ex.mean()) / ex.std(ddof=0)
    return {"sr": float(ex.mean() / sd), "skew": float((z ** 3).mean()),
            "kurt": float((z ** 4).mean()), "n": int(len(ex))}


def expected_max_sharpe(trial_srs):
    """Expected maximum of N independent Sharpe estimates under the null of zero true Sharpe
    (Bailey & Lopez de Prado 2014). Zero for a single trial."""
    n = len(trial_srs)
    if n < 2:
        return 0.0
    sd = float(np.std(trial_srs, ddof=1))
    return sd * ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n) + EULER_GAMMA * norm.ppf(1 - 1 / (n * math.e)))


def deflated_sharpe(m, trial_srs):
    """Probability that the true Sharpe exceeds the best you would expect from luck alone after
    `len(trial_srs)` trials. With one trial it is the probabilistic Sharpe ratio vs zero."""
    sr0 = expected_max_sharpe(trial_srs)
    sr, g3, g4, n = m["sr"], m["skew"], m["kurt"], m["n"]
    denom = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2, 1e-12))
    return float(norm.cdf((sr - sr0) * math.sqrt(n - 1) / denom))


def adjusted_ci(est, se, n_tests, level=0.95):
    """Normal-approximation interval widened for `n_tests` comparisons (Bonferroni): what the
    interval should be when the result was picked as the best of many tries."""
    z = norm.ppf(1 - (1 - level) / (2 * max(n_tests, 1)))
    return [est - z * se, est + z * se]
