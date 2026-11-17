# Portfolio Construction Project

A personal research project: build a walk-forward backtest that compares portfolio construction methods on a set of ETFs, using SQL for the data layer and pandas for analysis. Later, extend it to individual stocks.

> Educational project only. Backtest results do not predict future performance, and nothing here is investment advice.

---

## 1. Goal

Answer one question honestly: **do optimized portfolios beat simple ones once you account for estimation error, turnover, and costs, measured out of sample?**

Success is not finding the "best" method. Success is a backtest you can trust, with clear conclusions about what does and does not work.

---

## 2. Scope

**Universe (decided):** 10 liquid ETFs across asset classes.

| Ticker | Exposure |
|--------|----------|
| SPY | US large-cap equity |
| QQQ | US large-cap growth |
| IWM | US small-cap equity |
| EFA | Developed ex-US equity |
| EEM | Emerging market equity |
| TLT | Long-term US Treasuries |
| IEF | Intermediate US Treasuries |
| LQD | Investment-grade corporate bonds |
| GLD | Gold |
| VNQ | US real estate |

**Why ETFs first:** Clean histories, no survivorship bias from delisted companies, and fewer data-quality problems while you learn the pipeline.

**Data source:** `yfinance` for prices (use adjusted close). Record the download date, since free data sources can change.

**Sample period:** At least 10 years, ideally including 2008, 2020, and 2022.

---

## 3. Strategies to Compare

1. **Equal weight (1/N):** Baseline. Each ETF gets 10% and the portfolio is rebalanced back to that on schedule.
2. **Minimum variance:** Minimize portfolio volatility using a shrunk covariance matrix. Ignores expected returns.
3. **Risk parity:** Equalize each asset's share of total portfolio risk.
4. **Max Sharpe / mean-variance** (optional): Sensitive to return estimates. Useful for showing estimation error in action.
5. **Hierarchical Risk Parity (HRP)** (stretch goal): Cluster by correlation and allocate recursively, without inverting the covariance matrix.

Benchmarks: **buy-and-hold SPY** and a **60/40 portfolio** (60% SPY, 40% IEF).

---

## 4. Methodology

### 4.1 Data layer (SQL)

Table `prices`: `date`, `ticker`, `adj_close`. Compute daily returns with a window function:

```sql
SELECT date, ticker,
       adj_close / LAG(adj_close) OVER (PARTITION BY ticker ORDER BY date) - 1 AS ret
FROM prices;
```

Data-quality checks before any analysis:
- Missing trading days or unexpected gaps
- Extreme daily returns (investigate them; do not silently drop them)
- Tickers with different first available dates (align everything to a common start date)

### 4.2 Estimation

- **Lookback window:** 252 trading days (about one year). Test 126 and 504 later as a sensitivity check.
- **Covariance:** Shrinkage estimator (Ledoit-Wolf via `sklearn.covariance.LedoitWolf`). The raw sample covariance matrix is noisy.
- **Expected returns:** Avoid optimizing on raw historical averages. They are very noisy.

### 4.3 Rebalancing (decided: monthly)

**What it means:** Rebalancing resets the portfolio to its target weights. If the target is 10% per ETF and SPY grows to 14% of the portfolio, a rebalance sells some SPY and buys the others to return to 10%. Without rebalancing, the portfolio drifts toward whatever has performed best recently, and its risk profile changes.

**Why monthly:** It is a common, practical default. It keeps the weights close to target without generating the heavy trading that daily or weekly rebalancing would. Quarterly rebalancing is a useful sensitivity check, because it trades less and shows how much of the result depends on the schedule.

**Mechanics:**
1. On the last trading day of each month, use only data up to that date to compute target weights.
2. Trade to those weights.
3. Apply the new weights starting with the next trading day's return, which avoids look-ahead bias.

### 4.4 Constraints

- Long-only (no short positions)
- Maximum weight per asset: 40%
- Weights sum to 100%

### 4.5 Transaction costs (decided: 10 bps base case)

**What "bps" means:** A basis point is 0.01%, so 10 bps = 0.10%. On a $10,000 trade, 10 bps costs $10.

**How the model applies it:** At each rebalance, add up the absolute change in every weight (this is the turnover), then multiply by the cost rate:

```
cost = 0.0010 × sum of |new weight − old weight|
```

Example: if a rebalance moves 20% of the portfolio, the cost is 0.20 × 0.0010 = 0.02% of portfolio value.

**Why 10 bps:** It is a rough but reasonable assumption for liquid ETFs, which trade with tight spreads. Real costs depend on the broker, the ETF, and trade size, so the results will be sensitive to this number.

**Sensitivity check:** Run the full backtest at 0 bps (gross), 5 bps, 10 bps (base case), and 25 bps. If a strategy only wins at 0 bps, it is probably not worth the trading.

Report results both gross (before costs) and net (after costs).

---

## 5. Reference Code Sketch

```python
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

COST_BPS = 10          # base case from Section 4.5
LOOKBACK = 252         # trading days from Section 4.2

def min_variance_weights(cov):
    n = cov.shape[0]
    res = minimize(
        lambda w: w @ cov @ w,
        x0=np.full(n, 1 / n),
        bounds=[(0, 0.4)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1},
    )
    if not res.success:
        raise RuntimeError(f"Optimization failed: {res.message}")
    return res.x

def risk_parity_weights(cov):
    n = cov.shape[0]
    def obj(w):
        port_var = w @ cov @ w
        rc = w * (cov @ w) / port_var  # fractional risk contributions
        return ((rc - 1 / n) ** 2).sum()
    res = minimize(
        obj,
        x0=np.full(n, 1 / n),
        bounds=[(1e-4, 0.4)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1},
    )
    if not res.success:
        raise RuntimeError(f"Optimization failed: {res.message}")
    return res.x

# rets: DataFrame of daily returns, index = dates, columns = tickers
# Monthly rebalance dates: last trading day of each month
weights = {}
for d in rets.resample("ME").last().index:  # use "M" on pandas < 2.2
    hist = rets.loc[:d].tail(LOOKBACK)
    if len(hist) < LOOKBACK:
        continue
    cov = LedoitWolf().fit(hist.values).covariance_
    weights[d] = min_variance_weights(cov)

# Target weights apply from the next trading day (no look-ahead)
W = (pd.DataFrame(weights, index=rets.columns).T
       .reindex(rets.index, method="ffill")
       .shift(1)
       .dropna())

gross_ret = (W * rets.loc[W.index]).sum(axis=1)

# Turnover and costs on the days weights change
turnover = W.diff().abs().sum(axis=1).fillna(0)
cost = turnover * COST_BPS / 10_000
net_ret = gross_ret - cost
```

Check the `.shift(1)` line. Removing it is the classic way to produce a backtest that looks better than it should.

---

## 6. Evaluation

Report a table for every strategy, plus both benchmarks, gross and net of costs:

| Metric | Why it matters |
|--------|----------------|
| Annualized return | Headline performance |
| Annualized volatility | Risk level |
| Sharpe ratio | Return per unit of risk |
| Maximum drawdown | Worst peak-to-trough loss |
| Time to recover from max drawdown | Pain for the investor |
| Average annual turnover | Cost driver |
| Net return after costs | The number that matters most |
| Average largest weight | Concentration |

**Statistical significance:** Sharpe ratios have large standard errors. Use a block bootstrap (resample blocks of consecutive days, not single days) to build confidence intervals for the difference between each strategy and equal weight.

**Sub-period stress tests:** Report performance separately for 2008 to 2009, 2020, and 2022.

---

## 7. Known Pitfalls

- **Estimation error:** Optimized portfolios often look better in sample and worse out of sample. Equal weight is the benchmark to beat.
- **Concentration:** Minimum variance can concentrate in bonds or low-volatility assets. Inspect weights over time, not only summary statistics.
- **Look-ahead bias:** Any input not known on the rebalance date leaks the future into the backtest.
- **Cost sensitivity:** A strategy that trades often can look good gross and disappear net. Always check turnover.
- **Regime dependence:** One decade is one market environment. Test different start dates.
- **Overfitting:** Do not tune the lookback, rebalance frequency, or weight caps until the results look good. Fix choices in advance or hold out a test period.

---

## 8. Milestones

- [x] **M1: Data.** Prices loaded into SQL, returns view built, data-quality checks passing.
- [x] **M2: Baselines.** Equal weight and buy-and-hold SPY, verified against a manual calculation for a short period.
- [x] **M3: Minimum variance.** Monthly walk-forward loop with the one-day shift and shrinkage covariance.
- [x] **M4: Costs and metrics.** Turnover-based costs at 0, 5, 10, and 25 bps; metrics table gross and net.
- [x] **M5: Risk parity.** Implemented and compared with minimum variance.
- [x] **M6: Statistics.** Block bootstrap confidence intervals and sub-period stress tests.
- [x] **M7: Write-up.** One-page summary of findings, including what did not work.
- [ ] **Stretch:** ~~HRP~~ (done), ~~mean-variance with estimation-error analysis~~ (done), a dashboard of cumulative returns and weights.

---

## 9. Phase 2: Individual Stocks (planned)

Start only after the ETF version is complete and its conclusions are written up.

- **Survivorship bias:** Use a point-in-time list of index members or listed companies, including companies later delisted or bankrupt.
- **Corporate actions:** Handle splits, dividends, mergers, ticker changes, and delisting returns correctly.
- **Costs and liquidity:** Replace the flat 10 bps with a cost model based on volume or market cap, and filter out illiquid names.
- **Larger universe:** With many stocks, shrinkage matters even more. Add sector limits and consider HRP, which handles large correlated universes better than minimum variance.
- **Timing of fundamentals:** Use the date financial data became public, not the period it covers.

Suggested first step: a fixed group of about 30 large-cap stocks, with survivorship handled carefully, before expanding the universe.

---

## 10. Deliverables

- SQL schema and data-loading script
- Python notebook or scripts for the backtest
- Metrics table and charts (cumulative returns, drawdowns, weights over time)
- Short written conclusion, including the honest answer to the goal question in Section 1

---

## 11. Decisions Log

| Question | Decision | Reason |
|----------|----------|--------|
| ETF universe | 10 ETFs listed in Section 2 | Covers major asset classes, all liquid |
| Rebalancing | Monthly | Common default; avoids heavy trading |
| Transaction cost | 10 bps base case, tested at 0, 5, 10, 25 bps | Rough estimate for liquid ETFs; sensitivity shows how much it matters |
| Individual stocks | Phase 2, after ETF version | Adds survivorship and liquidity issues to handle first |
