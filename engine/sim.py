"""Setups and the trade simulator.

A setup is a small Python file in <workspace>/setups/ written with the user. It defines:

    NAME = "Pullback to the 20 EMA"
    entry(b)            -> None, or a dict {"stop": ..., "entry": ..., "type": "stop|market|limit",
                                            "target": ..., "valid_days": 1, "reason": "...", "score": ...}
    EXIT = {...}        optional exit rules (defaults below)
    exit(b, trade)      optional custom exit, called on every close while the trade is open

The same simulate() runs a signal forward for the backtester and for `outcome` (what happened next).
Fill and exit assumptions are deliberately conservative: if a bar touches both the stop and the target,
the stop wins; a gap through the stop fills at the open, not at the stop.
"""

import importlib.util
import math
import os

from .bars import LookaheadError, isnan, ema_list, wilder_list, true_range_list, rolling_min

DEFAULT_EXIT = {
    "target_r": 2.0,          # fixed target at 2R; None for no fixed target
    "trail": None,            # None | "ema:20" (close below -> exit next open) | "low:10" | "atr:3" | "pivot"
    "time_stop": 20,          # bars after entry; None to hold until stop/target/trail
    "breakeven_at_r": None,   # e.g. 1.0 moves the stop to the entry once price reaches +1R
}


class SetupError(Exception):
    pass


def load_setup(path):
    if not os.path.exists(path):
        raise SetupError("No setup file at %s" % path)
    name = "mfsetup_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        raise SetupError("%s does not load: %s: %s" % (os.path.basename(path), type(e).__name__, e))
    if not callable(getattr(mod, "entry", None)):
        raise SetupError("%s has no entry(b) function" % os.path.basename(path))
    ex = dict(DEFAULT_EXIT)
    ex.update(getattr(mod, "EXIT", {}) or {})
    mod.EXIT_RULES = ex
    mod.SETUP_NAME = getattr(mod, "NAME", os.path.splitext(os.path.basename(path))[0])
    mod.WARMUP_BARS = int(getattr(mod, "WARMUP", 200))
    mod.FILE = path
    return mod


def check_signal(sig, b):
    """Normalise a setup's return value. Returns a clean dict or raises SetupError."""
    if sig is None or sig is False:
        return None
    if not isinstance(sig, dict):
        raise SetupError("entry() must return None or a dict, got %r" % type(sig).__name__)
    if "stop" not in sig or sig["stop"] is None or isnan(sig["stop"]):
        raise SetupError("entry() returned a signal without a stop. Every trade needs one.")
    sig = dict(sig)
    if "valid_bars" in sig and "valid_days" not in sig:
        sig["valid_days"] = sig["valid_bars"]
    s = dict(sig)
    s["stop"] = float(s["stop"])
    if s.get("entry") is None:
        s["type"] = s.get("type", "market")
        s["entry"] = None
    else:
        s["entry"] = float(s["entry"])
        s["type"] = s.get("type", "stop")
    if s["type"] not in ("market", "stop", "limit"):
        raise SetupError("type must be market, stop or limit, got %r" % s["type"])
    ref = s["entry"] if s["entry"] is not None else b.close[0]
    if s["stop"] >= ref:
        return None   # a stop at or above the entry is not a long trade; quietly skip
    s["valid_days"] = int(s.get("valid_days", 1))
    s["reason"] = str(s.get("reason", ""))
    if s.get("target") is not None:
        s["target"] = float(s["target"])
    return s


class TradeView:
    """What a custom exit(b, trade) sees."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def r_now(self):
        return (self.close - self.entry) / self.risk if self.risk else 0.0


def simulate(hist, j, sig, setup, charges_cfg=None, end=None, ctx=None):
    """Run one signal (raised at bar j's close) forward. Returns a trade dict, or a dict with
    filled=False when the order never filled. Prices include slippage on market and stop fills."""
    slip = (charges_cfg or {}).get("slippage_pct", 0.10) / 100
    n = len(hist.c)
    end = n - 1 if end is None else min(end, n - 1)
    typ, entry, stop = sig["type"], sig["entry"], sig["stop"]
    fill_i = fill = None
    same_day = sig.get("same_day")
    for k in range(j + 1, min(j + 1 + sig["valid_days"], end + 1)):
        if same_day and hist.dates[k][:10] != hist.dates[j][:10]:
            break                                   # intraday orders expire at the close
        o, h, l = hist.o[k], hist.h[k], hist.l[k]
        if typ == "market":
            fill_i, fill = k, o
        elif typ == "stop" and h >= entry:
            fill_i, fill = k, max(o, entry)
        elif typ == "limit" and l <= entry:
            fill_i, fill = k, min(o, entry)
        if fill_i is not None:
            break
    base = {"symbol": hist.symbol, "sector": hist.meta.get("sector", ""), "signal_date": hist.dates[j],
            "signal_i": j, "order": typ, "order_price": entry, "stop0": stop, "reason": sig.get("reason", ""),
            "score": sig.get("score")}
    if fill_i is None:
        base.update(filled=False, why="order did not fill")
        return base
    if fill <= stop:
        base.update(filled=False, why="opened at or below the stop")
        return base
    if typ != "limit":
        fill = fill * (1 + slip)
    risk = fill - stop
    ex = setup.EXIT_RULES
    target = sig.get("target")
    if target is None and ex.get("target_r"):
        target = fill + ex["target_r"] * risk
    cur_stop = stop
    trail = ex.get("trail")
    tkind, targ = (trail.split(":") + [""])[:2] if trail else (None, "")
    if tkind == "ema":
        tser = hist.cached(("ema", int(targ or 20), "close"), lambda: ema_list(hist.c, int(targ or 20)))
    elif tkind == "low":
        tser = hist.cached(("lo", int(targ or 10), "low"), lambda: rolling_min(hist.l, int(targ or 10)))
    elif tkind == "atr":
        tser = hist.cached(("atr", 14), lambda: wilder_list(true_range_list(hist.h, hist.l, hist.c), 14))
    else:
        tser = None
    hi_close = fill
    hi_high = fill
    exit_next_open = None
    custom = getattr(setup, "exit", None)
    out = None
    k = fill_i
    bars = 0
    while k <= end:
        o, h, l, c = hist.o[k], hist.h[k], hist.l[k], hist.c[k]
        bars += 1
        if k > fill_i:
            if exit_next_open:
                out = (k, o * (1 - slip), exit_next_open)
                break
            if o <= cur_stop:
                out = (k, o * (1 - slip), "gap below stop")
                break
            if target is not None and o >= target:
                out = (k, o, "gap above target")
                break
        if l <= cur_stop:
            out = (k, cur_stop * (1 - slip), "stop" if cur_stop <= stop else "trailing stop")
            break
        if target is not None and h >= target:
            out = (k, target, "target")
            break
        # ---- end of bar: trailing, breakeven, time stop, custom exit
        hi_close = max(hi_close, c)
        hi_high = max(hi_high, h)
        if ex.get("breakeven_at_r") and hi_high >= fill + ex["breakeven_at_r"] * risk:
            cur_stop = max(cur_stop, fill)
        if tkind == "ema" and not isnan(tser[k]) and c < tser[k]:
            exit_next_open = "close below %s EMA" % (targ or 20)
        elif tkind == "low" and not isnan(tser[k]):
            cur_stop = max(cur_stop, tser[k])
        elif tkind == "atr" and not isnan(tser[k]):
            cur_stop = max(cur_stop, hi_close - float(targ or 3) * tser[k])
        elif tkind == "pivot":
            b = hist.bars(k, ctx)
            pl = b.last_pivot_low(3, 3)
            if not isnan(pl):
                cur_stop = max(cur_stop, pl)
        if callable(custom):
            b = hist.bars(k, ctx)
            tv = TradeView(entry=fill, stop=cur_stop, initial_stop=stop, target=target, bars_held=bars,
                           highest_close=hi_close, highest_high=hi_high, risk=risk, close=c,
                           entry_date=hist.dates[fill_i], symbol=hist.symbol)
            try:
                res = custom(b, tv)
            except LookaheadError:
                raise
            if isinstance(res, dict):
                if res.get("stop") is not None:
                    cur_stop = max(cur_stop, float(res["stop"]))
                if res.get("target") is not None:
                    target = float(res["target"])
                if res.get("exit") == "close":
                    out = (k, c * (1 - slip), res.get("reason", "custom exit"))
                    break
                if res.get("exit") == "next_open":
                    exit_next_open = res.get("reason", "custom exit")
        if getattr(setup, "intraday_product", False) and k < end and hist.dates[k + 1][:10] != hist.dates[k][:10]:
            out = (k, c * (1 - slip), "session close")      # MIS: never carried overnight, even if bars are missing
            break
        if ex.get("time_stop") and bars >= ex["time_stop"] and not exit_next_open:
            out = (k, c * (1 - slip), "time stop (%d bars)" % ex["time_stop"])
            break
        k += 1
    still_open = out is None
    if still_open:
        k = end
        out = (end, hist.c[end], "still open")
    xi, xp, why = out
    base.update(filled=True, entry_i=fill_i, entry_date=hist.dates[fill_i], entry=fill, risk=risk,
                target=target, exit_i=xi, exit_date=hist.dates[xi], exit=xp, exit_reason=why,
                bars=xi - fill_i + 1, r_gross=(xp - fill) / risk, open=still_open,
                mfe_r=(hi_high - fill) / risk)
    return base


def net_trade(tr, profile, qty=None):
    """Add rupee P&L and net R for a notional size from the profile (risk per trade, position cap)."""
    from .risk import charges
    cap = profile.get("capital") or 500000
    if qty is None:
        risk_amt = cap * profile.get("risk_per_trade_pct", 1.0) / 100
        q = math.floor(risk_amt / tr["risk"]) if tr["risk"] > 0 else 0
        q = min(q, math.floor(cap * profile.get("max_position_pct", 25) / 100 / tr["entry"]))
        qty = max(q, 1)
    buy, sell = qty * tr["entry"], qty * tr["exit"]
    ch = charges(buy, sell, trade_charges_cfg(profile, tr))["total"]
    gross = sell - buy
    tr["qty"] = qty
    tr["charges"] = ch
    tr["pnl_gross"] = gross
    tr["pnl_net"] = gross - ch
    tr["r_net"] = (gross - ch) / (qty * tr["risk"]) if tr["risk"] else 0.0
    return tr


def trade_charges_cfg(profile, tr):
    """Intraday (MIS) charges only when the card trades intraday AND the trade closed the same day."""
    ch = dict(profile.get("charges") or {})
    if ch.get("product") == "intraday" and str(tr.get("entry_date", ""))[:10] != str(tr.get("exit_date", ""))[:10]:
        ch["product"] = "delivery"
    return ch
