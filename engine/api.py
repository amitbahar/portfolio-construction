"""Single entry point: a config dict in, a JSON-serializable results dict out.

Used by the browser (via Pyodide), the research scripts and the tests.
"""
import math

import numpy as np
import pandas as pd

from .simulate import net_returns, run
from .stats import (adjusted_ci, block_bootstrap_vs, deflated_sharpe, drawdown, metrics, sharpe_moments,
                    vol_matched)
from .strategies import BENCHMARK_TICKERS, BENCHMARKS, STRATEGIES

DEFAULT_CONFIG = {
    "assets": ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "GLD", "VNQ"],
    "strategies": list(STRATEGIES),
    "start": None,          # "YYYY-MM-DD": first rebalance on or after this date (history before it feeds the lookback)
    "end": None,            # "YYYY-MM-DD": last day of the simulation
    "lookback": 252,
    "freq": "ME",
    "cost_bps": 10.0,
    "cap": 0.40,
    "band": 0.0,            # no-trade band: skip a rebalance unless some weight is this far from its new target
    "n_boot": 1000,
    "block": 21,
    "seed": 0,
}
LOOKBACKS = (126, 252, 504)
FREQS = {"ME": "monthly", "QE": "quarterly"}
COST_GRID = (0, 5, 10, 25)
MAX_BAND = 0.25
MIN_YEARS = 3
STRESS_PERIODS = [
    ("2008-09 financial crisis", "2008-01-01", "2009-12-31"),
    ("2020 COVID crash", "2020-01-01", "2020-12-31"),
    ("2022 rate shock", "2022-01-01", "2022-12-31"),
]


def prepare(prices, rf_annual, calendar="SPY"):
    """Daily returns and daily risk-free return from wide adjusted prices and the annual T-bill yield.

    Everything runs on the trading calendar of `calendar` (the US market): a move on a day only a
    foreign exchange trades rolls into the next US trading day.

    Missing prices inside a ticker's history (another exchange's holidays, data gaps) are
    forward-filled for up to 10 days, so a gap shows up as zero returns followed by the catch-up
    move instead of losing the move entirely.
    """
    prices = prices.sort_index()
    filled = prices.ffill(limit=10).where(prices.bfill().notna())  # never fill before a ticker's first price
    if calendar in prices.columns:
        filled = filled[prices[calendar].notna()]
    rets = filled.pct_change(fill_method=None)
    rf_daily = rf_annual.reindex(prices.index).ffill().shift(1).bfill() / 252  # yield known at prior close
    return rets, rf_daily


def normalize_config(cfg, available):
    """Fill defaults and validate. Raises ValueError with a message fit for the UI."""
    c = {**DEFAULT_CONFIG, **{k: v for k, v in (cfg or {}).items() if v is not None}}
    c["assets"] = sorted(set(c["assets"]))  # HRP depends on column order; make results independent of click order
    unknown = [a for a in c["assets"] if a not in available]
    if unknown:
        raise ValueError(f"Unknown tickers: {', '.join(unknown)}")
    if len(c["assets"]) < 2:
        raise ValueError("Pick at least 2 assets.")
    if c["lookback"] not in LOOKBACKS:
        raise ValueError(f"Lookback must be one of {LOOKBACKS}.")
    if c["freq"] not in FREQS:
        raise ValueError("Rebalance frequency must be monthly or quarterly.")
    c["cost_bps"] = float(c["cost_bps"])
    if not 0 <= c["cost_bps"] <= 200:
        raise ValueError("Cost must be between 0 and 200 bps.")
    c["band"] = float(c["band"])
    if not 0 <= c["band"] <= MAX_BAND:
        raise ValueError(f"The no-trade band must be between 0% and {MAX_BAND:.0%}.")
    c["cap"] = float(c["cap"])
    if c["cap"] * len(c["assets"]) < 1 - 1e-9:
        raise ValueError(f"A {c['cap']:.0%} cap is infeasible with {len(c['assets'])} assets: "
                         f"the cap must be at least {1 / len(c['assets']):.0%}.")
    c["strategies"] = ["Equal weight"] + [s for s in c["strategies"] if s in STRATEGIES and s != "Equal weight"]
    return c


def _clean(x):
    """Recursively convert to JSON-safe Python types (NaN/inf -> None)."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, pd.Timestamp):
        return x.strftime("%Y-%m-%d")
    return x


def _setup(cfg, rets_all, rf_daily, exchanges):
    """Validated config plus the returns and risk-free rate restricted to the backtest period."""
    c = normalize_config(cfg, list(rets_all.columns))
    cols = list(dict.fromkeys(c["assets"] + BENCHMARK_TICKERS))
    rets = rets_all[cols]
    rets = rets.loc[rets.notna().all(axis=1).idxmax():]          # common start: all chosen tickers trade
    if c["end"]:
        rets = rets.loc[:c["end"]]
    rets = rets.dropna()
    venues = {(exchanges or {}).get(a, "US") for a in c["assets"]}
    c["cov_freq"] = "weekly" if len(venues) > 1 else "daily"
    return c, rets, rf_daily.reindex(rets.index), cols


def _simulate(name, rule, c, rets, rf, cols, lookback):
    assets = c["assets"] if name in STRATEGIES else cols
    return run(rule, rets, rf, assets=assets, lookback=lookback, freq=c["freq"], cap=c["cap"], start=c["start"],
               cov=c["cov_freq"], band=c["band"])


def _ann_sharpe(r, rf):
    ex = r - rf.reindex(r.index)
    return ex.mean() / ex.std() * np.sqrt(252)


def run_all(cfg, rets_all, rf_daily, trials=None, config_key=None, progress=None, exchanges=None, keep=None):
    """Run every selected strategy plus the fixed benchmarks and compute all report sections.

    trials: {trial_key: daily Sharpe} of strategy variants already tried this session, used for the
    deflated Sharpe ratio. The current run's variants are added under `config_key` and the merged
    dict is returned so the caller can keep counting.

    exchanges: {ticker: exchange}. When the chosen assets trade on more than one exchange, the
    optimizers estimate covariance from weekly returns (see covariance.shrunk_cov).

    keep: optional dict that receives the lookback and net returns of the selected strategies, so
    lookback_grid can reuse them instead of simulating the selected lookback again.
    """
    c, rets, rf, cols = _setup(cfg, rets_all, rf_daily, exchanges)
    first_common = rets.index[0]

    rules = {k: STRATEGIES[k] for k in c["strategies"]}
    rules.update(BENCHMARKS)
    res = {}
    for i, (name, rule) in enumerate(rules.items()):
        if progress:
            progress(name, i, len(rules))
        res[name] = _simulate(name, rule, c, rets, rf, cols, c["lookback"])
    period = res["Equal weight"]["gross"].index
    years = len(period) / 252
    if years < MIN_YEARS:
        raise ValueError(f"Only {years:.1f} years to simulate after the lookback; need at least {MIN_YEARS}. "
                         "Pick an earlier start, a later end, or assets with longer histories.")
    if progress:
        progress("statistics", len(rules), len(rules))

    bps = c["cost_bps"]
    net = {k: net_returns(r, bps) for k, r in res.items()}
    if keep is not None:
        keep.update(lookback=c["lookback"], net={k: net[k] for k in c["strategies"]})
    out = {
        "config": c,
        "period": {"start": period[0], "end": period[-1], "years": years, "data_start": first_common,
                   "n_days": len(period)},
        "strategies": list(rules),
        "benchmarks": list(BENCHMARKS),
    }
    out["table"] = {k: {label: metrics(r, rf, res[k]["turnover"], res[k]["targets"])
                        for label, r in (("gross", res[k]["gross"]), ("net", net[k]))}
                    for k in rules}

    # Statistical comparison with equal weight (net of costs).
    out["vs_ew"] = {k: block_bootstrap_vs(net[k], net["Equal weight"], rf, block=c["block"],
                                          n_boot=c["n_boot"], seed=c["seed"])
                    for k in rules if k != "Equal weight"}

    # Deflated Sharpe: every strategy variant run this session counts as a trial (benchmarks don't).
    moments = {k: sharpe_moments(net[k], rf) for k in rules}
    trials = dict(trials or {})
    key = config_key or "local"
    for k in c["strategies"]:
        trials[f"{key}|{k}"] = moments[k]["sr"]
    srs = list(trials.values())
    out["dsr"] = {k: deflated_sharpe(moments[k], srs) for k in rules}
    out["trials"] = trials
    out["n_trials"] = len(trials)
    # Each variant tried is one more comparison with equal weight: widen the intervals accordingly.
    for k, v in out["vs_ew"].items():
        v["d_sharpe_ci_adj"] = adjusted_ci(v["d_sharpe"], v["d_sharpe_se"], len(trials))

    # Same-risk comparison: lever/delever each strategy to equal weight's volatility.
    ew_vol = net["Equal weight"].std() * np.sqrt(252)
    out["vol_matched"] = {"target_vol": ew_vol, "rows": {k: vol_matched(net[k], rf, ew_vol) for k in rules}}

    # Regime check: first half vs second half.
    mid = len(period) // 2
    halves = {}
    for k in rules:
        for label, seg in (("first", net[k].iloc[:mid]), ("second", net[k].iloc[mid:])):
            m = metrics(seg, rf, res[k]["turnover"].loc[seg.index], res[k]["targets"])
            halves.setdefault(k, {})[label] = {"ann_return": m["ann_return"], "sharpe": m["sharpe"]}
    out["halves"] = {"split": period[mid], "rows": halves}

    # Stress periods that lie fully inside the simulated period.
    stress = []
    for label, a, b in STRESS_PERIODS:
        if pd.Timestamp(a) >= period[0] and pd.Timestamp(b) <= period[-1]:
            stress.append({"label": label, "start": a, "end": b,
                           "rows": {k: {"return": (1 + net[k].loc[a:b]).prod() - 1,
                                        "max_dd": drawdown(net[k].loc[a:b]).min()} for k in rules}})
    out["stress"] = stress

    # Cost sensitivity reuses the simulated turnover.
    grid = sorted(set(COST_GRID) | {bps})
    costs = {}
    for k in rules:
        for b in grid:
            m = metrics(net_returns(res[k], b), rf, res[k]["turnover"], res[k]["targets"])
            costs.setdefault(k, {})[str(b)] = {"ann_return": m["ann_return"], "sharpe": m["sharpe"]}
    out["costs"] = {"grid": grid, "rows": costs}

    # Chart series: weekly wealth and drawdown (drawdown keeps each week's worst day).
    wealth = pd.DataFrame({k: (1 + net[k]).cumprod() for k in rules})
    dd = pd.DataFrame({k: drawdown(net[k]) for k in rules})
    wk = wealth.resample("W-FRI").last()
    out["series"] = {"dates": [d.strftime("%Y-%m-%d") for d in wk.index],
                     "wealth": {k: wk[k].round(5).tolist() for k in rules},
                     "drawdown": {k: dd[k].resample("W-FRI").min().round(5).tolist() for k in rules}}
    out["weights"] = {}
    for k in c["strategies"]:
        w = res[k]["weights"][c["assets"]].resample("ME").last()
        out["weights"][k] = {"dates": [d.strftime("%Y-%m-%d") for d in w.index], "assets": c["assets"],
                             "values": w.round(4).values.T.tolist()}
    return _clean(out)


def lookback_grid(cfg, rets_all, rf_daily, exchanges=None, progress=None, base=None):
    """Net Sharpe of each selected strategy at every lookback in LOOKBACKS, and its difference from
    equal weight at the same lookback.

    All lookbacks are compared over the common period in which each has a full window, so the
    numbers differ from the main table when the selected lookback is shorter than the longest one.
    base: {"lookback", "net"} as filled in by run_all(keep=...), reused instead of re-simulating.
    """
    c, rets, rf, cols = _setup(cfg, rets_all, rf_daily, exchanges)
    nets = {base["lookback"]: base["net"]} if base else {}
    todo = [lb for lb in LOOKBACKS if lb not in nets]
    skipped = []
    for j, lb in enumerate(todo):
        try:
            sims = {}
            for i, k in enumerate(c["strategies"]):
                if progress:
                    progress(f"{k}, {lb}-day lookback", j * len(c["strategies"]) + i, len(todo) * len(c["strategies"]))
                sims[k] = net_returns(_simulate(k, STRATEGIES[k], c, rets, rf, cols, lb), c["cost_bps"])
            nets[lb] = sims
        except ValueError:          # not enough history for this lookback
            skipped.append(lb)
    # Longer lookbacks start later; drop them until the common period is long enough to compare.
    lbs = sorted(nets)
    while len(lbs) >= 2:
        start = max(nets[lb]["Equal weight"].index[0] for lb in lbs)
        end = min(nets[lb]["Equal weight"].index[-1] for lb in lbs)
        n_days = len(nets[lbs[0]]["Equal weight"].loc[start:end])
        if n_days / 252 >= MIN_YEARS:
            break
        skipped.append(lbs.pop())
    if len(lbs) < 2:
        return {"lookbacks": [], "note": f"There is not enough history to compare lookback windows over at least "
                                         f"{MIN_YEARS} years for this period."}
    rows = {}
    for k in c["strategies"]:
        for lb in lbs:
            sr = _ann_sharpe(nets[lb][k].loc[start:end], rf)
            ew = _ann_sharpe(nets[lb]["Equal weight"].loc[start:end], rf)
            rows.setdefault(k, {})[str(lb)] = {"sharpe": sr, "d_sharpe": sr - ew}
    return _clean({"lookbacks": lbs, "selected": c["lookback"], "skipped": sorted(skipped), "start": start, "end": end,
                   "years": n_days / 252, "rows": rows})
