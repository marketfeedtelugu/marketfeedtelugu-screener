#!/usr/bin/env python3
"""marketfeed screener: your universe, your filters, your entry and exit rules -> trade plans on a chart.

    screener.py status                                  what is set up, what is missing
    screener.py universes                               every universe you can screen
    screener.py rules                                   the rule vocabulary (indicators, candles, helpers)
    screener.py card list | new NAME [--from EXAMPLE] | show NAME | check NAME
    screener.py data --strategy NAME [--years 5]        the bars this card needs (daily, weekly, 1h, 15m, 5m)
    screener.py scan --strategy NAME [--asof DATE]      the screener + news check -> runs/NAME/<date>/dashboard.html
    screener.py news --strategy NAME [--note SYM "text"]   re-check NSE filings + headlines for the trade plans
    screener.py backtest --strategy NAME [--from D] [--to D] [--oos 0.3]
    screener.py forward start|status --strategy NAME [--restart]
    screener.py explain SYMBOL --strategy NAME          why a stock did or did not show up
    screener.py size --strategy NAME --entry X --stop Y [--target Z]
    screener.py import-bars SYMBOL --tf 5m --file kite.json     longer history from Zerodha Kite
    screener.py position add SYM --qty Q --entry E --stop S | list | stop SYM --stop S | close SYM

Workspace: $MF_SCREENER_HOME or --home (default ~/marketfeed-screener).
Education and tooling, not investment advice. It never places an order.
"""

import argparse
import json
import os
import random
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import data as D  # noqa: E402
from engine import card as K  # noqa: E402
from engine.bars import isnan  # noqa: E402
from engine.expr import vocabulary, ExprError  # noqa: E402
from engine.panel import fmt  # noqa: E402
from engine.sim import SetupError, check_signal  # noqa: E402

EXAMPLES = os.path.join(HERE, "examples", "strategies")


def home_dir(a):
    return os.path.expanduser(a.home or os.environ.get("MF_SCREENER_HOME") or "~/marketfeed-screener")


def die(msg, code=1):
    print(msg)
    sys.exit(code)


def load(p, name):
    try:
        return K.load(p, name)
    except (K.CardError, ExprError, SetupError) as e:
        die("Card problem: %s" % e)


# ---------------------------------------------------------------- setup and cards

def cmd_status(a, p):
    print("Workspace:  %s" % p["home"])
    cards = K.list_cards(p)
    print("Strategies: %s" % (", ".join(cards) or "none yet (interview the user, then card new NAME)"))
    unis = sorted(f[:-5] for f in os.listdir(p["universes"]) if f.endswith(".json"))
    print("Universes:  %s" % (", ".join(unis) or "none cached"))
    n500 = D.load_prices(p, "NIFTY500")
    print("Daily bars: %s" % ("Nifty 500 index to %s, %d price files" % (n500["t"][-1], len(os.listdir(p["prices"])))
                              if n500 else "none (run data --strategy NAME)"))
    for tf in os.listdir(p["intraday"]) if os.path.isdir(p["intraday"]) else []:
        print("Intraday:   %s, %d files" % (tf, len(os.listdir(os.path.join(p["intraday"], tf)))))
    nse = D.load_nse_extras(p)
    print("NSE lists:  surveillance %s, calendar %s" % (nse.get("surv_fetched") or "not loaded",
                                                        nse.get("events_fetched") or "not loaded"))
    for c in cards:
        m = D.read_json(os.path.join(p["forward"], c, "meta.json"))
        if m:
            print("Forward:    %s since %s" % (c, m["start"]))
    print("Examples:   %s" % ", ".join(sorted(f[:-5] for f in os.listdir(EXAMPLES) if f.endswith(".json"))))


def cmd_universes(a, p):
    print("Universes (pass the key as \"universe\" in the card):")
    for k, (_, label) in D.UNIVERSES.items():
        print("  %-17s %s" % (k, label))
    print("Custom list: \"universe\": {\"index\": \"custom\", \"symbols\": [\"RELIANCE\", \"TCS\"]}")
    print("Timeframes: %s" % ", ".join(D.TIMEFRAMES))


def cmd_rules(a, p):
    print(vocabulary())


def cmd_card(a, p):
    if a.action == "list":
        print("Your strategies: %s" % (", ".join(K.list_cards(p)) or "none yet"))
        for f in sorted(os.listdir(EXAMPLES)):
            if f.endswith(".json"):
                c = K.read_card(os.path.join(EXAMPLES, f))
                print("  example %-22s %s (%s, %s)" % (f[:-5], c.get("description", "")[:80], c.get("timeframe", "1d"),
                                                      c.get("universe") if isinstance(c.get("universe"), str) else "custom"))
        return
    if not a.name:
        die("card %s NAME" % a.action)
    if a.action == "new":
        dst = os.path.join(p["strategies"], a.name + ".json")
        if os.path.exists(dst):
            die("%s already exists; edit it instead." % dst)
        src = os.path.join(EXAMPLES, (a.src or "_template") + ".json")
        if not os.path.exists(src):
            die("No example %s. Examples: %s" % (a.src, ", ".join(f[:-5] for f in os.listdir(EXAMPLES))))
        c = K.read_card(src)
        c["name"] = a.name
        D.write_json(dst, c)
        print("Created %s from %s. Fill in the user's answers, then: card check %s" % (dst, os.path.basename(src), a.name))
        return
    card, strat = load(p, a.name)
    if a.action == "show":
        print(K.plain_words(card))
        print("Card version %s, warm-up %d bars" % (K.card_hash(K.read_card(K.card_path(p, a.name))), strat.WARMUP_BARS))
        return
    if a.action == "check":
        card_check(p, card, strat)


def card_check(p, card, strat):
    """Compile the card, read it back in plain words, and run the entry rules over ~40 liquid stocks."""
    from engine.scan import load_market
    from engine import timeframes as TF
    print(K.plain_words(card))
    print("\nCompiled OK. Warm-up %d %s bars. Exit rules: %s" % (strat.WARMUP_BARS, card["timeframe"], strat.EXIT_RULES))
    try:
        mk = load_market(p, card, strat, None, need_extras=False, log=print)
    except D.DataError as e:
        print("No data yet (%s). The rules compiled; run data --strategy %s, then check again for signal counts."
              % (e, card["name"]))
        return
    panel = mk["panel"]
    syms = [s["symbol"] for s in mk["stocks"] if s["symbol"] in panel.hist]
    syms.sort(key=lambda s: -tv(panel.hist[s]))
    syms = syms[:120]
    random.Random(7).shuffle(syms)
    syms = syms[:40]
    n_bars = n_sig = 0
    errs, sample, filt_fail = [], [], 0
    span = {"1d": 500, "1w": 104}.get(card["timeframe"], 100000)
    for s in syms:
        base = TF.build(p, panel, s, card["timeframe"])
        if base is None:
            continue
        for j in range(max(strat.WARMUP_BARS - 1, len(base.c) - span), len(base.c)):
            n_bars += 1
            b = base.bars(j)
            try:
                sig = check_signal(strat.entry(b), b)
            except Exception as e:  # noqa: BLE001
                errs.append("%s %s: %s: %s" % (s, base.dates[j], type(e).__name__, e))
                break
            if sig:
                n_sig += 1
                if len(sample) < 5:
                    sample.append("%s %s entry %s stop %s%s" % (s, base.dates[j], fmt(sig["entry"] or base.c[j], 2),
                                                               fmt(sig["stop"], 2),
                                                               (" target %s" % fmt(sig["target"], 2)) if sig.get("target") else ""))
    if n_bars == 0:
        print("No %s bars yet for these stocks. Run data --strategy %s." % (card["timeframe"], card["name"]))
        return
    rate = n_sig / n_bars * 100
    print("Entry rules on %d liquid stocks, %d %s bars: %d signals (%.2f%% of bars)"
          % (len(syms), n_bars, card["timeframe"], n_sig, rate))
    for x in sample:
        print("  e.g. " + x)
    if errs:
        print("ERRORS (%d):" % len(errs))
        for e in errs[:5]:
            print("  " + e)
        sys.exit(1)
    if n_sig == 0:
        print("It never fired. A rule may be too strict, or two rules can never be true together.")
    elif rate > 5:
        print("It fires on more than 5% of bars: probably too loose to mean anything.")
    else:
        print("OK. Next: backtest --strategy %s" % card["name"])


def tv(h):
    n = min(20, len(h.c))
    return sum(h.c[-k] * h.v[-k] for k in range(1, n + 1)) / n if n else 0


# ---------------------------------------------------------------- data

def cmd_data(a, p):
    card, strat = load(p, a.strategy)
    from engine.scan import needs_market
    u = card["universe"]
    path = D.universe_file(p, u["index"], u["symbols"])
    doc = D.read_json(path)
    fresh = doc and (D.now_ist() - __import__("datetime").datetime.fromisoformat(doc["fetched"])).days < 7
    if not fresh or a.force_universe:
        print("Downloading the %s list from NSE..." % D.UNIVERSES[u["index"]][1])
        doc = D.build_universe(p, u["index"], u["symbols"])
    print("Universe %s: %d stocks" % (doc["label"], doc["count"]))
    market = needs_market(card, strat)
    syms = [s["symbol"] for s in doc["stocks"] if s["symbol"] not in set(u.get("exclude") or [])]
    if market and u["index"] != "nifty500":
        m = D.read_json(D.universe_file(p, "nifty500"))
        if not m or (D.now_ist() - __import__("datetime").datetime.fromisoformat(m["fetched"])).days >= 7:
            print("Your filters rank stocks against the market: downloading the Nifty 500 list too...")
            m = D.build_universe(p, "nifty500")
        syms = sorted(set(syms) | {s["symbol"] for s in m["stocks"]})
    keys = list(D.INDEX_TICKERS) + (list(D.SECTOR_INDEX_TICKERS.values()) if market else []) + syms
    print("Daily bars for %d symbols from Yahoo (free, unofficial, can lag)..." % len(keys))
    ok, failed = D.update_prices(p, keys, years=a.years, force=a.force)
    print("Fetched %d of %d daily charts%s." % (ok, len(keys), (" (%d failed: %s)" % (len(failed), ", ".join(list(failed)[:8]))) if failed else ""))
    filled = D.fill_missing_bars(p, keys)
    for day, n in sorted(filled.items()):
        print("Filled %s for %d charts from NSE's official end-of-day files (Yahoo had no close)." % (day, n))
    n500 = D.load_prices(p, "NIFTY500")
    if n500:
        print("Last daily bar: %s" % n500["t"][-1])
    tf = card["timeframe"]
    if tf in ("5m", "15m", "1h", "2h"):
        src = "1h" if tf in ("1h", "2h") else tf
        usyms = [s for s in [x["symbol"] for x in doc["stocks"]] if s not in set(u.get("exclude") or [])]
        print("%s bars for %d stocks (Yahoo keeps %s)..." % (src, len(usyms), "~60 days" if src != "1h" else "~2-3 years"))
        ok, failed = D.update_intraday(p, usyms, src)
        print("Fetched %d of %d %s charts%s." % (ok, len(usyms), src, (" (%d failed)" % len(failed)) if failed else ""))
        sample = D.load_intraday(p, src, usyms[0]) if usyms else None
        if sample and sample.get("t"):
            print("  %s: %s to %s, %d bars cached%s" % (usyms[0], sample["t"][0], sample["t"][-1], len(sample["t"]),
                                                       " (includes Kite import)" if sample.get("source") == "kite" else ""))
    if not a.skip_nse:
        st = D.update_nse_extras(p)
        print("NSE: %s" % ", ".join("%s %s" % kv for kv in st.items()))


def cmd_import_bars(a, p):
    try:
        n, total = D.import_bars(p, a.symbol.upper(), a.tf, a.file)
    except D.DataError as e:
        die(str(e))
    print("Imported %d %s bars for %s (%d in the cache now, marked as Kite data)." % (n, D.tf_key(a.tf), a.symbol.upper(), total))


# ---------------------------------------------------------------- scan, backtest, forward

def latest_backtest(p, name, h=None):
    best = None
    for x in sorted(os.listdir(p["backtests"])):
        if x.startswith(name + "-"):
            s = D.read_json(os.path.join(p["backtests"], x, "summary.json"))
            if s and (h is None or s.get("card_hash") == h):
                best = s
    return best


def write_outputs(p, card, raw, run, d, refresh_forward=True):
    """scan.json + dashboard.html for a run folder (also used by `news` to refresh them)."""
    from engine import dashboard
    D.write_json(os.path.join(d, "scan.json"), dashboard.clean(run))
    bt = latest_backtest(p, card["name"], run.get("card_hash"))
    fw = None
    if os.path.exists(os.path.join(p["forward"], card["name"], "meta.json")):
        if refresh_forward and not run.get("replay"):
            from engine import forward as F
            try:
                fw = F.status(p, card["name"], log=lambda *x: None)      # refresh the paper trades with the new bars
            except Exception as e:  # noqa: BLE001
                print("(forward test not refreshed: %s)" % e)
        if fw is None:
            fw = D.read_json(os.path.join(p["forward"], card["name"], "status.json"))
    html = os.path.join(d, "dashboard.html")
    dashboard.write(html, run, K.plain_words(card), raw, bt, fw)
    return html


def cmd_scan(a, p):
    from engine.scan import run_scan
    card, strat = load(p, a.strategy)
    raw = K.read_card(K.card_path(p, a.strategy))
    h = K.card_hash(raw)
    try:
        run = run_scan(p, card, strat, a.asof)
    except D.DataError as e:
        die(str(e))
    run["card_hash"] = h
    if not a.no_news and run["candidates"]:
        from engine.news import attach_news
        print("Checking NSE filings and news for the trade plans (use --no-news to skip)...")
        try:
            attach_news(run, log=lambda *x: None)
        except Exception as e:  # noqa: BLE001  news never blocks the scan
            run.setdefault("warnings", []).append("News check failed: %s" % e)
    tag = run["asof"].replace(":", "").replace(" ", "-") + ("-asof" if a.asof else "")
    d = os.path.join(p["runs"], card["name"], tag)
    os.makedirs(d, exist_ok=True)
    shutil.copy(K.card_path(p, a.strategy), os.path.join(d, "card.json"))
    html = write_outputs(p, card, raw, run, d)
    if a.json:
        print(json.dumps({k: run[k] for k in ("asof", "layers", "regime")}, default=str)[:4000])
        return
    print_scan(run)
    if run.get("news"):
        print_news(run)
    if not latest_backtest(p, card["name"], h):
        print("No backtest of this card version yet: backtest --strategy %s (its results show in the dashboard)." % card["name"])
    print("Dashboard: %s" % html)


def print_news(run):
    nw = run["news"]
    print("\nNews and catalysts (%s, up to %s): NSE filings %d days, headlines %d days"
          % ("live" if nw["live"] else "replay", nw["until"], nw["filing_days"], nw["headline_days"]))
    for sym, sn in nw["stocks"].items():
        print("  %s: %d filing(s), %d headline(s)" % (sym, len(sn["filings"]), len(sn["headlines"])))
        for f in sn["flags"][:5]:
            print("      %-5s %s" % (f["level"].upper(), f["text"][:150]))
        for f in [x for x in sn["filings"] if x["important"]][:4]:
            print("      ! %s  %s  [%s]  %s" % (f["time_ist"] or "?", f["subject"][:70], ", ".join(f["tags"]), f["link"]))
        for hd in sn["headlines"][:3]:
            print("        %s  %s (%s)" % ((hd["time_ist"] or "?")[:10], hd["title"][:100], hd.get("publisher") or ""))
        for n in sn["notes"]:
            print("      note: %s" % n)
        if nw.get("notes", {}).get(sym):
            print("      Catalyst note: %s" % nw["notes"][sym]["text"])
    for sec, hs in nw["sectors"].items():
        print("  Sector %s: %d headline(s)%s" % (sec, len(hs), "; latest: " + hs[0]["title"][:90] if hs else ""))
    for e_ in nw["errors"]:
        print("  note: " + e_)
    print("  Headlines are leads, not facts: open the filing or the story before acting.")


def latest_run_dir(p, name, tag=None):
    root = os.path.join(p["runs"], name)
    runs = sorted(x for x in os.listdir(root) if os.path.isfile(os.path.join(root, x, "scan.json"))) \
        if os.path.isdir(root) else []
    if not runs:
        die("No scan of %s yet. Run: scan --strategy %s" % (name, name))
    if tag:
        hits = [x for x in runs if x.startswith(tag)]
        if not hits:
            die("No run %s. Runs: %s" % (tag, ", ".join(runs[-8:])))
        return os.path.join(root, hits[-1])
    live = [x for x in runs if not x.endswith("-asof")]
    return os.path.join(root, (live or runs)[-1])


def cmd_news(a, p):
    """Re-check NSE filings and headlines for the latest scan (e.g. before the open), or save a catalyst note."""
    from engine.news import attach_news
    card, _ = load(p, a.strategy)
    d = latest_run_dir(p, card["name"], a.run)
    run = D.read_json(os.path.join(d, "scan.json"))
    raw = D.read_json(os.path.join(d, "card.json")) or K.read_card(K.card_path(p, a.strategy))
    if a.note:
        sym, text = a.note[0].upper(), a.note[1].strip()
        if not any(c["symbol"] == sym for c in run["candidates"]):
            die("%s is not a trade plan in %s" % (sym, os.path.basename(d)))
        run.setdefault("news", {"stocks": {}, "sectors": {}, "notes": {}, "errors": [], "live": True,
                                "until": "", "filing_days": 30, "headline_days": 14})
        run["news"].setdefault("notes", {})[sym] = {"text": text, "written": D.now_ist().isoformat(timespec="minutes")}
        print("Saved the catalyst note for %s." % sym)
    else:
        if not run["candidates"]:
            die("No trade plans in %s, so there is no news to check." % os.path.basename(d))
        syms = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else None
        print("Checking NSE filings and news for %s..." % (", ".join(syms) if syms else "the PASS / REVIEW trade plans"))
        attach_news(run, symbols=syms, log=lambda *x: None)
    html = write_outputs(p, card, raw, run, d, refresh_forward=False)
    if run.get("news") and not a.note:
        print_news(run)
    print("Dashboard: %s" % html)


def print_scan(run):
    reg = run["regime"]
    print("%s | %s chart | %s | last bar %s" % (run["title"], run["timeframe"], run["universe_label"], run["asof"]))
    if reg.get("gate"):
        print("Market: %s, risk x%s. %s." % (reg["label"].upper(), reg["risk_multiplier"], "; ".join(reg["facts"][:3])))
    else:
        print("Market (context only, regime gate off): %s." % reg["label"])
    if run["top_sectors"]:
        print("Top sectors: %s" % ", ".join(run["top_sectors"]))
    for L in run["layers"]:
        rj = ", ".join("%s %d" % (k, v) for k, v in L["rejects"][:4])
        print("  %5d  %-46s %s" % (L["count"], L["label"], ("(out: " + rj + ")") if rj else ""))
    if not run["candidates"]:
        print("No stock triggered every entry rule on the last bar. No trade is a valid outcome.")
    for c in run["candidates"]:
        sz = c["sizing"]
        print("\n  [%s] %s (%s%s) %s: entry %s, stop %s, target %s%s"
              % (c["status"], c["symbol"], c["sector"], (", RS %d" % c["rs_rank"]) if not isnan(c["rs_rank"]) else "",
                 "live order from " + c["signal_bar"] if c["state"] == "pending" else "new signal",
                 fmt(c["entry"], 2), fmt(c["stop"], 2), fmt(c["target"], 2) if c["target"] else "exit rules",
                 ("  -> %d shares, Rs %s at risk" % (sz["qty"], fmt(sz["risk_rupees"]))) if sz.get("ok") else ""))
        for ch in c["checks"]:
            if ch["status"] != "PASS" or ch["check"] == "News":
                print("      %-6s %-14s %s" % (ch["status"], ch["check"], ch["detail"]))
    near = [w for w in run["watchlist"] if w.get("rules_true") and w["rules_true"] >= max(1, w["rules_total"] - 1)]
    if near:
        print("\nNear misses (all but one entry rule true): %s" % ", ".join(w["symbol"] for w in near[:15]))
    for w in run["warnings"]:
        print("Note: " + w)
    print("Matched the rules is not a buy. Check each chart yourself; the decision is yours.")


def cmd_backtest(a, p):
    from engine.scan import load_market
    from engine.backtest import run_backtest, print_summary, write_trades_csv
    from engine import report
    card, strat = load(p, a.strategy)
    raw = K.read_card(K.card_path(p, a.strategy))
    print("Loading data...")
    mk = load_market(p, card, strat, a.to, need_extras=False)
    print("Backtesting %s on %d stocks (%s chart)..." % (card["title"], len(mk["stocks"]), card["timeframe"]))
    try:
        res, trades = run_backtest(p, card, strat, mk, getattr(a, "from"), a.to, a.oos)
    except SetupError as e:
        die(str(e))
    res["card_hash"] = K.card_hash(raw)
    res["profile"] = K.profile_from(card)
    stamp = D.now_ist().strftime("%Y%m%d-%H%M")
    d = os.path.join(p["backtests"], "%s-%s" % (card["name"], stamp))
    os.makedirs(d, exist_ok=True)
    shutil.copy(K.card_path(p, a.strategy), os.path.join(d, "card.json"))
    from engine.dashboard import clean
    D.write_json(os.path.join(d, "summary.json"), clean(res))
    write_trades_csv(os.path.join(d, "trades.csv"), trades)
    try:
        report.write_backtest_report(os.path.join(d, "report.html"), res, trades)
    except Exception as e:  # noqa: BLE001
        print("(backtest HTML report skipped: %s)" % e)
    print_summary(res)
    if card["timeframe"] in ("5m", "15m"):
        print("Short history: Yahoo keeps ~60 days of %s bars. Import Kite history (import-bars) for a real test."
              % card["timeframe"])
    print("Limits: survivorship bias (today's %s list used for the past), free data, and inside a bar the stop is "
          "assumed to come before the target." % card["universe"]["index"])
    print("Saved: %s (summary.json, trades.csv). Rescan to see it in the dashboard." % d)


def cmd_forward(a, p):
    from engine import forward as F
    if a.action == "start":
        try:
            meta = F.start(p, a.strategy, a.restart)
        except (SetupError, K.CardError) as e:
            die(str(e))
        print("Forward test of %s started %s (card version %s, frozen). Every signal on a bar that closes after now "
              "is paper-traded with the same rules. Run data, then forward status, after each session."
              % (meta["strategy"], meta["start"], meta["hash"]))
        return
    st = F.status(p, a.strategy)
    if not st:
        die("No forward test for %s. Start one: forward start --strategy %s" % (a.strategy, a.strategy))
    m = st["meta"]
    print("Forward test of %s since %s (%d sessions so far)" % (m["strategy"], m["start"], st.get("sessions", 0)))
    if st.get("card_changed"):
        print("  The card was edited after the start; this test keeps the frozen version. --restart to test the new one.")
    s = st["with_filters"]
    print("  %d signals, %d closed trades, %d open (%+.2fR open)" % (st.get("signals", 0), s.get("n", 0),
                                                                   st.get("open_trades", 0), st.get("open_r", 0)))
    if s.get("n"):
        print("  closed: win %.0f%%, expectancy %+.2fR net, total %+.1fR" % (s["win_rate"], s["expectancy_r"], s["total_r"]))
    ra = st.get("rules_alone") or {}
    if ra.get("n"):
        print("  rules alone, before your filters: %d closed, win %.0f%%, %+.2fR a trade (context only)"
              % (ra["n"], ra["win_rate"], ra["expectancy_r"]))
    ref = st.get("backtest_ref")
    if ref:
        print("  backtest of the same card: %+.2fR a trade out-of-sample (%d trades), win %.0f%%"
              % (ref["out_of_sample"]["expectancy_r"] or 0, ref["out_of_sample"]["n"] or 0, ref["all"]["win_rate"] or 0))
    for t in st.get("trades", [])[-10:]:
        print("    %-12s %s in %s out %s %-18s %+.2fR%s" % (t["symbol"], t["entry_date"], fmt(t["entry"], 2),
                                                          "open" if t["open"] else t["exit_date"],
                                                          (t["exit_reason"] or "")[:18], t["r_net"] or 0,
                                                          "" if t["in_funnel"] else "  (filtered out: %s)" % t["funnel_why"]))
    if s.get("n", 0) < 30:
        print("  Fewer than 30 closed trades: too early to judge. Keep it running.")
    print("Rescan to see it in the dashboard's Forward test tab.")


def cmd_explain(a, p):
    from engine.scan import run_scan
    card, strat = load(p, a.strategy)
    run = run_scan(p, card, strat, a.asof)
    sym = a.symbol.upper()
    for c in run["candidates"]:
        if c["symbol"] == sym:
            print("%s triggered (%s) on %s:" % (sym, c["status"], c["signal_bar"]))
            for ch in c["checks"]:
                print("  %-6s %-14s %s" % (ch["status"], ch["check"], ch["detail"]))
            return
    for w in run["watchlist"]:
        if w["symbol"] == sym:
            print("%s passed every filter; entry rules on the last bar:" % sym)
            for r, f in zip(card["entry"].get("when", []), w.get("flags") or []):
                print("  %s %s" % ("TRUE " if f else "false", r))
            return
    r = run["rejected"].get(sym)
    if r:
        labels = {k: lab for k, lab in [(L["key"], L["label"]) for L in run["layers"]]}
        print("%s stopped at '%s': %s" % (sym, labels.get(r["layer"], r["layer"]), r["reason"]))
        return
    die("%s is not in the %s universe." % (sym, run["universe_label"]))


def cmd_size(a, p):
    from engine.risk import size_position
    card, _ = load(p, a.strategy)
    prof = K.profile_from(card)
    pos = D.read_json(p["positions"], []) or []
    open_risk = sum(max(0.0, (x["entry"] - x["stop"]) * x["qty"]) for x in pos)
    sz = size_position(a.entry, a.stop, prof, a.regime, open_risk, target=a.target)
    if not sz["ok"]:
        die("Cannot size this trade: %s" % sz["why"])
    print("Quantity: %d shares (limited by %s)" % (sz["qty"], sz["limited_by"]))
    print("  Position Rs %s = %.1f%% of capital | risk Rs %s (%s/share) of Rs %s allowed"
          % (fmt(sz["position_value"]), sz["position_pct"], fmt(sz["risk_rupees"]), fmt(sz["risk_per_share"], 2),
             fmt(sz["risk_amount_allowed"])))
    print("  If stopped: about Rs %s after charges" % fmt(sz["net_at_stop"]))
    if a.target:
        print("  At target (%.1fR): about Rs %s after charges (%.2fR net)" % (sz["rr"], fmt(sz["net_at_target"]), sz["net_r_at_target"]))
    print("Formula: qty = capital x risk%% x regime / (entry - stop) = %s / %s, then capped by max position and open risk."
          % (fmt(sz["risk_amount_allowed"]), fmt(sz["risk_per_share"], 2)))


def cmd_position(a, p):
    pos = D.read_json(p["positions"], []) or []
    if a.action == "list":
        if not pos:
            print("No open positions.")
        for x in pos:
            print("  %-12s %5d @ %s  stop %s  risk Rs %s  %s" % (x["symbol"], x["qty"], fmt(x["entry"], 2), fmt(x["stop"], 2),
                                                                fmt((x["entry"] - x["stop"]) * x["qty"]), x.get("sector", "")))
        return
    sym = (a.symbol or "").upper()
    if a.action == "add":
        if a.qty is None or a.entry is None or a.stop is None:
            die("position add SYMBOL --qty Q --entry E --stop S")
        sec = ""
        for f in os.listdir(p["universes"]):
            u = D.read_json(os.path.join(p["universes"], f)) or {}
            sec = next((s["sector"] for s in u.get("stocks", []) if s["symbol"] == sym), "") or sec
            if sec:
                break
        pos = [x for x in pos if x["symbol"] != sym] + [{"symbol": sym, "qty": a.qty, "entry": a.entry, "stop": a.stop,
                                                         "sector": sec, "date": D.now_ist().date().isoformat()}]
    elif a.action == "stop":
        for x in pos:
            if x["symbol"] == sym:
                x["stop"] = a.stop
    elif a.action == "close":
        pos = [x for x in pos if x["symbol"] != sym]
    D.write_json(p["positions"], pos)
    print("Positions: %d open" % len(pos))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", help="workspace folder (default $MF_SCREENER_HOME or ~/marketfeed-screener)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    sp.add_parser("universes")
    sp.add_parser("rules")
    x = sp.add_parser("card")
    x.add_argument("action", choices=["list", "new", "show", "check"])
    x.add_argument("name", nargs="?")
    x.add_argument("--from", dest="src")
    x = sp.add_parser("data")
    x.add_argument("--strategy", required=True)
    x.add_argument("--years", type=int, default=5)
    x.add_argument("--force", action="store_true", help="refetch full daily history")
    x.add_argument("--force-universe", action="store_true")
    x.add_argument("--skip-nse", action="store_true")
    x = sp.add_parser("scan")
    x.add_argument("--strategy", required=True)
    x.add_argument("--asof")
    x.add_argument("--json", action="store_true")
    x.add_argument("--no-news", action="store_true", help="skip the NSE filings and headlines check")
    x = sp.add_parser("news")
    x.add_argument("--strategy", required=True)
    x.add_argument("--run", help="a run folder (date) of that strategy; default the latest live scan")
    x.add_argument("--symbols", help="only these trade plans, e.g. A,B")
    x.add_argument("--note", nargs=2, metavar=("SYMBOL", "TEXT"), help="save your own catalyst note")
    x = sp.add_parser("backtest")
    x.add_argument("--strategy", required=True)
    x.add_argument("--from")
    x.add_argument("--to")
    x.add_argument("--oos", type=float, default=0.3)
    x = sp.add_parser("forward")
    x.add_argument("action", choices=["start", "status"])
    x.add_argument("--strategy", required=True)
    x.add_argument("--restart", action="store_true")
    x = sp.add_parser("explain")
    x.add_argument("symbol")
    x.add_argument("--strategy", required=True)
    x.add_argument("--asof")
    x = sp.add_parser("size")
    x.add_argument("--strategy", required=True)
    x.add_argument("--entry", type=float, required=True)
    x.add_argument("--stop", type=float, required=True)
    x.add_argument("--target", type=float)
    x.add_argument("--regime", type=float, default=1.0, help="risk multiplier, default 1")
    x = sp.add_parser("import-bars")
    x.add_argument("symbol")
    x.add_argument("--tf", required=True)
    x.add_argument("--file", required=True)
    x = sp.add_parser("position")
    x.add_argument("action", choices=["add", "list", "close", "stop"])
    x.add_argument("symbol", nargs="?")
    x.add_argument("--qty", type=int)
    x.add_argument("--entry", type=float)
    x.add_argument("--stop", type=float)
    a = ap.parse_args(argv)
    p = D.paths(home_dir(a))
    try:
        globals()["cmd_" + a.cmd.replace("-", "_")](a, p)
    except (D.DataError, SetupError, K.CardError, ExprError) as e:
        die(str(e))


if __name__ == "__main__":
    main()
