"""The original 10-ETF study, rerun on the shared engine. Writes tables and charts to research/results/.

    python -m research.case_study                     # on the latest refresh (data/portfolio.db)
    python -m research.case_study --db <snapshot.db>  # on a frozen snapshot
"""
import argparse
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from engine.api import DEFAULT_CONFIG, prepare, run_all
from engine.simulate import first_rebalance

OUT = Path(__file__).parent / "results"


def load_prices(db):
    con = sqlite3.connect(db)
    prices = (pd.read_sql("SELECT date, ticker, adj_close FROM prices", con, parse_dates=["date"])
              .pivot(index="date", columns="ticker", values="adj_close"))
    rf = pd.read_sql("SELECT date, annual_rate FROM risk_free", con, parse_dates=["date"]).set_index("date")["annual_rate"]
    return prepare(prices, rf)


def pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def tables(r):
    names = r["strategies"]
    rows = []
    for label in ("gross", "net"):
        t = pd.DataFrame({k: r["table"][k][label] for k in names}).T
        t = t.assign(**{c: t[c].map(pct) for c in ["ann_return", "ann_vol", "max_dd", "ann_turnover", "avg_max_weight"]},
                     sharpe=t["sharpe"].map(lambda x: f"{x:.2f}"))
        rows.append((f"Metrics, {label}", t))
    b = pd.DataFrame({k: {"ΔSharpe": f"{v['d_sharpe']:+.2f}",
                          "95% CI": "[{:+.2f}, {:+.2f}]".format(*v["d_sharpe_ci"]),
                          "P(not better)": f"{v['p_not_better']:.2f}",
                          "ΔAnn. return": f"{v['d_ann_return']:+.2%}",
                          "ΔMax DD": f"{v['d_max_dd']:+.1%}",
                          "ΔMax DD 95% CI": "[{:+.1%}, {:+.1%}]".format(*v["d_max_dd_ci"])}
                      for k, v in r["vs_ew"].items()}).T
    rows.append(("Difference vs equal weight (net, block bootstrap)", b))
    v = pd.DataFrame({k: {"Leverage": f"{x['leverage']:.2f}x", "Ann. return": pct(x["ann_return"]),
                          "Max DD": pct(x["max_dd"])} for k, x in r["vol_matched"]["rows"].items()}).T
    rows.append((f"Scaled to equal weight's volatility ({r['vol_matched']['target_vol']:.1%})", v))
    h = pd.DataFrame({k: {"1st half return": pct(x["first"]["ann_return"]), "1st half Sharpe": f"{x['first']['sharpe']:.2f}",
                          "2nd half return": pct(x["second"]["ann_return"]), "2nd half Sharpe": f"{x['second']['sharpe']:.2f}"}
                      for k, x in r["halves"]["rows"].items()}).T
    rows.append((f"Halves (split {r['halves']['split']})", h))
    s = pd.DataFrame({k: {f"{p['label']}": f"{p['rows'][k]['return']:.1%} (DD {p['rows'][k]['max_dd']:.1%})"
                          for p in r["stress"]} for k in names}).T
    rows.append(("Stress periods: return (max drawdown)", s))
    c = pd.DataFrame({k: {f"{b} bps": f"{x[str(b)]['ann_return']:.2%} / {x[str(b)]['sharpe']:.2f}" for b in r["costs"]["grid"]}
                      for k, x in r["costs"]["rows"].items()}).T
    rows.append(("Cost sensitivity: ann. return / Sharpe", c))
    return rows


def charts(r):
    dates = pd.to_datetime(r["series"]["dates"])
    fig, ax = plt.subplots(figsize=(11, 6))
    pd.DataFrame(r["series"]["wealth"], index=dates).plot(ax=ax, logy=True, lw=1.3)
    ax.set_title("Growth of $1, net of costs (log scale)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "cumulative_returns.png", dpi=130)
    fig, ax = plt.subplots(figsize=(11, 5))
    pd.DataFrame(r["series"]["drawdown"], index=dates).plot(ax=ax, lw=1)
    ax.set_title("Drawdown from previous peak (net)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "drawdowns.png", dpi=130)
    opt = [k for k in r["weights"] if k != "Equal weight"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True, sharey=True)
    for ax, k in zip(axes.flat, opt):
        w = r["weights"][k]
        pd.DataFrame(dict(zip(w["assets"], w["values"])), index=pd.to_datetime(w["dates"])).plot.area(
            ax=ax, lw=0, legend=False, cmap="tab10")
        ax.set_title(f"{k}: weights over time")
        ax.set_ylim(0, 1)
    axes[0, 0].legend(loc="upper left", ncol=5, fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "weights.png", dpi=130)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/portfolio.db", help="SQLite db from pipeline.refresh")
    ap.add_argument("--n-boot", type=int, default=2000)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    rets, rf = load_prices(a.db)
    cfg = {**DEFAULT_CONFIG, "n_boot": a.n_boot}
    r = run_all(cfg, rets, rf)
    p = r["period"]
    md = [f"# Backtest results\n\nPeriod {p['start']} to {p['end']} ({p['years']:.1f} years), lookback "
          f"{cfg['lookback']}d, monthly rebalance, {cfg['cost_bps']:.0f} bps costs, {cfg['cap']:.0%} cap. "
          "Sharpe is on excess return over 3-month T-bills.\n"]
    for title, t in tables(r):
        md.append(f"## {title}\n\n{t.to_markdown()}\n")

    # Design sensitivity (reported, not tuned): every variant starts on the same date.
    start = first_rebalance(rets[cfg["assets"] + ["IEF"]].dropna().index, 504).strftime("%Y-%m-%d")
    sens = {}
    for label, kw in {"252d monthly (base)": {}, "126d monthly": {"lookback": 126},
                      "504d monthly": {"lookback": 504}, "252d quarterly": {"freq": "QE"}}.items():
        v = run_all({**cfg, **kw, "start": start, "n_boot": 10}, rets, rf)
        for k in v["strategies"]:
            if k in v["weights"]:
                m = v["table"][k]["net"]
                sens.setdefault(k, {})[label] = f"{m['ann_return']:.1%} / {m['sharpe']:.2f} / {m['ann_turnover']:.0%}"
    md.append(f"## Design sensitivity from {start}: ann. return / Sharpe / turnover (net)\n\n"
              f"{pd.DataFrame(sens).T.to_markdown()}\n")
    (OUT / "report.md").write_text("\n".join(md))
    charts(r)
    print("\n".join(md))


if __name__ == "__main__":
    main()
