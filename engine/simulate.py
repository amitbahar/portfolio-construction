"""Walk-forward simulation with drifting weights, next-day application and turnover costs."""
import numpy as np
import pandas as pd


def rebalance_dates(index, freq="ME"):
    """Last trading day of each month ("ME") or quarter ("QE") in `index`."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(s.index.to_period(freq[0])).max().values)


def first_rebalance(index, lookback, freq="ME", start=None):
    """First rebalance date with a full lookback window that is on or after `start`."""
    dates = rebalance_dates(index, freq)
    dates = dates[index.get_indexer(dates) >= lookback - 1]
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    if len(dates) == 0:
        raise ValueError("Not enough history for the chosen lookback and start date")
    return dates[0]


def run(rule, rets, rf_daily, assets=None, lookback=252, freq="ME", cap=1.0, start=None, cov="daily"):
    """Simulate a weighting rule.

    On each rebalance date d, `rule` sees only the trailing `lookback` returns of `assets` up to and
    including d; the new target applies from the next trading day. Between rebalances weights drift
    with returns. Columns of `rets` outside `assets` get zero weight.

    Returns daily gross returns, daily turnover (sum |trade|, recorded on rebalance days), drifted
    start-of-day weights, and the targets set at each rebalance.
    """
    assets = list(rets.columns) if assets is None else list(assets)
    first = first_rebalance(rets.index, lookback, freq, start)
    dates = rebalance_dates(rets.index, freq)
    dates = dates[dates >= first]
    sub = rets[assets]
    targets = {}
    for d in dates:
        hist = sub.loc[:d].tail(lookback)
        targets[d] = rule(hist, cap=cap, rf=rf_daily.loc[d] * 252, cov=cov)
    targets = pd.DataFrame(targets).T.reindex(columns=rets.columns, fill_value=0.0)

    R = rets.values
    i0 = rets.index.get_loc(dates[0])
    sim_idx = rets.index[i0 + 1:]
    reb = set(rets.index.get_indexer(dates[1:]))
    w = targets.iloc[0].values.copy()          # start invested at the first target, no entry cost
    gross = np.empty(len(sim_idx))
    turnover = np.zeros(len(sim_idx))
    held = np.empty((len(sim_idx), len(rets.columns)))
    for k, i in enumerate(range(i0 + 1, len(rets))):
        held[k] = w
        r = R[i] @ w
        gross[k] = r
        w = w * (1 + R[i]) / (1 + r)            # drift to end-of-day weights
        if i in reb:
            tgt = targets.loc[rets.index[i]].values
            turnover[k] = np.abs(tgt - w).sum()
            w = tgt.copy()
    return {
        "gross": pd.Series(gross, index=sim_idx),
        "turnover": pd.Series(turnover, index=sim_idx),
        "weights": pd.DataFrame(held, index=sim_idx, columns=rets.columns),
        "targets": targets,
    }


def net_returns(res, bps):
    """Cost = bps x turnover, charged at the rebalance close (on that day's return)."""
    return (1 + res["gross"]) * (1 - res["turnover"] * bps / 10_000) - 1
