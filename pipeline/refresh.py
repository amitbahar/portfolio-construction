"""Nightly data refresh: download -> SQLite -> quality gates -> export for the site.

ETF prices come from Yahoo Finance via yfinance; the T-bill rate comes from FRED.

Exits non-zero if a hard quality check fails, so the deploy job is skipped and the previously
published data stays live.

    python -m pipeline.refresh --db data/portfolio.db --out _site/data
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

from .universe import REQUIRED, RISK_FREE, UNIVERSE

START = "1993-01-01"
EXTREME = 0.15          # |daily return| worth listing for a human to look at
MAX_STALE_DAYS = 7      # newest price must be at most this many calendar days old
MAX_MISSING_DAYS = 20   # per ticker, trading days missing after its first price
MIN_COVERAGE = 0.9      # share of the universe that must download

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date      TEXT NOT NULL,
    ticker    TEXT NOT NULL,
    adj_close REAL NOT NULL,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS risk_free (
    date        TEXT PRIMARY KEY,
    annual_rate REAL NOT NULL  -- 3-month T-bill rate (FRED DTB3), decimal
);
CREATE TABLE IF NOT EXISTS download_log (
    downloaded_at TEXT NOT NULL,
    source        TEXT NOT NULL,
    n_tickers     INTEGER NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL
);
DROP VIEW IF EXISTS returns;
CREATE VIEW returns AS
SELECT date, ticker,
       adj_close / LAG(adj_close) OVER (PARTITION BY ticker ORDER BY date) - 1 AS ret
FROM prices;
"""


def download(tickers, retries=3):
    """Adjusted closes (dividends and splits) for `tickers`, retrying tickers that come back empty."""
    import yfinance as yf

    frames, todo = [], list(tickers)
    for attempt in range(retries):
        if not todo:
            break
        raw = yf.download(todo, start=START, auto_adjust=True, progress=False, threads=True)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(todo[0])
        got = raw.dropna(axis=1, how="all")
        frames.append(got)
        todo = [t for t in todo if t not in got.columns]
        if todo:
            print(f"retrying {todo} (attempt {attempt + 2})", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    return pd.concat(frames, axis=1) if frames else pd.DataFrame(), todo


def download_risk_free(series=RISK_FREE, retries=3):
    """Daily T-bill rate (percent) from FRED's public CSV endpoint; no API key needed."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    for attempt in range(retries):
        try:
            df = pd.read_csv(url, index_col=0, parse_dates=True, na_values=["."])
            return df[series].dropna()
        except Exception as e:  # network hiccup: retry, then let the quality gate catch a stale series
            print(f"FRED download failed ({e}); attempt {attempt + 1}/{retries}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    return pd.Series(dtype=float)


def load(con, close, rf_pct):
    import yfinance as yf

    con.executescript(SCHEMA)
    con.execute("DELETE FROM prices")
    con.execute("DELETE FROM risk_free")
    long = close.stack().dropna().rename("adj_close").reset_index()
    long.columns = ["date", "ticker", "adj_close"]
    long["date"] = long["date"].dt.strftime("%Y-%m-%d")
    long.to_sql("prices", con, if_exists="append", index=False)
    rf = pd.DataFrame({"date": rf_pct.index.strftime("%Y-%m-%d"), "annual_rate": rf_pct.values / 100})
    rf.to_sql("risk_free", con, if_exists="append", index=False)
    con.execute("INSERT INTO download_log VALUES (?, ?, ?, ?, ?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), f"Yahoo Finance via yfinance {yf.__version__}; T-bill rate from FRED {RISK_FREE}",
                 close.shape[1], long["date"].min(), long["date"].max()))
    con.commit()


def quality(con, expected, today=None):
    """Run the checks in SQL. Returns (hard failures, report for the site)."""
    today = today or dt.date.today()
    fails = []
    q = lambda sql, *p: pd.read_sql(sql, con, params=p)

    span = q("""SELECT ticker, MIN(date) AS first, MAX(date) AS last, COUNT(*) AS n
                FROM prices GROUP BY ticker ORDER BY ticker""").set_index("ticker")
    missing = [t for t in expected if t not in span.index]
    if len(span) < MIN_COVERAGE * len(expected):
        fails.append(f"only {len(span)}/{len(expected)} tickers downloaded")
    for t in REQUIRED:
        if t not in span.index:
            fails.append(f"required ticker {t} missing")

    newest = pd.Timestamp(span["last"].max()).date() if len(span) else None
    if newest is None or (today - newest).days > MAX_STALE_DAYS:
        fails.append(f"newest price {newest} is more than {MAX_STALE_DAYS} days old")
    rf_last = con.execute("SELECT MAX(date) FROM risk_free").fetchone()[0]
    if rf_last is None or (today - pd.Timestamp(rf_last).date()).days > MAX_STALE_DAYS:
        fails.append(f"risk-free series ends {rf_last}")

    # Trading days (any ticker trades) after each ticker's first date on which it has no price.
    gaps = q("""WITH cal AS (SELECT DISTINCT date FROM prices),
                     first AS (SELECT ticker, MIN(date) AS f, MAX(date) AS l FROM prices GROUP BY ticker)
                SELECT first.ticker, COUNT(*) AS missing_days
                FROM first JOIN cal ON cal.date BETWEEN first.f AND first.l
                LEFT JOIN prices p ON p.ticker = first.ticker AND p.date = cal.date
                WHERE p.date IS NULL GROUP BY first.ticker""").set_index("ticker")["missing_days"]
    for t, n in gaps.items():
        if n > MAX_MISSING_DAYS:
            fails.append(f"{t} is missing {n} trading days inside its history")

    bad = q("SELECT ticker, COUNT(*) AS n FROM prices WHERE adj_close <= 0 GROUP BY ticker")
    for _, row in bad.iterrows():
        fails.append(f"{row.ticker} has {row.n} non-positive prices")

    extreme = q("SELECT date, ticker, ret FROM returns WHERE ABS(ret) > ? ORDER BY date", EXTREME)
    stale = q("""WITH r AS (SELECT ticker, date, ret,
                                   SUM(CASE WHEN ret <> 0 THEN 1 ELSE 0 END)
                                       OVER (PARTITION BY ticker ORDER BY date) AS grp
                            FROM returns WHERE ret IS NOT NULL)
                 SELECT ticker, MAX(cnt) AS longest_flat_run FROM
                   (SELECT ticker, grp, COUNT(*) - 1 AS cnt FROM r GROUP BY ticker, grp)
                 GROUP BY ticker HAVING MAX(cnt) >= 5""")

    report = {
        "missing_tickers": missing,
        "missing_days": {t: int(n) for t, n in gaps.items()},
        "extreme_returns": [{"date": r.date, "ticker": r.ticker, "ret": round(r.ret, 4)}
                            for r in extreme.itertuples()],
        "flat_runs": {r.ticker: int(r.longest_flat_run) for r in stale.itertuples()},
        "hard_failures": fails,
    }
    return fails, report, span


def export(con, out, report, span):
    out.mkdir(parents=True, exist_ok=True)
    prices = (pd.read_sql("SELECT date, ticker, adj_close FROM prices", con)
              .pivot(index="date", columns="ticker", values="adj_close"))
    rf = pd.read_sql("SELECT date, annual_rate FROM risk_free", con).set_index("date")["annual_rate"]
    prices.insert(0, "_rf", rf.reindex(prices.index))
    prices.to_csv(out / "prices.csv", float_format="%.8g")

    log = con.execute("SELECT * FROM download_log ORDER BY downloaded_at DESC LIMIT 1").fetchone()
    names = {t: (n, c) for t, n, c in UNIVERSE}
    meta = {
        "downloaded_at": log[0],
        "source": log[1],
        "data_through": prices.index.max(),
        "assets": [{"ticker": t, "name": names[t][0], "category": names[t][1],
                    "first": span.loc[t, "first"], "last": span.loc[t, "last"]}
                   for t, _, _ in UNIVERSE if t in span.index],
        "quality": report,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"exported {prices.shape[1] - 1} tickers x {len(prices)} days to {out}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/portfolio.db")
    ap.add_argument("--out", default="_site/data")
    ap.add_argument("--skip-download", action="store_true", help="re-run checks/export on the existing db")
    a = ap.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(a.db)
    expected = [t for t, _, _ in UNIVERSE]

    if not a.skip_download:
        close, failed = download(expected)
        if failed:
            print(f"WARNING: no data for {failed}", file=sys.stderr)
        load(con, close, download_risk_free())

    fails, report, span = quality(con, expected)
    print(json.dumps({k: v for k, v in report.items() if k != "extreme_returns"}, indent=1))
    print(f"{len(report['extreme_returns'])} daily returns beyond ±{EXTREME:.0%} listed in meta.json")
    if fails:
        print("QUALITY GATE FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    export(con, Path(a.out), report, span)
    return 0


if __name__ == "__main__":
    sys.exit(main())
