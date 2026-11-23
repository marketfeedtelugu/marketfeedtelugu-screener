"""Forward test: paper-trade the strategy from today, on bars nobody has seen yet.

`forward start` freezes a copy of the card (and its hash) with the start time. `forward status` then
re-runs the FROZEN rules over every bar that closed after the start: each signal is filled, stopped,
targeted or timed out by the same simulator as the backtest, so nothing depends on the user running a
scan every day. Editing the card afterwards does not change a running forward test; restart it to test
the new version (and the clock starts again).

Out-of-sample by construction: none of these bars existed when the rules were written.
"""

import os
import shutil

from . import data as D
from .backtest import run_backtest, stats
from .card import normalise, CardStrategy, card_hash, read_card, card_path
from .sim import SetupError


def fdir(p, name):
    d = os.path.join(p["forward"], name)
    os.makedirs(d, exist_ok=True)
    return d


def start(p, name, restart=False):
    path = card_path(p, name)
    raw = read_card(path)
    card = normalise(raw)
    d = fdir(p, card["name"])
    meta_p = os.path.join(d, "meta.json")
    old = D.read_json(meta_p)
    if old and not restart:
        raise SetupError("A forward test of %s is already running since %s (card %s). Use --restart to start over "
                         "with the current card." % (card["name"], old["start"], old["hash"]))
    if old and restart:
        arch = os.path.join(d, "archive-%s" % old["start"].replace(":", "").replace(" ", "-"))
        os.makedirs(arch, exist_ok=True)
        for f in os.listdir(d):
            if f.endswith(".json"):
                shutil.move(os.path.join(d, f), os.path.join(arch, f))
    stamp = D.now_ist().strftime("%Y-%m-%d %H:%M")
    D.write_json(os.path.join(d, "card.frozen.json"), raw)
    if card.get("python"):
        src = os.path.join(p["strategies"], card["python"]) if not os.path.isabs(card["python"]) else card["python"]
        if os.path.exists(src):
            shutil.copy(src, os.path.join(d, os.path.basename(src)))
    meta = {"strategy": card["name"], "title": card["title"], "start": stamp, "hash": card_hash(raw),
            "timeframe": card["timeframe"], "universe": card["universe"]["index"]}
    D.write_json(meta_p, meta)
    return meta


def status(p, name, log=print):
    d = os.path.join(p["forward"], name)
    meta = D.read_json(os.path.join(d, "meta.json"))
    if not meta:
        return None
    raw = D.read_json(os.path.join(d, "card.frozen.json"))
    card = normalise(raw)
    strat = CardStrategy(card, os.path.join(d, "card.frozen.json"), d)
    from .scan import load_market
    mk = load_market(p, card, strat, None, need_extras=False, log=log)
    cur_hash = None
    try:
        cur_hash = card_hash(read_card(card_path(p, name)))
    except Exception:  # noqa: BLE001
        pass
    out = {"meta": meta, "card_changed": bool(cur_hash and cur_hash != meta["hash"]), "generated":
           D.now_ist().isoformat(timespec="minutes")}
    try:
        res, trades = run_backtest(p, card, strat, mk, date_from=meta["start"], oos_frac=0, log=lambda *a: None,
                                   min_sessions=0)
    except SetupError as e:
        out.update(sessions=0, note=str(e), trades=[], with_filters=stats([]), rules_alone=stats([]))
        D.write_json(os.path.join(d, "status.json"), out)
        return out
    f_t = [t for t in trades if t["in_funnel"]]
    rows = []
    for t in sorted(trades, key=lambda t: t["entry_date"]):
        rows.append({k: t.get(k) for k in ("symbol", "sector", "signal_date", "entry_date", "entry", "stop0", "target",
                                           "exit_date", "exit", "exit_reason", "bars", "r_net", "pnl_net", "qty",
                                           "open", "in_funnel", "funnel_why", "regime")})
    closed_f = [t for t in f_t if not t["open"]]
    out.update(sessions=res["sessions"], window=res["window"], signals=res["signals"], unfilled=res["unfilled"],
               trades=rows, with_filters=stats(closed_f), rules_alone=stats([t for t in trades if t["in_all"] and not t["open"]]),
               open_trades=len([t for t in f_t if t["open"]]),
               open_r=sum(t["r_net"] for t in f_t if t["open"]), portfolio=res["portfolio_funnel"],
               benchmark=res["benchmark"])
    out["backtest_ref"] = backtest_ref(p, name, meta["hash"])
    D.write_json(os.path.join(d, "status.json"), out)
    return out


def backtest_ref(p, name, h):
    """The latest backtest of the same card version, to compare the forward results against."""
    best = None
    root = p["backtests"]
    for x in sorted(os.listdir(root)):
        if not x.startswith(name + "-"):
            continue
        s = D.read_json(os.path.join(root, x, "summary.json"))
        if s and s.get("card_hash") == h:
            best = s
    if not best:
        return None
    w = best["with_funnel"]
    return {"window": best["window"], "all": {k: w["all"].get(k) for k in ("n", "win_rate", "expectancy_r", "profit_factor")},
            "out_of_sample": {k: w["out_of_sample"].get(k) for k in ("n", "win_rate", "expectancy_r", "profit_factor")},
            "verdict": best.get("verdict", {}).get("tone")}
