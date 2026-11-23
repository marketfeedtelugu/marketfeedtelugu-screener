"""Self-contained HTML reports (no external files, light and dark mode): the funnel run and the backtest."""

import html
import math

from .bars import isnan
from .panel import fmt

CSS = """
:root{--bg:#f7f6f2;--panel:#ffffff;--ink:#1d1d1b;--muted:#6b6a64;--line:#e4e2da;--accent:#1f5fbf;
--up:#13804d;--down:#c63b2f;--warn:#b7791f;--soft:#eef2f9;--bar:#1f5fbf;--bar2:#9db7e3;--bar-ink:#ffffff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121212;--panel:#1c1c1c;--ink:#ecebe6;
--muted:#a3a29b;--line:#2f2f2d;--accent:#7aa7f0;--up:#3fbf7f;--down:#ef6b5e;--warn:#e0b050;--soft:#1f2633;
--bar:#7aa7f0;--bar2:#3c5580;--bar-ink:#0d1b33}}
:root[data-theme="dark"]{--bg:#121212;--panel:#1c1c1c;--ink:#ecebe6;--muted:#a3a29b;--line:#2f2f2d;--accent:#7aa7f0;
--up:#3fbf7f;--down:#ef6b5e;--warn:#e0b050;--soft:#1f2633;--bar:#7aa7f0;--bar2:#3c5580;--bar-ink:#0d1b33}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}main{max-width:1100px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 12px}h3{font-size:16px;margin:0}
.sub{color:var(--muted);font-size:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);
text-align:right;white-space:nowrap}th{color:var(--muted);font-weight:600}td:first-child,th:first-child,
td.l,th.l{text-align:left}.wrap{overflow-x:auto}.pos{color:var(--up)}.neg{color:var(--down)}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.03em}
.b-bullish,.b-PASS,.b-positive{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}
.b-bearish,.b-REJECT,.b-negative{background:color-mix(in srgb,var(--down) 18%,transparent);color:var(--down)}
.b-neutral,.b-REVIEW,.b-mixed,.b-insufficient{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.funnel .row{display:grid;grid-template-columns:70px 1fr;gap:12px;align-items:center;margin:8px 0}
.funnel .n{font-size:22px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums}
.funnel .bar{background:var(--bar);color:var(--bar-ink);border-radius:6px;padding:7px 10px;font-weight:600;font-size:14px;
min-width:fit-content}.funnel .why{color:var(--muted);font-size:12.5px;margin-top:3px}
.checks td{text-align:left;white-space:normal;vertical-align:top}.checks td:first-child{width:80px}
.checks td:nth-child(2){width:120px;font-weight:600}svg text{fill:var(--muted);font-size:11px}
details summary{cursor:pointer;color:var(--accent)}.note{color:var(--muted);font-size:13px}
.verdict{border-left:4px solid var(--accent);padding:10px 14px;background:var(--soft);border-radius:8px}
.kpi{display:flex;flex-wrap:wrap;gap:22px}.kpi div b{display:block;font-size:22px}.kpi div span{color:var(--muted);font-size:12.5px}
footer{margin-top:40px;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""


def e(x):
    return html.escape(str(x))


def num(x, dec=1, sign=False, suffix=""):
    if x is None or (isinstance(x, float) and (isnan(x) or math.isinf(x))):
        return "n/a"
    s = ("%+." if sign else "%.") + str(dec) + "f"
    return s % x + suffix


def cls(x):
    return "" if x is None or isnan(x) else ("pos" if x > 0 else "neg" if x < 0 else "")


def page(title, body):
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>%s</title><style>%s</style></head><body><main>%s"
            "<footer>Education and tooling, not investment advice. A stock that matched the rules is not a "
            "recommendation to buy. Data: NSE index lists, Yahoo Finance daily bars (free, unofficial, can lag), "
            "NSE surveillance and board-meeting lists. Past results do not predict future results. "
            "Built with the marketfeed screener skill for Claude Code.</footer></main></body></html>"
            % (e(title), CSS, body))


# ---------------------------------------------------------------- charts

def candle_svg(ch, entry=None, stop=None, target=None, resistance=None, w=680, h=300):
    n = len(ch["c"])
    if n < 2:
        return ""
    pad_r, pad_t, vol_h = 70, 10, 50
    ph = h - vol_h - pad_t - 18
    lows = ch["l"] + [x for x in (stop,) if x]
    highs = ch["h"] + [x for x in (entry, target) if x]
    lo, hi = min(lows), max(highs)
    if resistance and resistance < hi * 1.15:
        hi = max(hi, resistance)
    span = (hi - lo) or 1
    lo -= span * 0.03
    hi += span * 0.03
    cw = (w - pad_r) / n

    def y(p):
        return pad_t + (hi - p) / (hi - lo) * ph
    out = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' aria-label='daily chart'>" % (w, h)]
    vmax = max(ch["v"]) or 1
    for i in range(n):
        o, hh, l, c, v = ch["o"][i], ch["h"][i], ch["l"][i], ch["c"][i], ch["v"][i]
        x = i * cw + cw / 2
        col = "var(--up)" if c >= o else "var(--down)"
        out.append("<line x1='%.1f' x2='%.1f' y1='%.1f' y2='%.1f' stroke='%s' stroke-width='1'/>" % (x, x, y(hh), y(l), col))
        top, bot = y(max(o, c)), y(min(o, c))
        out.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='%s'/>" % (x - cw * 0.35, top, cw * 0.7,
                                                                                     max(bot - top, 0.8), col))
        vh = v / vmax * (vol_h - 6)
        out.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='var(--bar2)'/>" % (x - cw * 0.35, h - 18 - vh,
                                                                                              cw * 0.7, vh))
    for key, color in (("ema20", "var(--accent)"), ("sma50", "var(--warn)")):
        pts = ["%.1f,%.1f" % (i * cw + cw / 2, y(v)) for i, v in enumerate(ch.get(key) or []) if v]
        if pts:
            out.append("<polyline fill='none' stroke='%s' stroke-width='1.4' points='%s'/>" % (color, " ".join(pts)))
    for val, label, color, dash in ((entry, "entry", "var(--accent)", "5 3"), (stop, "stop", "var(--down)", "5 3"),
                                    (target, "target", "var(--up)", "5 3"), (resistance, "resist.", "var(--muted)", "2 3")):
        if val and lo <= val <= hi:
            out.append("<line x1='0' x2='%d' y1='%.1f' y2='%.1f' stroke='%s' stroke-dasharray='%s' stroke-width='1.3'/>"
                       % (w - pad_r, y(val), y(val), color, dash))
            out.append("<text x='%d' y='%.1f'>%s %s</text>" % (w - pad_r + 4, y(val) + 4, label, fmt(val, 1)))
    out.append("<text x='0' y='%d'>%s</text><text x='%d' y='%d' text-anchor='end'>%s</text>"
               % (h - 3, e(ch["d"][0]), w - pad_r, h - 3, e(ch["d"][-1])))
    out.append("<text x='4' y='%d'>— 20 EMA</text><text x='70' y='%d' style='fill:var(--warn)'>— 50 SMA</text>"
               % (pad_t + 10, pad_t + 10))
    out.append("</svg>")
    return "".join(out)


def line_svg(series, w=900, h=280, labels=None):
    """series: list of (name, color, [(date, value)]) normalised by the caller."""
    pts_all = [v for _, _, s in series for _, v in s]
    if not pts_all:
        return ""
    lo, hi = min(pts_all), max(pts_all)
    span = (hi - lo) or 1
    lo -= span * 0.05
    hi += span * 0.05
    pad_l, pad_b = 62, 22
    dates = sorted({d for _, _, s in series for d, _ in s})
    pos = {d: i for i, d in enumerate(dates)}
    n = max(len(dates) - 1, 1)

    def x(d):
        return pad_l + pos[d] / n * (w - pad_l - 10)

    def y(v):
        return 8 + (hi - v) / (hi - lo) * (h - pad_b - 8)
    out = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' aria-label='equity curve'>" % (w, h)]
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append("<line x1='%d' x2='%d' y1='%.1f' y2='%.1f' stroke='var(--line)'/><text x='%d' y='%.1f' "
                   "text-anchor='end'>%s</text>" % (pad_l, w - 10, y(v), y(v), pad_l - 6, y(v) + 4, fmt(v)))
    for name, color, s in series:
        out.append("<polyline fill='none' stroke='%s' stroke-width='1.8' points='%s'/>"
                   % (color, " ".join("%.1f,%.1f" % (x(d), y(v)) for d, v in s)))
    out.append("<text x='%d' y='%d'>%s</text><text x='%d' y='%d' text-anchor='end'>%s</text>"
               % (pad_l, h - 4, e(dates[0]), w - 10, h - 4, e(dates[-1])))
    lx = pad_l + 8
    for name, color, _ in series:
        out.append("<text x='%d' y='20' style='fill:%s;font-weight:600'>%s</text>" % (lx, color, e(name)))
        lx += 9 * len(name) + 24
    out.append("</svg>")
    return "".join(out)


def hist_svg(values, w=900, h=200):
    bins = [(-99, -1.5), (-1.5, -1.0), (-1.0, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 5), (5, 99)]
    labels = ["< -1.5R", "-1.5 to -1", "-1 to -0.5", "-0.5 to 0", "0 to 0.5", "0.5 to 1", "1 to 1.5", "1.5 to 2",
              "2 to 3", "3 to 5", "> 5R"]
    counts = [sum(1 for v in values if a <= v < b) for a, b in bins]
    mx = max(counts) or 1
    bw = (w - 20) / len(bins)
    h = h + 6
    out = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' aria-label='R distribution'>" % (w, h)]
    for i, c in enumerate(counts):
        bh = c / mx * (h - 44)
        col = "var(--down)" if bins[i][1] <= 0 else "var(--up)"
        x = 10 + i * bw
        out.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='%s' rx='3'/>" % (x + 3, h - 22 - bh, bw - 6, bh, col))
        out.append("<text x='%.1f' y='%.1f' text-anchor='middle'>%d</text>" % (x + bw / 2, h - 26 - bh, c))
        out.append("<text x='%.1f' y='%d' text-anchor='middle'>%s</text>" % (x + bw / 2, h - 6, labels[i]))
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- funnel report

def write_funnel_report(path, run):
    reg = run["regime"]
    b = []
    b.append("<h1>Swing funnel: %s</h1><div class='sub'>Close of %s · universe %s · setup: %s · generated %s</div>"
             % (e(run["asof"]), e(run["asof"]), e(run["universe"]), e(", ".join(run["setups"])), e(run["generated"])))
    # funnel
    total = max(run["layers"][0]["count"], 1)
    rows = []
    for L in run["layers"]:
        wpct = max(8, L["count"] / total * 100)
        why = "; ".join("%s: %d" % (k, v) for k, v in L["rejects"][:4])
        rows.append("<div class='row'><div class='n'>%s</div><div><div class='bar' style='width:%.1f%%'>%s</div>"
                    "%s</div></div>" % (fmt(L["count"]), wpct, e(L["label"]),
                                       ("<div class='why'>Removed here: %s</div>" % e(why)) if why else ""))
    b.append("<h2>The funnel</h2><div class='card funnel'>%s<p class='note'>Layers 1-4 find good stocks. "
             "Layers 5-6 ask whether there is a good trade today. They are not the same question.</p></div>" % "".join(rows))
    # regime
    facts = "".join("<li>%s</li>" % e(f) for f in reg["facts"])
    idx_rows = "".join("<tr><td>%s</td><td>%s</td><td class='%s'>%s</td><td class='%s'>%s</td><td class='%s'>%s</td>"
                       "<td class='%s'>%s</td><td class='%s'>%s</td></tr>"
                       % (e(r["name"]), fmt(r["close"]), cls(r["ret_5"]), num(r["ret_5"], 1, True, "%"),
                          cls(r["ret_21"]), num(r["ret_21"], 1, True, "%"), cls(r["ret_63"]), num(r["ret_63"], 1, True, "%"),
                          cls(r["vs50"]), num(r["vs50"], 1, True, "%"), cls(r["vs200"]), num(r["vs200"], 1, True, "%"))
                       for r in run["indices"])
    b.append("<h2>1 · Market regime</h2><div class='card'><span class='badge b-%s'>%s</span> &nbsp;risk multiplier "
             "<b>x%s</b><ul>%s<li>%d advancers / %d decliners; %d new 52-week highs / %d new lows</li></ul>"
             "<div class='wrap'><table><tr><th>Index</th><th>Close</th><th>1 week</th><th>1 month</th><th>3 months</th>"
             "<th>vs 50 DMA</th><th>vs 200 DMA</th></tr>%s</table></div></div>"
             % (e(reg["state"]), e(reg.get("label", reg["state"]).upper()), reg["risk_multiplier"], facts, reg["advancers"],
                reg["decliners"], reg["new_highs"], reg["new_lows"], idx_rows))
    # sectors
    srows = []
    mx = max([abs(r["score"]) for r in run["sectors"]] + [1])
    for r in run["sectors"]:
        top = r["sector"] in run["top_sectors"]
        bar = "<span style='display:inline-block;height:9px;width:%dpx;background:%s;border-radius:3px'></span>" % (
            int(abs(r["score"]) / mx * 70), "var(--up)" if r["score"] > 0 else "var(--down)")
        srows.append("<tr%s><td class='l'>%s%s</td><td>%d</td><td class='%s'>%s</td><td class='%s'>%s</td>"
                     "<td class='%s'>%s</td><td class='%s'>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='l'>%s</td></tr>"
                     % (" style='background:var(--soft)'" if top else "", "★ " if top else "", e(r["sector"]),
                        r["members"], cls(r["ex_5"]), num(r["ex_5"], 1, True), cls(r["ex_21"]), num(r["ex_21"], 1, True),
                        cls(r["ex_63"]), num(r["ex_63"], 1, True), cls(r.get("ex_126")), num(r.get("ex_126"), 1, True),
                        num(r["breadth50"], 0, False, "%"), num(r.get("participation"), 0, False, "%"),
                        num(r.get("vol_ratio"), 2, False, "x"), bar))
    sirows = "".join("<tr><td>%s</td><td class='%s'>%s</td><td class='%s'>%s</td><td class='%s'>%s</td></tr>"
                     % (e(r["name"]), cls(r["rs_5"]), num(r["rs_5"], 1, True), cls(r["rs_21"]), num(r["rs_21"], 1, True),
                        cls(r["rs_63"]), num(r["rs_63"], 1, True)) for r in run["sector_indices"])
    b.append("<h2>2 · Sector strength</h2><div class='card'><p class='note'>Each sector is the median of its stocks, "
             "measured against the Nifty 500 in percentage points (relative strength), plus breadth: the share of its "
             "stocks above their 50-day average, the share beating the index (is it most stocks or a few heavyweights?), and volume expansion. A sector up 3%% while the market is up 5%% is weak. ★ = taken forward "
             "(top %d that beat the index).</p><div class='wrap'><table><tr><th class='l'>Sector</th><th>Stocks</th>"
             "<th>1w vs index</th><th>1m vs index</th><th>3m vs index</th><th>6m vs index</th><th>Above 50 DMA</th>"
             "<th>Beating index (1m)</th><th>Volume 10d/50d</th><th class='l'>Score</th></tr>%s"
             "</table></div>%s</div>"
             % (run["funnel_cfg"].get("top_sectors", 3), "".join(srows),
                ("<details style='margin-top:12px'><summary>NSE sector indices vs Nifty 50</summary><div class='wrap'>"
                 "<table><tr><th class='l'>Index</th><th>1w vs Nifty 50</th><th>1m</th><th>3m</th></tr>%s</table>"
                 "</div></details>" % sirows) if sirows else ""))
    # watchlist
    wrows = "".join("<tr><td class='l'><b>%s</b></td><td class='l'>%s</td><td class='l'>%s</td><td>%d</td><td>%s</td>"
                    "<td class='%s'>%s</td><td>%s</td><td>%s</td></tr>"
                    % (e(w["symbol"]), e(w["sector"]) + ("" if w["in_top_sector"] else " <span class='note'>(RS leader)</span>"), e(w["cap"]), w["rs_rank"], fmt(w["close"], 2), cls(w["ex_63"]),
                       num(w["ex_63"], 1, True), num(w["atr_pct"], 1, False, "%"), num(w["tv_cr"], 0))
                    for w in run["watchlist"])
    b.append("<h2>3-4 · Watchlist: strong stocks in strong places (%d)</h2><div class='card'><p class='note'>Liquid, "
             "affordable, not under surveillance, in a leading sector (or an RS leader anywhere), RS rank ≥ %s, "
             "trend filter '%s'. These are stocks worth watching, not trades.</p><div class='wrap'><table><tr>"
             "<th class='l'>Stock</th><th class='l'>Sector</th><th class='l'>Cap</th><th>RS rank</th><th>Close</th>"
             "<th>3m vs index</th><th>ATR %%</th><th>Traded Rs cr/day</th></tr>%s</table></div></div>"
             % (len(run["watchlist"]), run["funnel_cfg"].get("min_rs"), e(run["funnel_cfg"].get("trend")), wrows))
    # candidates
    b.append("<h2>5-7 · Setups that triggered (%d)</h2>" % len(run["candidates"]))
    if not run["candidates"]:
        b.append("<div class='card'>No setup triggered on the watchlist at this close. No trade is a valid outcome: "
                 "cash is a position.</div>")
    for c in run["candidates"]:
        chk = "".join("<tr><td><span class='badge b-%s'>%s</span></td><td>%s</td><td>%s</td></tr>"
                      % (x["status"], x["status"], e(x["check"]), e(x["detail"])) for x in c["checks"])
        sz = c["sizing"]
        size_html = ""
        if sz.get("ok"):
            size_html = ("<div class='kpi' style='margin-top:10px'><div><b>%d</b><span>shares</span></div>"
                         "<div><b>Rs %s</b><span>position (%.0f%% of capital)</span></div>"
                         "<div><b>Rs %s</b><span>at risk if stopped</span></div>%s</div>"
                         % (sz["qty"], fmt(sz["position_value"]), sz["position_pct"], fmt(sz["risk_rupees"]),
                            ("<div><b>Rs %s</b><span>at target, after charges</span></div>" % fmt(sz["net_at_target"]))
                            if sz.get("net_at_target") is not None else ""))
        b.append("<div class='card'><h3><span class='badge b-%s'>%s</span> &nbsp;%s <span class='sub'>%s · %s · RS %d · "
                 "%s</span></h3><div class='grid' style='margin-top:10px'><div>%s</div><div><table class='checks'>%s"
                 "</table>%s</div></div></div>"
                 % (c["status"], c["status"], e(c["symbol"]), e(c["name"] or ""), e(c["sector"]), c["rs_rank"],
                    e(c["setup"]), candle_svg(c["chart"], c["entry"], c["stop"], c["target"], c["resistance"]),
                    chk, size_html))
    b.append("<div class='card note'><b>Your manual check before any order:</b> open the chart on your broker; is the "
             "entry condition still true tomorrow morning? Read the latest NSE announcements for the stock. Check the "
             "bid-ask spread in market depth. Confirm the stop is where you are actually wrong, not just a number. "
             "If anything is off, skip it. AI finds candidates; the decision is yours.</div>")
    if run["warnings"]:
        b.append("<div class='card note'>%s</div>" % "<br>".join(e(w) for w in run["warnings"]))
    rj = "".join("<tr><td class='l'>%s</td><td class='l'>%s</td><td class='l'>%s</td></tr>" % (e(k), e(v["layer"]),
                 e(v["reason"])) for k, v in sorted(run["rejected"].items()))
    b.append("<details class='card'><summary>Every stock that was filtered out, and why (%d)</summary><div class='wrap'>"
             "<table><tr><th class='l'>Stock</th><th class='l'>Stopped at</th><th class='l'>Reason</th></tr>%s</table>"
             "</div></details>" % (len(run["rejected"]), rj))
    with open(path, "w") as fh:
        fh.write(page("Swing funnel %s" % run["asof"], "".join(b)))


# ---------------------------------------------------------------- backtest report

def stats_row(name, s):
    if not s.get("n"):
        return "<tr><td class='l'>%s</td><td>0</td><td colspan='8' class='l note'>no trades</td></tr>" % e(name)
    return ("<tr><td class='l'>%s</td><td>%d</td><td>%s</td><td class='pos'>%s</td><td class='neg'>%s</td>"
            "<td class='%s'><b>%s</b></td><td>%s</td><td>%s</td><td class='neg'>%s</td><td>%s</td></tr>"
            % (e(name), s["n"], num(s["win_rate"], 1, False, "%"), num(s["avg_win_r"], 2, True), num(s["avg_loss_r"], 2, True),
               cls(s["expectancy_r"]), num(s["expectancy_r"], 2, True), num(s["gross_expectancy_r"], 2, True),
               num(s["profit_factor"], 2), num(s["max_dd_r"], 1), num(s["avg_bars"], 0)))


def exit_words(x):
    parts = []
    if x.get("target_r"):
        parts.append("target %sR" % x["target_r"])
    if x.get("trail"):
        k, _, v = x["trail"].partition(":")
        parts.append({"ema": "close below the %s EMA" % v, "low": "trail under the %s-day low" % v,
                      "atr": "trail %s ATR under the highest close" % v, "pivot": "trail under the last swing low"}.get(k, x["trail"]))
    if x.get("breakeven_at_r"):
        parts.append("breakeven at +%sR" % x["breakeven_at_r"])
    if x.get("time_stop"):
        parts.append("time stop %s sessions" % x["time_stop"])
    return ", ".join(parts) or "stop only"


def write_backtest_report(path, res, trades):
    wf, so = res["with_funnel"], res["setup_only"]
    b = []
    b.append("<h1>Backtest: %s</h1><div class='sub'>%s to %s · %d stocks · %d signals · out-of-sample from %s · "
             "exits: %s</div>" % (e(res["setup"]), e(res["window"][0]), e(res["window"][1]), res["stocks"],
                                  res["signals"], e(res["oos_from"]), e(exit_words(res["exit_rules"]))))
    v = res["verdict"]
    b.append("<div class='card verdict'><span class='badge b-%s'>%s</span><p>%s</p></div>"
             % (e(v["tone"]), e(v["tone"].upper()), "<br>".join(e(x) for x in v["lines"])))
    head = ("<tr><th class='l'></th><th>Trades</th><th>Win rate</th><th>Avg win</th><th>Avg loss</th>"
            "<th>Expectancy (net)</th><th>Gross</th><th>Profit factor</th><th>Max DD</th><th>Avg days</th></tr>")
    rows = [stats_row("Setup alone", so["all"]), stats_row("Setup + funnel", wf["all"]),
            stats_row("  in-sample", wf["in_sample"]), stats_row("  out-of-sample", wf["out_of_sample"])]
    rows += [stats_row("Setup alone, %s market" % k, so["by_regime"][k]) for k in ("bullish", "neutral", "bearish")]
    b.append("<h2>Per trade, in R, after charges and slippage</h2><div class='card'><div class='wrap'><table>%s%s</table>"
             "</div><p class='note'>1R is the rupee risk from entry to stop. Expectancy is the average result per trade: "
             "+0.20R means a trader risking Rs 5,000 a trade made about Rs 1,000 a trade on average. Charges per trade "
             "averaged %sR.</p></div>" % (head, "".join(rows), num(wf["all"].get("avg_charges_r"), 2)))
    pf, ps, bm = res["portfolio_funnel"], res["portfolio_setup_only"], res["benchmark"]
    cap = pf["start_capital"]
    base = bm["curve"][0][1] if bm["curve"] else 1
    series = [("Setup + funnel", "var(--accent)", pf["curve"]), ("Setup alone", "var(--warn)", ps["curve"]),
              ("Nifty 500", "var(--muted)", [(d, c / base * cap) for d, c in bm["curve"]])]
    b.append("<h2>Portfolio with your limits</h2><div class='card'><div class='kpi'>"
             "<div><b>%s%%</b><span>CAGR, setup + funnel</span></div><div><b>%s%%</b><span>max drawdown</span></div>"
             "<div><b>%d</b><span>trades taken (%d skipped for limits)</span></div><div><b>%s%%</b><span>days in the market</span></div>"
             "<div><b>%s%% / %s%%</b><span>Nifty 500 CAGR / max DD</span></div></div>%s<p class='note'>Capital Rs %s, "
             "risk %s%% a trade scaled by the regime, at most %d positions. Setup alone: CAGR %s%%, max DD %s%%.</p></div>"
             % (num(pf["cagr_pct"], 1), num(pf["max_drawdown_pct"], 1), pf["trades"], pf["skipped_for_limits"],
                num(pf["time_in_market_pct"], 0), num(bm["cagr_pct"], 1), num(bm["max_drawdown_pct"], 1),
                line_svg(series), fmt(cap), res["profile"]["risk_per_trade_pct"], res["profile"]["max_positions"],
                num(ps["cagr_pct"], 1), num(ps["max_drawdown_pct"], 1)))
    fun_r = [t["r_net"] for t in trades if t["in_funnel"]]
    b.append("<h2>Distribution of results (setup + funnel)</h2><div class='card'>%s<p class='note'>Most swing systems "
             "lose more often than they win; the edge, if any, comes from winners being bigger than losers.</p></div>"
             % hist_svg(fun_r))
    yrows = "".join(stats_row(y, s) for y, s in wf["by_year"].items())
    b.append("<h2>By year</h2><div class='card'><div class='wrap'><table>%s%s</table></div></div>" % (head, yrows))
    em = "".join("<tr><td class='l'>%s</td><td>%d</td></tr>" % (e(k), n) for k, n in wf["all"].get("exit_mix", {}).items())
    fr = "".join("<tr><td class='l'>%s</td><td>%d</td></tr>" % (e(k), n) for k, n in res["funnel_rejects"].items())
    b.append("<div class='grid'><div class='card'><h3>How trades ended</h3><table>%s</table></div><div class='card'><h3>"
             "Signals the funnel filtered out</h3><table>%s</table></div></div>" % (em, fr))
    b.append("<h2>What this test cannot tell you</h2><div class='card'><ul>"
             "<li><b>Survivorship bias.</b> Today's index list was used for the whole period. Stocks that fell out "
             "(often after falling) are missing, so results look better than reality.</li>"
             "<li><b>Inside a daily bar</b> we cannot see whether the stop or the target came first; the stop is assumed.</li>"
             "<li><b>Every tweak you make after seeing this report</b> fits the rules to this data and uses up the "
             "out-of-sample test. Decide changes for a reason, then re-test on a fresh period.</li>"
             "<li>Charges are a discount broker's delivery charges (editable in profile.json); slippage %s%% a fill. "
             "Taxes (short-term capital gains) are not included.</li>"
             "<li>Free Yahoo data can have errors and gaps. Results are not a promise of future returns.</li></ul></div>"
             % e(res["profile"]["charges"].get("slippage_pct", 0.1)))
    recent = sorted([t for t in trades if t["in_funnel"]], key=lambda t: t["entry_date"])[-60:]
    trs = "".join("<tr><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='l'>%s</td>"
                  "<td class='%s'>%s</td></tr>" % (e(t["symbol"]), e(t["entry_date"]), fmt(t["entry"], 2), e(t["exit_date"]),
                                                   fmt(t["exit"], 2), e(t["exit_reason"]), cls(t["r_net"]),
                                                   num(t["r_net"], 2, True)) for t in reversed(recent))
    b.append("<details class='card'><summary>Last 60 trades (setup + funnel); all trades are in trades.csv</summary>"
             "<div class='wrap'><table><tr><th class='l'>Stock</th><th>Entry date</th><th>Entry</th><th>Exit date</th>"
             "<th>Exit</th><th class='l'>Why</th><th>R net</th></tr>%s</table></div></details>" % trs)
    with open(path, "w") as fh:
        fh.write(page("Backtest %s" % res["setup"], "".join(b)))
