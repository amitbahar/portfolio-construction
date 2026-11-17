# Summary of findings (ETF phase)

*Educational project. Backtest results do not predict future performance and are not investment advice.*

**Setup.** 10 ETFs, walk-forward from 2005-12-01 to 2026-11-13 (20.9 years). Weights are recomputed each month-end using only the trailing 252 days, with Ledoit-Wolf covariance, long-only and a 40% cap. New weights apply from the next trading day, and weights drift between rebalances. Costs are 10 bps × turnover. Sharpe ratios use excess return over 3-month T-bills. Full tables are in [report.md](report.md).

## The answer to the goal question

**No. Once you measure out of sample and net of costs, no optimized method beat equal weight in a statistically meaningful way.**

| Net 10 bps | Ann. return | Vol | Sharpe | Max DD | Sharpe − EW, 95% CI |
|---|---|---|---|---|---|
| Equal weight | 8.0% | 12.9% | 0.53 | −37.4% | — |
| Min variance | 5.1% | 6.4% | 0.54 | −23.9% | [−0.29, +0.31] |
| Risk parity | 6.4% | 8.0% | 0.60 | −24.2% | [−0.13, +0.29] |
| Max Sharpe | 8.3% | 10.9% | 0.63 | −22.8% | [−0.27, +0.45] |
| HRP | 5.2% | 7.1% | 0.51 | −23.6% | [−0.34, +0.30] |
| 60/40 | 8.1% | 10.8% | 0.62 | −32.6% | [−0.05, +0.23] |
| SPY | 11.0% | 19.2% | 0.55 | −55.2% | [−0.15, +0.20] |

Every Sharpe difference against equal weight has a block-bootstrap 95% interval that includes zero. The point estimates range from −0.02 to +0.10, while the intervals are about ±0.3 wide. Twenty years of data are not enough to separate these methods by Sharpe ratio.

## What did work

- **Drawdown control, and this one is statistically significant.** Every risk-based method cut the worst drawdown from −37% to about −24%. The block-bootstrap 95% intervals for the improvement exclude zero for min variance [+2.9, +25.7 pts], risk parity [+2.1, +21.3] and HRP [+0.4, +24.6], but not for max Sharpe [−9.0, +23.6]. In 2008–09 they finished slightly positive while equal weight lost 2% and SPY lost 20%. It comes mostly from holding bonds, so it is a lower risk level rather than better risk-adjusted performance.
- **At the same risk, three methods come out ahead, but none convincingly.** Levered to equal weight's 12.9% volatility (borrowing at the T-bill rate), the annual returns would have been: max Sharpe 9.4%, 60/40 9.3%, risk parity 9.1%, equal weight 8.0%. Max Sharpe's figure rests on its fragile, high-turnover weights (see below). Risk parity gets there with stable weights and a similar worst drawdown to equal weight (−36.8% vs −37.4%). Min variance and HRP, levered up, return about the same as equal weight with deeper drawdowns. This comparison uses full-sample volatility, so it isn't a tradable strategy.
- **Risk parity was the most robust optimized method.** Its Sharpe was 0.58 to 0.61 across all lookback and rebalance-frequency variants, it had low turnover (31–67% a year), it barely reacted to costs, and its weights stayed stable (see weights.png). It came closest to "better than 1/N", but the evidence is still weak (P(ΔSharpe ≤ 0) = 0.24).

## What did not work

- **Max Sharpe looks like the winner, but it isn't reliable.** It had the highest gross Sharpe (0.67), but with about 450% annual turnover it is the only strategy that costs hurt materially. At 25 bps its Sharpe drops to 0.56, and with quarterly rebalancing it falls to 0.45. Its weights swing between corner portfolios every month. This is estimation error on expected returns in action.
- **Min variance and HRP did not beat 1/N on a risk-adjusted basis.** They gave up about 3 percentage points a year of return in exchange for half the volatility, and ended at roughly the same Sharpe. Min variance sits at the 40% cap almost all the time (IEF and LQD), so the cap drives the result more than the optimizer does.
- **Regime dependence.** In the first half (2005–2016, when bonds rallied), every optimized method had a Sharpe of about 0.8 against 0.5 for equal weight. In the second half (2016–2026, when equities were strong and 2022 hurt both stocks and bonds), they dropped to 0.27–0.45 against 0.56. Bonds offered no protection in 2022: every strategy lost 16–20%. A study that stopped in 2016 would have reached the opposite conclusion.
- **Simple benchmarks hold up.** 60/40 matched the best optimized Sharpe (0.62), with almost no turnover and no estimation error.

## Caveats

- Numbers here come from the 2026-11-15 data snapshot. Rerunning on a later refresh extends the period, so the figures move slightly.
- One asset universe and one 21-year history. The design sensitivity table only reports the variants; nothing was tuned on it.
- yfinance data was downloaded on 2026-11-15 (logged in `download_log`), and free data sources can change after the fact.
- The flat 10 bps cost model ignores market impact and bid-ask spreads that widen during stress periods.
- Max Sharpe uses raw trailing means on purpose, as a demonstration of estimation error. A shrunk return estimate would trade less.
