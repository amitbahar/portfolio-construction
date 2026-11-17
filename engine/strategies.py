"""Weighting rules.

Each rule takes a (lookback x assets) DataFrame of daily returns, the per-asset weight cap and the
annual risk-free rate known on the rebalance date, and returns long-only weights summing to 1.
"""
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from .covariance import shrunk_cov


def _simplex_qp(obj, n, cap, lower=0.0, x0=None, jac=None):
    """Minimize obj(w) subject to sum(w) = 1 and lower <= w <= cap."""
    res = minimize(
        obj,
        x0=np.full(n, 1 / n) if x0 is None else x0,
        jac=jac,
        method="SLSQP",
        bounds=[(lower, cap)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1, "jac": lambda w: np.ones(n)},
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not res.success:
        raise RuntimeError(f"Optimization failed: {res.message}")
    w = np.clip(res.x, 0, None)
    return w / w.sum()


def cap_weights(w, cap):
    """Clip weights at `cap` and redistribute the excess pro rata to uncapped assets."""
    w = np.asarray(w, float).copy()
    for _ in range(len(w)):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = w < cap - 1e-12
        w[free] += excess * w[free] / w[free].sum()
    return w


def _min_var_weights(cov, cap):
    return _simplex_qp(lambda w: w @ cov @ w, len(cov), cap, jac=lambda w: 2 * cov @ w)


def equal_weight(hist, cap=1.0, rf=0.0):
    n = hist.shape[1]
    return pd.Series(1 / n, index=hist.columns)


def min_variance(hist, cap=1.0, rf=0.0):
    return pd.Series(_min_var_weights(shrunk_cov(hist), cap), index=hist.columns)


def risk_parity(hist, cap=1.0, rf=0.0):
    """Equal risk contribution. Solved exactly via the log-barrier form
    min 0.5 w'Cw - (1/n) sum(log w), whose solution (normalized) has equal risk contributions.
    Falls back to a constrained least-squares fit on risk contributions if the cap binds."""
    cov = shrunk_cov(hist)
    n = len(cov)
    res = minimize(
        lambda y: 0.5 * y @ cov @ y - np.log(y).sum() / n,
        x0=np.full(n, 1 / n),
        jac=lambda y: cov @ y - 1 / (n * y),
        method="L-BFGS-B",
        bounds=[(1e-8, None)] * n,
    )
    w = res.x / res.x.sum()
    if w.max() > cap:
        def obj(w):
            rc = w * (cov @ w) / (w @ cov @ w)
            return ((rc - 1 / n) ** 2).sum()
        w = _simplex_qp(obj, n, cap, lower=1e-4, x0=cap_weights(w, cap))
    return pd.Series(w, index=hist.columns)


def _best_capped_return(mu, cap):
    """Highest expected return attainable under the cap: fill the best assets up to the cap."""
    w = np.zeros(len(mu))
    left = 1.0
    for i in np.argsort(-mu):
        w[i] = min(cap, left)
        left -= w[i]
        if left <= 1e-12:
            break
    return w


def _tangency_convex(cov, mu, cap):
    """Max Sharpe via the convex reformulation: min y'Cy s.t. mu'y = 1, y >= 0,
    y_i <= cap * sum(y); then w = y / sum(y). Inputs are rescaled to keep SLSQP well conditioned."""
    n = len(mu)
    m = mu / np.abs(mu).max()
    c = cov / np.trace(cov) * n
    pos = m > 0
    res = minimize(
        lambda y: y @ c @ y,
        x0=np.where(pos, 1.0, 0.0) / m[pos].sum(),
        jac=lambda y: 2 * c @ y,
        method="SLSQP",
        bounds=[(0, None)] * n,
        constraints=[{"type": "eq", "fun": lambda y: y @ m - 1, "jac": lambda y: m},
                     {"type": "ineq", "fun": lambda y: cap * y.sum() - y,
                      "jac": lambda y: cap * np.ones((n, n)) - np.eye(n)}],
        options={"ftol": 1e-14, "maxiter": 500},
    )
    if not res.success:
        return None
    w = np.clip(res.x, 0, None)
    return w / w.sum()


def _tangency_sweep(cov, mu, cap, w_mv, w_top, points=25):
    """Fallback: sweep the capped efficient frontier and keep the best Sharpe point."""
    n = len(mu)
    best, best_sr = w_mv, (w_mv @ mu) / np.sqrt(w_mv @ cov @ w_mv)
    for target in np.linspace(max(w_mv @ mu, 0), w_top @ mu, points)[1:]:
        res = minimize(lambda w: w @ cov @ w, x0=w_top, jac=lambda w: 2 * cov @ w, method="SLSQP",
                       bounds=[(0, cap)] * n,
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1},
                                    {"type": "ineq", "fun": lambda w, t=target: w @ mu - t}],
                       options={"ftol": 1e-12, "maxiter": 1000})
        if not res.success:
            continue
        w = np.clip(res.x, 0, None)
        w /= w.sum()
        sr = (w @ mu) / np.sqrt(w @ cov @ w)
        if sr > best_sr:
            best, best_sr = w, sr
    return best


def max_sharpe(hist, cap=1.0, rf=0.0):
    """Mean-variance tangency using raw trailing mean returns: deliberately naive, to expose
    estimation error. Holds min variance when no capped portfolio is expected to beat cash."""
    cov = shrunk_cov(hist)
    mu = hist.mean().values * 252 - rf
    w_top = _best_capped_return(mu, cap)
    if w_top @ mu <= 0:
        return pd.Series(_min_var_weights(cov, cap), index=hist.columns)
    w = _tangency_convex(cov, mu, cap)
    if w is None:
        w = _tangency_sweep(cov, mu, cap, _min_var_weights(cov, cap), w_top)
    return pd.Series(w, index=hist.columns)


def hrp(hist, cap=1.0, rf=0.0):
    """Hierarchical Risk Parity (Lopez de Prado 2016), then capped."""
    cov = shrunk_cov(hist)
    sd = np.sqrt(np.diag(cov))
    corr = np.clip(cov / np.outer(sd, sd), -1, 1)
    dist = np.sqrt(0.5 * (1 - corr))
    np.fill_diagonal(dist, 0)
    order = list(leaves_list(linkage(squareform(dist, checks=False), "single")))

    def cluster_var(idx):
        c = cov[np.ix_(idx, idx)]
        ivp = 1 / np.diag(c)
        ivp /= ivp.sum()
        return ivp @ c @ ivp

    w = np.ones(len(cov))
    clusters = [order]
    while clusters:
        clusters = [c[i:j] for c in clusters for i, j in ((0, len(c) // 2), (len(c) // 2, len(c))) if len(c) > 1]
        for k in range(0, len(clusters), 2):
            left, right = clusters[k], clusters[k + 1]
            vl, vr = cluster_var(left), cluster_var(right)
            alpha = 1 - vl / (vl + vr)
            w[left] *= alpha
            w[right] *= 1 - alpha
    return pd.Series(cap_weights(w / w.sum(), cap), index=hist.columns)


def fixed(weights):
    """Static target weights, e.g. benchmarks. Missing tickers get 0. Ignores the cap."""
    def rule(hist, cap=1.0, rf=0.0):
        return pd.Series(weights, index=hist.columns).fillna(0.0)
    return rule


STRATEGIES = {
    "Equal weight": equal_weight,
    "Min variance": min_variance,
    "Risk parity": risk_parity,
    "Max Sharpe": max_sharpe,
    "HRP": hrp,
}
BENCHMARKS = {
    "SPY buy & hold": fixed({"SPY": 1.0}),
    "60/40": fixed({"SPY": 0.6, "IEF": 0.4}),
}
BENCHMARK_TICKERS = ["SPY", "IEF"]
