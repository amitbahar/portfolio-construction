"""Run the default configuration on the exported CSV exactly as the browser will, and sanity-check it.

    python -m pipeline.smoke_test _site/data/prices.csv
"""
import json
import sys

import pandas as pd

from engine.api import DEFAULT_CONFIG, prepare, run_all


def main(path):
    p = pd.read_csv(path, index_col=0, parse_dates=True)
    rets, rf = prepare(p.drop(columns="_rf"), p["_rf"])
    out = run_all({**DEFAULT_CONFIG, "n_boot": 200}, rets, rf)
    json.dumps(out)
    ew = out["table"]["Equal weight"]["net"]
    assert out["period"]["years"] > 15, out["period"]
    assert -0.05 < ew["ann_return"] < 0.25 and 0.03 < ew["ann_vol"] < 0.4, ew
    print(f"smoke test ok: {out['period']['start']} to {out['period']['end']}, "
          f"EW {ew['ann_return']:.1%} / Sharpe {ew['sharpe']:.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
