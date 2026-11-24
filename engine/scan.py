"""The scan: the user's universe through the user's filters and entry rules, down to sized trade plans.

    Universe       the index (or list) the card names
    Regime         (optional) marketfeed market-regime gate: bullish x1, sideways x0.5, bearish x0
    Sectors        (optional) only the strongest sectors by relative strength, or an RS leader
    Tradable       cap size, liquidity, price, affordability, volatility, gaps, ASM/GSM, price band
    Strength       (optional) RS rank and trend (above the 200 DMA / stage 2)
    Your filters   (optional) the card's own daily-chart rules
    Triggered      every entry rule true on the last completed bar of the card's timeframe
    Validated      stop vs ATR, reward:risk, room to resistance, extension, events, portfolio, sizing

Filters are daily facts. On an intraday chart they use the previous session's daily bar (today's daily
bar is not complete until 15:30), exactly like the backtest does.
"""

import statistics

from . import data as D
from . import timeframes as TF
from .bars import isnan, wilder_list, true_range_list, ema_list
from .card import profile_from, to_tick
from .expr import ev, truthy, series_for_chart
from .panel import Panel, fmt
from .risk import size_position
from .sim import check_signal, simulate, net_trade, LookaheadError, SetupError

CHART_BARS = {"5m": 225, "15m": 150, "1h": 175, "2h": 120, "1d": 160, "1w": 110}


def needs_market(card, strat):
    f = card["filters"]
    if f["regime"] or f["top_sectors"] or f["min_rs"]:
        return True
    from .expr import uses
    nodes = [n for _, n in getattr(strat, "when", [])] + [n for _, n in strat.filter_rules]
    return any(uses(n, {"rs_rank"}) for n in nodes)


def load_market(p, card, strat, asof=None, need_extras=True, log=print):
    u = card["universe"]
    uni = D.load_universe(p, u["index"], u["symbols"])
    stocks = [s for s in uni["stocks"] if s["symbol"] not in set(u.get("exclude") or [])]
    pool = {s["symbol"]: s for s in stocks}
    market = needs_market(card, strat)
    if market and u["index"] != "nifty500":
        try:
            for s in D.load_universe(p, "nifty500")["stocks"]:
                pool.setdefault(s["symbol"], s)
        except D.DataError:
            log("Note: the Nifty 500 list is not cached, so RS ranks and sector strength use your universe only.")
    docs, missing = {}, []
    for sym, s in pool.items():
        d = D.completed_bars(D.load_prices(p, sym), asof)
        if d and d.get("t"):
            docs[sym] = d
        elif sym in {x["symbol"] for x in stocks}:
            missing.append(sym)
    idx = {}
    for k in D.INDEX_TICKERS:
        d = D.completed_bars(D.load_prices(p, k), asof)
        if d and d.get("t"):
            idx[k] = d
    if "NIFTY500" not in idx:
        raise D.DataError("No Nifty 500 index data. Run: screener.py data --strategy %s" % card["name"])
    panel = Panel(list(pool.values()), docs, idx, min_bars=card["filters"].get("min_bars", 150))
    extras = D.load_nse_extras(p) if need_extras else None
    return {"uni": uni, "stocks": stocks, "panel": panel, "extras": extras, "missing": missing, "market": market}


def layer_list(card, strat):
    f = card["filters"]
    out = [("universe", "Universe: %s" % D.UNIVERSES[card["universe"]["index"]][1])]
    if f["top_sectors"]:
        out.append(("sector", "In a top-%d sector (or an RS leader)" % f["top_sectors"]))
    out.append(("tradable", "Tradable: liquid, affordable, not flagged"))
    if f["min_rs"] or f["trend"] != "none":
        bits = []
        if f["min_rs"]:
            bits.append("RS rank >= %s" % f["min_rs"])
        if f["trend"] != "none":
            bits.append({"above200": "above 200 DMA", "stage2": "stage-2 uptrend"}[f["trend"]])
        out.append(("strength", "Strong stocks: " + ", ".join(bits)))
    if strat.filter_rules:
        out.append(("rules", "Your daily filters (%d rule%s)" % (len(strat.filter_rules), "" if len(strat.filter_rules) == 1 else "s")))
    out.append(("triggered", "Entry rules triggered (%s chart)" % card["timeframe"]))
    out.append(("validated", "Passed validation"))
    return out


def screen_stock(panel, strat, card, profile, sym, m, extras, top):
    """Daily filters for one stock at panel date m. Returns (failed layer key or None, reason, facts)."""
    f = card["filters"]
    meta = panel.meta[sym]
    fx = panel.stock_facts(sym, m)
    if fx is None:
        return "tradable", "no daily bar on this date", None
    if fx["bars"] < f.get("min_bars", 150):
        return "tradable", "less than %d sessions of history" % f.get("min_bars", 150), fx
    if f["top_sectors"]:
        leader = not isnan(fx["rs_rank"]) and fx["rs_rank"] >= f.get("leader_rs", 95)
        if meta.get("sector") not in top and not leader:
            return "sector", "not in a leading sector", fx
    caps = f.get("caps") or []
    if meta.get("cap") and caps and meta.get("cap") not in caps:
        return "tradable", "%s-cap (excluded)" % meta.get("cap"), fx
    if meta.get("series", "EQ") != "EQ":
        return "tradable", "series %s (trade-to-trade)" % meta.get("series"), fx
    if extras:
        sv = extras.get("surveillance", {}).get(sym)
        if sv and f.get("exclude_surveillance", True):
            return "tradable", "under NSE surveillance (ASM/GSM)", fx
    if meta.get("band") in [str(b) for b in f.get("exclude_bands", [])]:
        return "tradable", "%s%% price band" % meta.get("band"), fx
    if fx["close"] < f.get("min_price", 0):
        return "tradable", "price below Rs %s" % f.get("min_price"), fx
    if isnan(fx["tv_cr"]) or fx["tv_cr"] < f.get("min_traded_value_cr", 0):
        return "tradable", "traded value under Rs %s cr a day" % f.get("min_traded_value_cr"), fx
    ceiling = profile.get("price_ceiling")
    if ceiling and fx["close"] > ceiling:
        return "tradable", "price above your Rs %s ceiling" % fmt(ceiling), fx
    cap = profile["capital"] * profile["max_position_pct"] / 100
    if fx["close"] > cap:
        return "tradable", "one share costs more than your max position (Rs %s)" % fmt(cap), fx
    if isnan(fx["atr_pct"]) or fx["atr_pct"] < f.get("atr_pct_min", 0):
        return "tradable", "too quiet: ATR under %s%% of price" % f.get("atr_pct_min"), fx
    if fx["atr_pct"] > f.get("atr_pct_max", 100):
        return "tradable", "too wild: ATR over %s%% of price" % f.get("atr_pct_max"), fx
    if fx["big_gaps_60d"] > f.get("max_big_gaps", 999):
        return "tradable", "more than %s gaps of 4%%+ in 60 sessions" % f.get("max_big_gaps"), fx
    if f["min_rs"] and (isnan(fx["rs_rank"]) or fx["rs_rank"] < f["min_rs"]):
        return "strength", "RS rank under %s" % f["min_rs"], fx
    if f["trend"] == "above200" and not (fx["close"] > fx["sma200"]):
        return "strength", "below its 200-day average", fx
    if f["trend"] == "stage2" and not (fx["close"] > fx["sma50"] > fx["sma200"]):
        return "strength", "not in a stage-2 uptrend", fx
    if strat.filter_rules:
        ok, rule = strat.passes_filters(panel.hist[sym], fx["j"])
        if not ok:
            return "rules", "fails: %s" % rule, fx
    return None, None, fx


def regime_at(panel, m, card):
    f = card["filters"]
    if not f["regime"]:
        r = panel.regime(m, dict(f, regime_risk={"bullish": 1.0, "neutral": 1.0, "bearish": 1.0}))
        r["risk_multiplier"] = 1.0
        r["gate"] = False
        return r
    r = panel.regime(m, dict(f, regime_risk=f["regime_risk"]))
    r["gate"] = True
    return r


def resistance_above(base, i, price, lookback=250):
    best = None
    for _, ph in base.bars(i).pivot_highs(5, 5, lookback):
        if ph > price * 1.005 and (best is None or ph < best):
            best = ph
    return best


def validate(cand, base, i, fx, regime, profile, card, strat, extras, positions):
    f = card["filters"]
    sig = cand["signal"]
    checks = []
    entry = sig["entry"] if sig["entry"] is not None else base.c[i]
    stop = sig["stop"]
    rps = entry - stop
    atr_l = base.cached(("atr", 14), lambda: wilder_list(true_range_list(base.h, base.l, base.c), 14))
    atr = atr_l[i]
    tfw = {"1d": "daily", "1w": "weekly"}.get(card["timeframe"], card["timeframe"])

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    if regime.get("gate"):
        if regime["risk_multiplier"] <= 0:
            add("Market regime", "REJECT", "Regime is %s: no new long trades. Cash is a position." % regime["state"])
        else:
            add("Market regime", "PASS", "%s, risk x%s" % (regime["state"].title(), regime["risk_multiplier"]))
    nxt = "tomorrow's open" if card["timeframe"] in ("1d",) else "the next bar's open"
    order = {"market": "buy at %s" % nxt, "stop": "buy-stop at Rs %s" % fmt(entry, 2),
             "limit": "buy-limit at Rs %s" % fmt(entry, 2)}.get(sig["type"], sig["type"])
    add("Entry", "PASS", "%s, valid %d bar(s)%s" % (order, sig["valid_days"], " (expires at the close)" if sig.get("same_day") else ""))
    stop_atr = rps / atr if atr and not isnan(atr) else float("nan")
    det = "Rs %s, %.1f%% below entry, %.1f x the %s ATR" % (fmt(stop, 2), rps / entry * 100, stop_atr, tfw)
    st = "PASS"
    if not isnan(stop_atr) and stop_atr < 0.5:
        st, det = "REVIEW", det + ": inside normal bar-to-bar noise, likely to be hit by chance"
    elif (not isnan(stop_atr) and stop_atr > 4) or rps / entry > 0.12:
        st, det = "REVIEW", det + ": very wide, the position will be small"
    add("Stop-loss", st, det)
    ex = strat.EXIT_RULES
    target = sig.get("target") or (to_tick(entry + ex["target_r"] * rps, "down") if ex.get("target_r") else None)
    min_rr = f.get("min_rr", 0) or 0
    res = resistance_above(base, i, entry) if f.get("check_resistance") or min_rr else None
    if f.get("check_resistance"):
        if res is None:
            add("Room to move", "PASS", "No swing high overhead in the last 250 bars")
        else:
            rr_res = (res - entry) / rps
            if min_rr and rr_res < min_rr:
                add("Room to move", "REJECT", "Next resistance Rs %s is only %.1fR away (need %.1fR)" % (fmt(res, 2), rr_res, min_rr))
            else:
                add("Room to move", "PASS", "Next resistance Rs %s, %.1fR away" % (fmt(res, 2), rr_res))
    if target:
        rr = (target - entry) / rps
        txt = "Rs %s = %.1fR" % (fmt(target, 2), rr)
        if res is not None and target > res:
            txt += " (above resistance Rs %s: expect a fight there)" % fmt(res, 2)
        add("Target", "REVIEW" if (min_rr and rr < min_rr) else "PASS", txt)
    else:
        rules = []
        x = card["exit"]
        if x.get("trail_stop"):
            rules.append("trailing stop %s" % x["trail_stop"])
        if x.get("exit_when"):
            rules.append("exit when %s" % x["exit_when"])
        if x.get("time_bars"):
            rules.append("time stop %d bars" % x["time_bars"])
        add("Target", "PASS", "No fixed target; exits by %s" % (", ".join(rules) or "the stop"))
    e20 = base.cached(("ema", 20, "close"), lambda: ema_list(base.c, 20))[i]
    if atr and not isnan(atr) and not isnan(e20):
        ext = (base.c[i] - e20) / atr
        add("Extension", "REVIEW" if ext > 3 else "PASS", "%.1f ATR from the 20 EMA%s" % (ext, ": chasing" if ext > 3 else ""))
    sym = cand["symbol"]
    if card["capital"]["product"] == "delivery":
        evs = (extras or {}).get("events", {}).get(sym, []) if extras else []
        today = base.dates[i][:10]
        soon = [e for e in evs if e["date"] >= today]
        if soon:
            add("Events", "REVIEW", "%s on %s: a gap through your stop is possible" % (soon[0]["purpose"], soon[0]["date"]))
        elif extras and extras.get("events_fetched"):
            add("Events", "PASS", "No board meeting in NSE's calendar for the next 45 days")
        else:
            add("Events", "REVIEW", "NSE calendar not loaded: check the results date yourself")
    held = positions or []
    open_risk = sum(max(0.0, (x["entry"] - x["stop"]) * x["qty"]) for x in held)
    same = [x for x in held if x.get("sector") == base.meta.get("sector")]
    if any(x["symbol"] == sym for x in held):
        add("Portfolio", "REJECT", "Already holding %s" % sym)
    elif len(held) >= profile["max_positions"]:
        add("Portfolio", "REVIEW", "Portfolio full: %d of %d positions" % (len(held), profile["max_positions"]))
    elif len(same) >= profile.get("max_per_sector", 2):
        add("Portfolio", "REVIEW", "Already %d position(s) in %s (limit %d)" % (len(same), base.meta.get("sector"),
                                                                              profile.get("max_per_sector", 2)))
    else:
        add("Portfolio", "PASS", "%d open position(s), open risk Rs %s" % (len(held), fmt(open_risk)))
    sz = size_position(entry, stop, profile, regime["risk_multiplier"], open_risk, target=target)
    if not sz["ok"]:
        add("Position size", "REJECT", sz["why"])
    else:
        add("Position size", "PASS", "%d shares = Rs %s (%.0f%% of capital), risking Rs %s; limited by %s"
            % (sz["qty"], fmt(sz["position_value"]), sz["position_pct"], fmt(sz["risk_rupees"]), sz["limited_by"]))
    if sig["type"] in ("stop", "market") and atr and not isnan(atr):
        add("Gap rule", "PASS", "If it opens above Rs %s (entry + 0.5 ATR), skip: the risk has grown" % fmt(entry + 0.5 * atr, 2))
    status = "REJECT" if any(c["status"] == "REJECT" for c in checks) else \
        "REVIEW" if any(c["status"] == "REVIEW" for c in checks) else "PASS"
    return status, checks, sz, target, res


def chart_payload(base, strat, card, i_last, plan=None, n=None, mark_signals=True, profile=None):
    """Bars, overlays and past signals for the dashboard chart."""
    n = n or CHART_BARS.get(card["timeframe"], 150)
    i0 = max(0, i_last - n + 1)
    out = {"t": base.dates[i0:i_last + 1], "o": base.o[i0:i_last + 1], "h": base.h[i0:i_last + 1],
           "l": base.l[i0:i_last + 1], "c": base.c[i0:i_last + 1], "v": base.v[i0:i_last + 1], "overlays": []}
    for label, name, args in strat.chart_overlays[:5]:
        try:
            out["overlays"].append({"label": label, "values": series_for_chart(base, name, args, i0, i_last)})
        except Exception:  # noqa: BLE001
            pass
    if plan:
        out["plan"] = dict(plan, signal_i=plan["signal_i"] - i0 if plan.get("signal_i") is not None else None)
    if mark_signals:
        marks, busy = [], -1
        for j in range(max(i0, strat.WARMUP_BARS - 1), i_last):
            if j <= busy:
                continue
            b = base.bars(j)
            try:
                sig = check_signal(strat.entry(b), b)
            except Exception:  # noqa: BLE001
                break
            if not sig:
                continue
            tr = simulate(base, j, sig, strat, (profile or {}).get("charges"), end=i_last)
            mk = {"s": j - i0}
            if tr.get("filled"):
                if profile:
                    net_trade(tr, profile)
                mk.update(e=tr["entry_i"] - i0, ep=round(tr["entry"], 2), x=tr["exit_i"] - i0, xp=round(tr["exit"], 2),
                          r=round(tr.get("r_net", tr["r_gross"]), 2), why=tr["exit_reason"], open=tr["open"],
                          st=round(tr["stop0"], 2), tg=round(tr["target"], 2) if tr.get("target") else None)
                busy = tr["exit_i"]
            else:
                mk["nofill"] = tr.get("why")
            marks.append(mk)
        out["signals"] = marks
    return out


def rules_true(strat, base, i):
    """How many of the entry rules are true now (for near-misses)."""
    if not getattr(strat, "when", None):
        return None, []
    c = strat._ctx(base)
    flags = []
    for r, n in strat.when:
        try:
            flags.append(bool(truthy(ev(n, c, i))))
        except Exception:  # noqa: BLE001
            flags.append(False)
    return sum(flags), flags


def run_scan(p, card, strat, asof=None, log=print):
    profile = profile_from(card)
    mk = load_market(p, card, strat, asof)
    panel, extras, stocks = mk["panel"], mk["extras"], mk["stocks"]
    f = card["filters"]
    tf = card["timeframe"]
    positions = [] if asof else (D.read_json(p["positions"], []) or [])
    layers = layer_list(card, strat)
    keys = [k for k, _ in layers]
    counts = {k: 0 for k in keys}
    rejects = {k: {} for k in keys}
    rejected = {}
    counts["universe"] = len(stocks)
    top_cache, screen_cache = {}, {}

    def top_at(m):
        if m not in top_cache:
            top_cache[m] = panel.top_sectors(m, f["top_sectors"]) if f["top_sectors"] else []
        return top_cache[m]

    bases, last_bars, no_bars = {}, [], []
    for s in stocks:
        sym = s["symbol"]
        if sym not in panel.hist:
            no_bars.append(sym)
            continue
        base = TF.build(p, panel, sym, tf, asof)
        if base is None or len(base.c) < 2:
            no_bars.append(sym)
            continue
        bases[sym] = base
        last_bars.append(base.dates[-1])
    if not bases:
        raise D.DataError("No %s bars for any stock in the universe. Run: screener.py data --strategy %s"
                          % (tf, card["name"]))
    latest = max(last_bars)
    stale = [sym for sym, b in bases.items() if b.dates[-1] < latest]
    ms = [b.dmap[-1] for b in bases.values() if b.dates[-1] == latest and b.dmap[-1] >= 0]
    m_ref = statistics.mode(ms) if ms else panel.M - 1
    regime = regime_at(panel, m_ref, card)
    sectors = panel.sectors(m_ref) if (f["top_sectors"] or mk["market"]) else []
    top = top_at(m_ref)
    order = keys[1:keys.index("triggered")]
    watch = []
    for sym in [s["symbol"] for s in stocks]:
        if sym not in bases:
            rejected[sym] = ("tradable", "no %s bars" % tf)
            rejects["tradable"]["no price data"] = rejects["tradable"].get("no price data", 0) + 1
            continue
        base = bases[sym]
        if base.dates[-1] < latest:
            rejected[sym] = ("tradable", "no bar on %s (last %s)" % (latest, base.dates[-1]))
            rejects["tradable"]["no bar on the latest date"] = rejects["tradable"].get("no bar on the latest date", 0) + 1
            continue
        m = base.dmap[-1]
        if m < 0:
            rejected[sym] = ("tradable", "no daily history before this bar")
            continue
        layer, why, fx = screen_stock(panel, strat, card, profile, sym, m, extras, top_at(m))
        failed_at = order.index(layer) if layer in order else len(order)
        for k in order[:failed_at]:
            counts[k] += 1
        if layer:
            bucket = why if not why.startswith("fails:") else why
            rejects[layer][bucket] = rejects[layer].get(bucket, 0) + 1
            rejected[sym] = (layer, why)
            continue
        watch.append((sym, base, fx))
    # ---- entry rules on the card's timeframe
    cands, errors, near = [], [], []
    for sym, base, fx in watch:
        i = len(base.c) - 1
        if i + 1 < strat.WARMUP_BARS:
            rejected[sym] = ("triggered", "not enough %s bars for the rules (%d of %d)" % (tf, i + 1, strat.WARMUP_BARS))
            continue
        found = None
        look = max(1, getattr(strat, "valid", None) or 1)
        for k in range(0, look):
            j = i - k
            b = base.bars(j)
            try:
                sig = check_signal(strat.entry(b), b)
            except (LookaheadError, SetupError) as e:
                errors.append("%s: %s" % (sym, e))
                break
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %s: %s" % (sym, type(e).__name__, e))
                break
            if not sig:
                continue
            if k == 0:
                found = (j, sig, "new")
                break
            # an older signal whose order is still live and has not filled
            if k < sig["valid_days"]:
                tr = simulate(base, j, sig, strat, profile["charges"], end=i)
                if not tr.get("filled") and tr.get("why") == "order did not fill" and sig["type"] != "market":
                    found = (j, sig, "pending")
                    break
        if found:
            cands.append({"symbol": sym, "base": base, "fx": fx, "j": found[0], "signal": found[1], "state": found[2]})
        else:
            n_true, flags = rules_true(strat, base, i)
            near.append((sym, base, fx, n_true, flags))
            rejected[sym] = ("triggered", "entry rules not all true on the last bar")
    trig = {c["symbol"] for c in cands}
    counts["triggered"] = len(trig)
    nt = len(watch) - len(trig)
    if nt:
        rejects["triggered"]["rules not all true on the last bar"] = nt
    out_c = []
    sec_rank = {r["sector"]: r["rank"] for r in sectors}
    for c in cands:
        base, j = c["base"], c["j"]
        status, checks, sz, target, res = validate(c, base, len(base.c) - 1, c["fx"], regime, profile, card, strat,
                                                   extras, positions)
        sig = c["signal"]
        entry = sig["entry"] if sig["entry"] is not None else base.c[j]
        meta = base.meta
        plan = {"entry": entry, "stop": sig["stop"], "target": target, "resistance": res, "signal_i": j,
                "type": sig["type"]}
        out_c.append({"symbol": c["symbol"], "name": meta.get("name"), "sector": meta.get("sector"),
                      "cap": meta.get("cap"), "status": status, "state": c["state"], "checks": checks,
                      "signal_bar": base.dates[j], "signal": {k: v for k, v in sig.items()}, "entry": entry,
                      "stop": sig["stop"], "target": target, "resistance": res, "sizing": sz,
                      "rs_rank": c["fx"]["rs_rank"], "close": base.c[-1], "sector_rank": sec_rank.get(meta.get("sector")),
                      "rr": (target - entry) / (entry - sig["stop"]) if target else None,
                      "chart": chart_payload(base, strat, card, len(base.c) - 1, plan, profile=profile)})
        if status == "REJECT":
            why = next(x for x in checks if x["status"] == "REJECT")
            rejects["validated"][why["check"]] = rejects["validated"].get(why["check"], 0) + 1
            rejected[c["symbol"]] = ("validated", "%s: %s" % (why["check"], why["detail"]))
    rank = {"PASS": 0, "REVIEW": 1, "REJECT": 2}
    out_c.sort(key=lambda x: (rank[x["status"]], -((x["signal"].get("score") or 0)), -(x["rs_rank"] if not isnan(x["rs_rank"]) else 0)))
    counts["validated"] = len({c["symbol"] for c in out_c if c["status"] != "REJECT"})
    near.sort(key=lambda x: (-(x[3] or 0), -(x[2]["rs_rank"] if not isnan(x[2]["rs_rank"]) else 0)))
    watch_rows = []
    for k, (sym, base, fx, n_true, flags) in enumerate(near):
        meta = base.meta
        row = {"symbol": sym, "name": meta.get("name"), "sector": meta.get("sector"), "cap": meta.get("cap"),
               "close": base.c[-1], "rs_rank": fx["rs_rank"], "ret_21": fx["ret_21"], "atr_pct": fx["atr_pct"],
               "tv_cr": fx["tv_cr"], "rules_true": n_true, "rules_total": len(getattr(strat, "when", []) or []),
               "flags": flags}
        if k < 40:
            row["chart"] = chart_payload(base, strat, card, len(base.c) - 1, None, profile=profile)
        watch_rows.append(row)
    layer_out = [{"key": k, "label": lab, "count": counts[k],
                  "rejects": sorted(rejects[k].items(), key=lambda x: -x[1])} for k, lab in layers]
    warnings = []
    if mk["missing"]:
        warnings.append("%d stock(s) had no daily data and were left out" % len(mk["missing"]))
    if stale:
        warnings.append("%d stock(s) have no %s bar on %s (suspended or a data gap)" % (len(stale), tf, latest))
    if extras and not extras.get("surv_fetched"):
        warnings.append("NSE surveillance lists (ASM/GSM) not loaded: those stocks were not filtered")
    if asof:
        warnings.append("As-of replay: today's index list and NSE lists are used for a past date (survivorship bias).")
    if tf in ("5m", "15m"):
        warnings.append("Free Yahoo data keeps only ~60 days of %s bars." % tf)
    warnings += errors[:10]
    return {"asof": latest, "replay": bool(asof), "bar_minutes": D.TF_MINUTES.get(tf),
            "daily_asof": panel.dates[m_ref], "generated": D.now_ist().isoformat(timespec="minutes"),
            "strategy": card["name"], "title": card["title"], "timeframe": tf,
            "universe": card["universe"]["index"], "universe_label": D.UNIVERSES[card["universe"]["index"]][1],
            "regime": regime, "sectors": sectors[:30], "top_sectors": top, "layers": layer_out,
            "candidates": out_c, "watchlist": watch_rows,
            "rejected": {k: {"layer": v[0], "reason": v[1]} for k, v in rejected.items()},
            "warnings": warnings, "profile": {k: profile[k] for k in ("capital", "risk_per_trade_pct", "max_positions",
                                                                      "max_open_risk_pct", "max_position_pct",
                                                                      "price_ceiling")}}
