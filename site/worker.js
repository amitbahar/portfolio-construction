// Runs the Python engine in Pyodide off the main thread.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.8/full/pyodide.mjs";

const post = (msg) => self.postMessage(msg);
let pyodide;

async function init() {
  post({ type: "status", text: "Loading the Python runtime (about 30 MB on the first visit, then cached)…", pct: 5 });
  pyodide = await loadPyodide();
  post({ type: "status", text: "Loading numpy, pandas and scipy…", pct: 30 });
  await pyodide.loadPackage(["numpy", "pandas", "scipy"]);

  post({ type: "status", text: "Loading the engine and price data…", pct: 75 });
  const files = await (await fetch("engine/manifest.json")).json();
  pyodide.FS.mkdirTree("/home/pyodide/engine");
  await Promise.all(files.map(async (f) => {
    const src = await (await fetch(`engine/${f}`)).text();
    pyodide.FS.writeFile(`/home/pyodide/engine/${f}`, src);
  }));
  const csv = await (await fetch("data/prices.csv")).text();
  pyodide.FS.writeFile("/home/pyodide/prices.csv", csv);

  await pyodide.runPythonAsync(`
import sys, json
sys.path.insert(0, "/home/pyodide")
import pandas as pd
from engine.api import prepare, run_all

_p = pd.read_csv("/home/pyodide/prices.csv", index_col=0, parse_dates=True)
RETS, RF = prepare(_p.drop(columns="_rf"), _p["_rf"])
del _p

def _run(cfg_json, trials_json, key, progress):
    try:
        out = run_all(json.loads(cfg_json), RETS, RF, trials=json.loads(trials_json),
                      config_key=key, progress=lambda name, i, n: progress(name, i, n))
        return json.dumps(out)
    except ValueError as e:
        return json.dumps({"error": str(e)})
`);
  post({ type: "ready" });
}

self.onmessage = async (e) => {
  const { type, cfg, trials, key, id } = e.data;
  if (type !== "run") return;
  try {
    const progress = (name, i, n) => post({ type: "progress", name, i, n, id });
    const json = pyodide.globals.get("_run")(JSON.stringify(cfg), JSON.stringify(trials), key, progress);
    post({ type: "result", id, result: JSON.parse(json) });
  } catch (err) {
    post({ type: "result", id, result: { error: `Unexpected error: ${err.message.split("\n").slice(-2).join(" ")}` } });
  }
};

init().catch((err) => post({ type: "fatal", text: `Could not start the Python runtime: ${err.message}` }));
