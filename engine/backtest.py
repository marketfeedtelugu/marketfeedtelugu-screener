"""Backtester: does the user's setup have an edge after Indian costs, and does the funnel help?

1. Walk every stock, every day. Ask the setup entry(b) on each completed bar (no lookahead possible:
   b.close[-1] raises). One open trade per stock at a time.
2. Simulate each signal with the same simulate() the live tool uses: fills, slippage, stop, target,
   trailing, time stop, gaps.
3. Tag each signal with whether it would have passed the funnel that day (regime, eligibility,
   sector, RS, trend), so we can compare "setup alone" with "setup + funnel".
4. Report per trade in R (net of charges), split in-sample / out-of-sample, by regime and by year,
   and a portfolio simulation with the user's capital, risk per trade and position limits.

Honest limits, always printed: survivorship bias (today's index list), Yahoo data quality, daily bars
cannot tell which of stop or target came first inside a bar (we assume the stop), and any rule tweaked
after seeing results makes the out-of-sample test less meaningful.
"""

import bisect
import csv
import math
import os
import statistics
import time

from .bars import isnan, LookaheadError
from .panel import fmt
from .risk import charges
from .sim import check_signal, simulate, net_trade, SetupError


def run_backtest(p, card, strat, mk, date_from=None, date_to=None, oos_frac=0.3, log=print, label=None,
                 min_sessions=None):
    """Every stock in the card's universe, every completed bar of its timeframe in the window."""
    from . import timeframes as TF
    from .card import profile_from
    from .scan import screen_stock, regime_at
    panel = mk["panel"]
    profile = profile_from(card)
    f = card["filters"]
    tf = card["timeframe"]
    t_from = date_from or ""
    t_to = (date_to + (" 23:59" if len(date_to) == 10 else "")) if date_to else "9999"
    if f["regime"]:
        first_ok = 0
        while first_ok < panel.M and (isnan(panel.idx_sma200[first_ok]) or first_ok < 130):
            first_ok += 1
        if first_ok < panel.M:
            t_from = max(t_from, panel.dates[first_ok])
    bases = {}
    for s in mk["stocks"]:
        if s["symbol"] in panel.hist:
            b = TF.build(p, panel, s["symbol"], tf, date_to)
            if b is not None and len(b.c) > strat.WARMUP_BARS:
                bases[s["symbol"]] = b
    if not bases:
        raise SetupError("No %s bars with enough history (%d bars warm-up). Run data first." % (tf, strat.WARMUP_BARS))
    days = sorted({d[:10] for b in bases.values() for d in b.dates if t_from <= d <= t_to})
    need = min_sessions if min_sessions is not None else (20 if tf in ("5m", "15m", "1h", "2h") else 60)
    if not days or len(days) < need:
        raise SetupError("Not enough history in the window: %d sessions (need %d)" % (len(days), need or 1))
    split_day = days[int(len(days) * (1 - oos_frac))] if oos_frac else "9999"
    rcache, tcache, scache = {}, {}, {}

    def reg_at(m):
        if m not in rcache:
            rcache[m] = regime_at(panel, m, card)
        return rcache[m]

    def top_at(m):
        if m not in tcache:
            tcache[m] = panel.top_sectors(m, f["top_sectors"]) if f["top_sectors"] else []
        return tcache[m]

    trades, errors, n_signals, unfilled = [], [], 0, 0
    t0 = time.time()
    syms = sorted(bases)
    for si, sym in enumerate(syms, 1):
        h = bases[sym]
        idx = [j for j, d in enumerate(h.dates) if t_from <= d <= t_to]
        if not idx:
            continue
        j_lo, j_hi = max(idx[0], strat.WARMUP_BARS - 1), idx[-1]
        busy_all = busy_f = -1
        j = j_lo
        while j < j_hi:
            if j < busy_all and j < busy_f:
                j += 1
                continue
            m = h.dmap[j]
            if m < 0:
                j += 1
                continue
            reg = reg_at(m)
            b = h.bars(j, {"regime": reg["state"]})
            try:
                sig = check_signal(strat.entry(b), b)
            except (LookaheadError, SetupError) as e:
                errors.append("%s %s: %s" % (sym, h.dates[j], e))
                if len(errors) > 20:
                    raise SetupError("The rules keep failing: " + "; ".join(errors[:3]))
                j += 1
                continue
            except Exception as e:  # noqa: BLE001
                errors.append("%s %s: %s: %s" % (sym, h.dates[j], type(e).__name__, e))
                if len(errors) > 20:
                    raise SetupError("The rules keep failing: " + "; ".join(errors[:3]))
                j += 1
                continue
            if not sig:
                j += 1
                continue
            n_signals += 1
            key = (sym, m)
            if key not in scache:
                scache[key] = screen_stock(panel, strat, card, profile, sym, m, None, top_at(m))
            layer, why, _ = scache[key]
            fpass = why is None and reg["risk_multiplier"] > 0
            if why is None and reg["risk_multiplier"] <= 0:
                why = "bearish regime"
            take_all, take_f = j >= busy_all, fpass and j >= busy_f
            if not (take_all or take_f):
                j += 1
                continue
            tr = simulate(h, j, sig, strat, profile.get("charges"), end=j_hi)
            if not tr.get("filled"):
                unfilled += 1
                j += 1
                continue
            tr.update(in_all=take_all, in_funnel=take_f, funnel_why=why, regime=reg["state"],
                      regime_mult=reg["risk_multiplier"], rs_rank=panel.rs[sym][m],
                      oos=h.dates[j][:10] >= split_day, year=h.dates[j][:4])
            net_trade(tr, profile)
            trades.append(tr)
            if take_all:
                busy_all = tr["exit_i"]
            if take_f:
                busy_f = tr["exit_i"]
            j += 1
        if si % 100 == 0:
            log("  %d / %d stocks, %d trades so far (%.0fs)" % (si, len(syms), len(trades), time.time() - t0))
    all_t = [t for t in trades if t["in_all"]]
    fun_t = [t for t in trades if t["in_funnel"]]
    m0 = max(0, bisect.bisect_left(panel.dates, days[0]))
    m1 = min(panel.M - 1, bisect.bisect_right(panel.dates, days[-1]) - 1)
    res = {
        "setup": strat.SETUP_NAME, "strategy": card["name"], "file": os.path.basename(str(strat.FILE)),
        "exit_rules": strat.EXIT_RULES, "timeframe": tf, "universe": card["universe"]["index"],
        "window": [days[0], days[-1]], "oos_from": split_day if oos_frac else None, "stocks": len(syms),
        "sessions": len(days), "signals": n_signals, "unfilled": unfilled, "errors": errors[:10],
        "setup_only": block_stats(all_t), "with_funnel": block_stats(fun_t),
        "portfolio_funnel": portfolio(panel, fun_t, profile, m0, m1, use_regime=True),
        "portfolio_setup_only": portfolio(panel, all_t, profile, m0, m1, use_regime=False),
        "benchmark": benchmark(panel, m0, m1),
        "funnel_rejects": reject_mix([t for t in all_t if not t["in_funnel"]]),
        "filters_on": filters_on(card, strat), "seconds": round(time.time() - t0, 1), "label": label,
    }
    res["verdict"] = verdict(res)
    return res, trades


def filters_on(card, strat):
    f = card["filters"]
    out = []
    if f["regime"]:
        out.append("regime gate")
    if f["top_sectors"]:
        out.append("top %d sectors" % f["top_sectors"])
    if f["min_rs"]:
        out.append("RS >= %s" % f["min_rs"])
    if f["trend"] != "none":
        out.append(f["trend"])
    out.append("tradability")
    if strat.filter_rules:
        out.append("%d daily rule(s)" % len(strat.filter_rules))
    return out


def stats(ts):
    if not ts:
        return {"n": 0}
    r = [t["r_net"] for t in ts]
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    streak = worst_streak = 0
    for t in sorted(ts, key=lambda t: t["exit_date"]):
        eq += t["r_net"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = streak + 1 if t["r_net"] <= 0 else 0
        worst_streak = max(worst_streak, streak)
    gp, gl = sum(wins), -sum(losses)
    return {"n": len(r), "win_rate": len(wins) / len(r) * 100, "avg_win_r": statistics.mean(wins) if wins else 0,
            "avg_loss_r": statistics.mean(losses) if losses else 0, "expectancy_r": statistics.mean(r),
            "median_r": statistics.median(r), "profit_factor": gp / gl if gl else float("inf"),
            "total_r": sum(r), "max_dd_r": dd, "worst_losing_streak": worst_streak,
            "avg_bars": statistics.mean(t["bars"] for t in ts),
            "gross_expectancy_r": statistics.mean(t["r_gross"] for t in ts),
            "avg_charges_r": statistics.mean(t["r_gross"] - t["r_net"] for t in ts),
            "exit_mix": mix(t["exit_reason"].split(" (")[0] for t in ts)}


def mix(items):
    out = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def block_stats(ts):
    return {"all": stats(ts), "in_sample": stats([t for t in ts if not t["oos"]]),
            "out_of_sample": stats([t for t in ts if t["oos"]]),
            "by_regime": {k: stats([t for t in ts if t["regime"] == k]) for k in ("bullish", "neutral", "bearish")},
            "by_year": {y: stats([t for t in ts if t["year"] == y]) for y in sorted({t["year"] for t in ts})}}


def reject_mix(ts):
    return mix((t["funnel_why"] or "") for t in ts)


def portfolio(panel, ts, profile, m0, m1, use_regime=True):
    """Take trades in date order with the user's limits. Sizes from equity at the previous close."""
    cap0 = profile.get("capital") or 500000
    rp, mp = profile["risk_per_trade_pct"] / 100, profile["max_positions"]
    mpos_pct, heat = profile["max_position_pct"] / 100, profile["max_open_risk_pct"] / 100
    per_sec = profile.get("max_per_sector", 2)
    by_entry = {}
    for t in ts:
        by_entry.setdefault(str(t["entry_date"])[:10], []).append(t)
    cash, equity, open_pos = cap0, cap0, []
    curve, taken, skipped = [], [], 0
    days_in = 0
    for m in range(m0, m1 + 1):
        d = panel.dates[m]
        todays = sorted(by_entry.get(d, []), key=lambda t: -((t.get("score") or 0) * 1000 + (t.get("rs_rank") or 0)))
        for t in todays:
            if len(open_pos) >= mp or any(p["t"]["symbol"] == t["symbol"] for p in open_pos):
                skipped += 1
                continue
            if sum(1 for p in open_pos if p["t"]["sector"] == t["sector"]) >= per_sec:
                skipped += 1
                continue
            mult = t["regime_mult"] if use_regime else 1.0
            open_risk = sum(p["qty"] * p["t"]["risk"] for p in open_pos)
            q = math.floor(equity * rp * mult / t["risk"])
            q = min(q, math.floor(equity * mpos_pct / t["entry"]), math.floor(cash / t["entry"]),
                    math.floor(max(0.0, equity * heat - open_risk) / t["risk"]))
            if q < 1:
                skipped += 1
                continue
            cash -= q * t["entry"]
            open_pos.append({"t": t, "qty": q})
        still = []
        for p in open_pos:
            t = p["t"]
            if str(t["exit_date"])[:10] == d:
                from .sim import trade_charges_cfg
                ch = charges(p["qty"] * t["entry"], p["qty"] * t["exit"], trade_charges_cfg(profile, t))["total"]
                cash += p["qty"] * t["exit"] - ch
                pnl = p["qty"] * (t["exit"] - t["entry"]) - ch
                taken.append({"symbol": t["symbol"], "entry_date": t["entry_date"], "exit_date": d,
                              "qty": p["qty"], "pnl": pnl, "r": pnl / (p["qty"] * t["risk"]),
                              "risk_rs": p["qty"] * t["risk"]})
            else:
                still.append(p)
        open_pos = still
        mtm = 0.0
        for p in open_pos:
            h = panel.hist[p["t"]["symbol"]]
            j = panel.jmap[p["t"]["symbol"]][m]
            if j >= 0 and str(p["t"]["entry_date"])[:10] <= d:
                p["last"] = h.c[j]
            mtm += p["qty"] * p.get("last", p["t"]["entry"])
        equity = cash + mtm
        days_in += bool(open_pos)
        curve.append((d, round(equity, 2)))
    n_days = m1 - m0 + 1
    years = n_days / 248
    final = curve[-1][1] if curve else cap0
    peak, mdd = cap0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, (e / peak - 1) * 100)
    wins = [x for x in taken if x["pnl"] > 0]
    return {"start_capital": cap0, "final_equity": final, "total_return_pct": (final / cap0 - 1) * 100,
            "cagr_pct": ((final / cap0) ** (1 / years) - 1) * 100 if years > 0 and final > 0 else float("nan"),
            "max_drawdown_pct": mdd, "trades": len(taken), "skipped_for_limits": skipped,
            "win_rate": len(wins) / len(taken) * 100 if taken else 0,
            "avg_r": statistics.mean(x["r"] for x in taken) if taken else 0,
            "avg_risk_rs": statistics.mean(x["risk_rs"] for x in taken) if taken else 0,
            "time_in_market_pct": days_in / n_days * 100, "curve": thin(curve, 400)}


def benchmark(panel, m0, m1):
    c = panel.idx_c[m0:m1 + 1]
    years = (m1 - m0 + 1) / 248
    peak, mdd = c[0], 0.0
    for x in c:
        peak = max(peak, x)
        mdd = min(mdd, (x / peak - 1) * 100)
    return {"name": "Nifty 500 (price index, no dividends)", "total_return_pct": (c[-1] / c[0] - 1) * 100,
            "cagr_pct": ((c[-1] / c[0]) ** (1 / years) - 1) * 100, "max_drawdown_pct": mdd,
            "curve": thin(list(zip(panel.dates[m0:m1 + 1], c)), 400)}


def thin(curve, n):
    if len(curve) <= n:
        return curve
    step = len(curve) / n
    out = [curve[int(i * step)] for i in range(n)]
    if out[-1] != curve[-1]:
        out.append(curve[-1])
    return out


def verdict(res):
    s = res["with_funnel"]
    a = s["all"]
    o, i = s["out_of_sample"], s["in_sample"]
    lines = []
    if a.get("n", 0) < 30:
        lines.append("Too few trades (%d with your filters) to judge anything. Widen the dates or the universe, "
                     "or loosen the rules; do not trade on this." % a.get("n", 0))
        tone = "insufficient"
    elif o.get("n", 0) < 15:
        lines.append("Only %d out-of-sample trades: the recent period cannot confirm or reject the setup." % o.get("n", 0))
        tone = "insufficient"
    elif o["expectancy_r"] <= 0:
        lines.append("No edge after costs in the out-of-sample period (%.2fR a trade over %d trades). %s"
                     % (o["expectancy_r"], o["n"], "As written, these rules lose money recently." if o["expectancy_r"] < -0.03
                        else "About breakeven: the charges eat whatever edge the rules had."))
        tone = "negative"
    elif i.get("n", 0) and i["expectancy_r"] > 0 and a["n"] >= 100:
        lines.append("Positive after costs in both periods (%.2fR in-sample, %.2fR out-of-sample, %d trades). "
                     "That is evidence, not proof: paper trade it for 1-2 months before real money."
                     % (i["expectancy_r"], o["expectancy_r"], a["n"]))
        tone = "positive"
    else:
        lines.append("Mixed: %.2fR in-sample, %.2fR out-of-sample over %d trades. Not strong enough to rely on yet."
                     % (i.get("expectancy_r", 0), o.get("expectancy_r", 0), a["n"]))
        tone = "mixed"
    so = res["setup_only"]["all"]
    if so.get("n", 0) >= 30 and a.get("n", 0) >= 30:
        diff = a["expectancy_r"] - so["expectancy_r"]
        lines.append("The filters %s the entry rules: %.2fR a trade with them vs %.2fR without (%d vs %d trades)."
                     % ("improved" if diff > 0.05 else "hurt" if diff < -0.05 else "barely changed",
                        a["expectancy_r"], so["expectancy_r"], a["n"], so["n"]))
    pf, bm = res.get("portfolio_funnel") or {}, res.get("benchmark") or {}
    pc, bc = pf.get("cagr_pct"), bm.get("cagr_pct")
    if pf.get("trades", 0) >= 30 and pc is not None and bc is not None and pc == pc and bc == bc:
        if pc < bc and tone in ("positive", "mixed"):
            lines.append("But as a portfolio with your limits it grew %.1f%% a year, less than the %.1f%% a year from "
                         "simply holding the Nifty 500 (before dividends). An edge per trade is not enough: it has "
                         "to beat holding the index to be worth the effort and the risk." % (pc, bc))
        elif pc < bc:
            lines.append("As a portfolio with your limits it grew %.1f%% a year, less than the %.1f%% a year from "
                         "simply holding the Nifty 500 (before dividends)." % (pc, bc))
        else:
            lines.append("As a portfolio with your limits it grew %.1f%% a year vs %.1f%% for holding the Nifty 500 "
                         "(before dividends)." % (pc, bc))
    br = res["setup_only"]["by_regime"]
    worst = [k for k in br if br[k].get("n", 0) >= 15 and br[k]["expectancy_r"] < 0]
    if len(worst) == 3:
        lines.append("Without filters, the rules lose money in bullish, sideways and bearish markets alike, so no "
                     "regime gate can rescue them: the entry or exit rules themselves need rethinking.")
    elif worst:
        lines.append("Without filters, the rules lose money in %s markets: that is what a regime gate is for."
                     % " and ".join({"neutral": "sideways"}.get(w, w) for w in worst))
    return {"tone": tone, "lines": lines}


def write_trades_csv(path, trades):
    cols = ["symbol", "sector", "signal_date", "entry_date", "entry", "stop0", "target", "exit_date", "exit",
            "exit_reason", "bars", "r_gross", "r_net", "qty", "charges", "pnl_net", "regime", "rs_rank",
            "in_all", "in_funnel", "funnel_why", "oos", "reason"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in sorted(trades, key=lambda t: t["entry_date"]):
            w.writerow([round(t[c], 4) if isinstance(t.get(c), float) else t.get(c, "") for c in cols])


def print_summary(res, log=print):
    def line(name, s):
        if not s.get("n"):
            return "  %-22s no trades" % name
        return ("  %-22s %4d trades  win %4.1f%%  avg win %+.2fR  avg loss %+.2fR  expectancy %+.2fR net "
                "(%+.2fR gross)  PF %.2f  max DD %.1fR" % (name, s["n"], s["win_rate"], s["avg_win_r"],
                                                           s["avg_loss_r"], s["expectancy_r"],
                                                           s["gross_expectancy_r"], s["profit_factor"], s["max_dd_r"]))
    log("Backtest: %s  |  %s chart  |  %s to %s (%d %s)  |  %d stocks, %d signals (%d orders never filled)"
        % (res["setup"], res.get("timeframe", "1d"), res["window"][0], res["window"][1], res.get("sessions", 0),
           "weeks" if res.get("timeframe") == "1w" else "sessions",
           res["stocks"], res["signals"], res["unfilled"]))
    log("Filters: %s" % ", ".join(res.get("filters_on", [])))
    log("Per trade, net of charges and slippage (1R = the risk from entry to stop):")
    log(line("Rules alone", res["setup_only"]["all"]))
    wf = res["with_funnel"]
    log(line("Rules + filters", wf["all"]))
    log(line("  in-sample", wf["in_sample"]))
    if res.get("oos_from"):
        log(line("  out-of-sample", wf["out_of_sample"]) + "   (from %s)" % res["oos_from"])
    for k in ("bullish", "neutral", "bearish"):
        log(line("Rules alone, %s" % k, res["setup_only"]["by_regime"][k]))
    pf, bm = res["portfolio_funnel"], res["benchmark"]
    log("Portfolio with your limits (rules + filters): Rs %s -> Rs %s, CAGR %.1f%%, max drawdown %.1f%%, "
        "%d trades (%+.2fR each on average, Rs %s risked a trade on average), %d skipped for limits, "
        "in the market %.0f%% of days"
        % (fmt(pf["start_capital"]), fmt(pf["final_equity"]), pf["cagr_pct"], pf["max_drawdown_pct"],
           pf["trades"], pf["avg_r"], fmt(pf["avg_risk_rs"]), pf["skipped_for_limits"], pf["time_in_market_pct"]))
    log("Nifty 500 over the same dates: CAGR %.1f%%, max drawdown %.1f%% (price index, no dividends)"
        % (bm["cagr_pct"], bm["max_drawdown_pct"]))
    log("Verdict: " + " ".join(res["verdict"]["lines"]))
