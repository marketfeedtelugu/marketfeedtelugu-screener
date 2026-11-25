"""Script-free HTML for the dashboard.

The dashboard's tabs, sortable tables and canvas charts are drawn by JavaScript. Some viewers never run
scripts: the Claude app's file pane and its browser pane for a file outside the project, Quick Look,
mail and chat previews. There a script-only page shows nothing but its footer. So `dashboard.write`
also puts this static version of every tab in the page (funnel, trade plans with checks and news, an SVG
chart for every stock, backtest, forward test, strategy). When scripts do run they replace it with the
interactive version; when they don't, the static version is what the trader sees.
"""
from html import escape
import json
import math


def e(x):
    return escape("" if x is None else str(x), quote=True)


def bad(x):
    return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def inr(x, d=0):
    """Indian digit grouping: 12,34,567.89."""
    if bad(x):
        return "n/a"
    neg = x < 0
    s = ("%." + str(d) + "f") % abs(x)
    w, _, f = s.partition(".")
    if len(w) > 3:
        head, tail = w[:-3], w[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        w = ",".join(parts) + "," + tail
    return ("-" if neg else "") + w + ("." + f if f else "")


def num(x, d=1, sign=False):
    if bad(x):
        return "n/a"
    s = ("%." + str(d) + "f") % x
    return ("+" + s) if sign and x > 0 else s


def cls(x):
    if bad(x):
        return ""
    return "pos" if x > 0 else "neg" if x < 0 else ""


def badge(kind, text=None):
    return "<span class='badge b-%s'>%s</span>" % (e(kind), e(text if text is not None else kind))


# ------------------------------------------------------------------------------------------- charts
def _p(v):
    return "%.1f" % v


def chart_svg(ch, w=900, h=380):
    """Candles, volume, the card's overlays, entry / stop / target / resistance with the risk and reward
    zones, and past signals with their outcome. Same drawing as the interactive chart, as inline SVG."""
    if not ch or not ch.get("c"):
        return "<p class='muted small'>No chart data for this stock.</p>"
    n = len(ch["c"])
    O, H, L, C, V = ch["o"], ch["h"], ch["l"], ch["c"], ch.get("v") or [0] * n
    pad_r, pad_t, pad_b = 74, 8, 20
    vol_h = round(h * 0.16)
    ph = h - vol_h - pad_t - pad_b - 4
    lows = [v for v in L if not bad(v)]
    highs = [v for v in H if not bad(v)]
    if not lows or not highs:
        return "<p class='muted small'>No chart data for this stock.</p>"
    lo, hi = min(lows), max(highs)
    P = ch.get("plan") or None
    if P:
        for v in (P.get("entry"), P.get("stop"), P.get("target")):
            if not bad(v):
                lo, hi = min(lo, v), max(hi, v)
        r = P.get("resistance")
        if not bad(r) and r < hi * 1.15:
            hi = max(hi, r)
    span = (hi - lo) or 1
    lo -= span * 0.03
    hi += span * 0.03
    cw = (w - pad_r) / n

    def y(v):
        return pad_t + (hi - v) / (hi - lo) * ph

    def x(i):
        return i * cw + cw / 2

    out = ["<svg class='sv' viewBox='0 0 %d %d' preserveAspectRatio='none' role='img' aria-label='price chart'>" % (w, h),
           "<rect class='sv-bg' x='0' y='0' width='%d' height='%d'/>" % (w, h)]
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = y(v)
        out.append("<line class='sv-grid' x1='0' x2='%s' y1='%s' y2='%s'/>" % (_p(w - pad_r), _p(yy), _p(yy)))
        out.append("<text class='sv-t' x='%s' y='%s'>%s</text>" % (_p(w - pad_r + 4), _p(yy + 4), inr(v, 2 if v < 100 else 0)))
    vmax = max([v for v in V if not bad(v)] or [1]) or 1
    bw = max(1.0, cw * 0.65)
    wick = {True: [], False: []}
    body = {True: [], False: []}
    vol = {True: [], False: []}
    for i in range(n):
        o, hh, l, c = O[i], H[i], L[i], C[i]
        if any(bad(z) for z in (o, hh, l, c)):
            continue
        up = c >= o
        xx = x(i)
        wick[up].append("M%s %sV%s" % (_p(xx), _p(y(hh)), _p(y(l))))
        t, b = y(max(o, c)), y(min(o, c))
        body[up].append("M%s %sh%sv%sh-%sZ" % (_p(xx - bw / 2), _p(t), _p(bw), _p(max(1, b - t)), _p(bw)))
        vv = V[i] if not bad(V[i]) else 0
        vh = vv / vmax * vol_h
        vol[up].append("M%s %sh%sv%sh-%sZ" % (_p(xx - bw / 2), _p(h - pad_b - vh), _p(bw), _p(vh), _p(bw)))
    for up, k in ((True, "up"), (False, "dn")):
        if vol[up]:
            out.append("<path class='sv-vol-%s' d='%s'/>" % (k, "".join(vol[up])))
        if wick[up]:
            out.append("<path class='sv-wick-%s' d='%s'/>" % (k, "".join(wick[up])))
        if body[up]:
            out.append("<path class='sv-%s' d='%s'/>" % (k, "".join(body[up])))
    for k, o in enumerate(ch.get("overlays") or []):
        d, on = [], False
        for i, v in enumerate(o.get("values") or []):
            if bad(v):
                on = False
                continue
            d.append(("L" if on else "M") + "%s %s" % (_p(x(i)), _p(y(v))))
            on = True
        if d:
            out.append("<path class='sv-ov sv-c%d' d='%s'/>" % (k % 5 + 1, "".join(d)))
    x0 = 0.0
    if P and not bad(P.get("signal_i")):
        si = int(P["signal_i"])
        x0 = x(max(0, min(si, n - 1) - round(n * 0.15)))
    if P and not bad(P.get("entry")) and not bad(P.get("stop")):
        ye, ys = y(P["entry"]), y(P["stop"])
        out.append("<rect class='sv-risk' x='%s' y='%s' width='%s' height='%s'/>" % (_p(x0), _p(min(ye, ys)), _p(w - pad_r - x0), _p(abs(ys - ye))))
        if not bad(P.get("target")):
            yt = y(P["target"])
            out.append("<rect class='sv-reward' x='%s' y='%s' width='%s' height='%s'/>" % (_p(x0), _p(min(yt, ye)), _p(w - pad_r - x0), _p(abs(ye - yt))))

    def hline(v, k, label, dash=False):
        if bad(v):
            return
        yy = y(v)
        out.append("<line class='sv-l-%s%s' x1='%s' x2='%s' y1='%s' y2='%s'/>" % (k, " sv-dash" if dash else "", _p(x0), _p(w - pad_r), _p(yy), _p(yy)))
        out.append("<rect class='sv-f-%s' x='%s' y='%s' width='%d' height='16'/>" % (k, _p(w - pad_r), _p(yy - 8), pad_r))
        out.append("<text class='sv-lt' x='%s' y='%s'>%s %s</text>" % (_p(w - pad_r + 3), _p(yy + 4), label, inr(v, 2)))
    if P:
        hline(P.get("resistance"), "res", "R", True)
        hline(P.get("target"), "tgt", "T")
        hline(P.get("stop"), "stp", "S")
        hline(P.get("entry"), "ent", "E")
    for s in ch.get("signals") or []:
        si = s.get("s")
        if bad(si) or not 0 <= si < n or bad(L[si]):
            continue
        xs, yy = x(si), y(L[si]) + 10
        out.append("<path class='sv-sig' d='M%s %sL%s %sL%s %sZ'/>" % (_p(xs), _p(yy - 6), _p(xs - 5), _p(yy + 3), _p(xs + 5), _p(yy + 3)))
        if not bad(s.get("e")) and not bad(s.get("x")) and not bad(s.get("ep")) and not bad(s.get("xp")):
            k = "win" if (s.get("r") or 0) > 0 else "loss"
            x1, y1, x2, y2 = x(s["e"]), y(s["ep"]), x(s["x"]), y(s["xp"])
            out.append("<line class='sv-%s sv-dash' x1='%s' y1='%s' x2='%s' y2='%s'/>" % (k, _p(x1), _p(y1), _p(x2), _p(y2)))
            out.append("<circle class='sv-%sd' cx='%s' cy='%s' r='3'/><circle class='sv-%sd' cx='%s' cy='%s' r='3'/>"
                       % (k, _p(x1), _p(y1), k, _p(x2), _p(y2)))
    if P and not bad(P.get("signal_i")):
        xs = x(max(0, min(int(P["signal_i"]), n - 1)))
        out.append("<rect class='sv-band' x='%s' y='%d' width='%s' height='%s'/>" % (_p(xs - cw / 2), pad_t, _p(cw), _p(ph)))
    t = ch.get("t") or []
    step = max(1, math.ceil(n / max(2, int((w - pad_r) / 95))))
    for i in range(0, min(n, len(t)), step):
        lab = t[i][5:10] + " " + t[i][11:16] if len(t[i]) > 10 else t[i][2:]
        out.append("<text class='sv-t' x='%s' y='%d' text-anchor='middle'>%s</text>" % (_p(x(i)), h - 5, e(lab)))
    out.append("</svg>")
    leg = []
    for k, o in enumerate(ch.get("overlays") or []):
        leg.append("<span><i class='lg-c%d'></i>%s</span>" % (k % 5 + 1, e(o.get("label"))))
    if P:
        leg.append("<span><i class='lg-ent'></i>entry</span><span><i class='lg-stp'></i>stop</span>")
        if not bad(P.get("target")):
            leg.append("<span><i class='lg-tgt'></i>target</span>")
        if not bad(P.get("resistance")):
            leg.append("<span><i class='lg-res'></i>resistance</span>")
    sig = ch.get("signals") or []
    if sig:
        leg.append("<span>&#9650; past signals (%d): green = won, red = lost</span>" % len(sig))
    return "<div class='chartbox'>%s<div class='legend'>%s</div></div>" % ("".join(out), "".join(leg))


def line_svg(series, w=900, h=320):
    """series: [(label, css class, [(date, value)])], values already normalised by the caller."""
    series = [(lab, k, [(d, v) for d, v in pts if not bad(v)]) for lab, k, pts in series]
    series = [s for s in series if len(s[2]) > 1]
    if not series:
        return ""
    allv = [v for _, _, pts in series for _, v in pts]
    lo, hi = min(allv), max(allv)
    sp = (hi - lo) or 1
    lo -= sp * 0.05
    hi += sp * 0.05
    pad_r, pad_b = 56, 18

    def y(v):
        return 8 + (hi - v) / (hi - lo) * (h - pad_b - 8)
    out = ["<svg class='sv' viewBox='0 0 %d %d' preserveAspectRatio='none' role='img' aria-label='equity curve'>" % (w, h),
           "<rect class='sv-bg' x='0' y='0' width='%d' height='%d'/>" % (w, h)]
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append("<line class='sv-grid' x1='0' x2='%d' y1='%s' y2='%s'/>" % (w - pad_r, _p(y(v)), _p(y(v))))
        out.append("<text class='sv-t' x='%d' y='%s'>%s</text>" % (w - pad_r + 4, _p(y(v) + 4), num(v, 0)))
    for _, k, pts in series:
        m = len(pts)
        stride = max(1, m // 600)
        idx = list(range(0, m, stride))
        if idx[-1] != m - 1:
            idx.append(m - 1)
        d = "".join(("L" if j else "M") + "%s %s" % (_p(i / (m - 1) * (w - pad_r)), _p(y(pts[i][1]))) for j, i in enumerate(idx))
        out.append("<path class='sv-eq %s' d='%s'/>" % (k, d))
    p0 = series[0][2]
    for i in (0, len(p0) // 2, len(p0) - 1):
        xx = min(w - pad_r - 30, max(30, i / (len(p0) - 1) * (w - pad_r)))
        out.append("<text class='sv-t' x='%s' y='%d' text-anchor='middle'>%s</text>" % (_p(xx), h - 4, e(str(p0[i][0])[:10])))
    out.append("</svg>")
    leg = "".join("<span><i class='lg-%s'></i>%s</span>" % (k, e(lab)) for lab, k, _ in series)
    return "<div class='chartbox'>%s<div class='legend'>%s</div></div>" % ("".join(out), leg)


# ------------------------------------------------------------------------------------------- tabs
def header(S):
    sub = "%s &middot; %s chart &middot; last bar %s" % (e(S.get("universe_label")), e(S.get("timeframe")), e(S.get("asof")))
    if S.get("timeframe") not in ("1d", "1w") and S.get("daily_asof"):
        sub += " &middot; daily filters as of " + e(S.get("daily_asof"))
    sub += " &middot; generated " + e(str(S.get("generated") or "").replace("T", " "))
    return e(S.get("title") or "Strategy"), sub


def nav():
    return ("<a href='#tab-screener'>Screener</a><a href='#tab-backtest'>Backtest</a>"
            "<a href='#tab-forward'>Forward test</a><a href='#tab-strategy'>Strategy</a>")


def news_html(NW, sym):
    if not NW:
        return "<p class='small muted'>News not checked in this run.</p>"
    sn = (NW.get("stocks") or {}).get(sym)
    note = (NW.get("notes") or {}).get(sym)
    if not sn and not note:
        return "<p class='small muted'>No news fetched for this stock (only PASS / REVIEW plans are checked).</p>"
    h = ["<h3 style='margin-top:14px'>News <span class='muted small'>%s, up to %s</span></h3>"
         % ("live" if NW.get("live") else "replay", e(NW.get("until")))]
    if note:
        h.append("<div class='verdict small' style='margin:6px 0'>Your catalyst note: %s</div>" % e(note.get("text")))
    if sn:
        lv = {"red": "neg", "check": "", "info": "muted"}
        if sn.get("flags"):
            h.append("<ul class='small'>%s</ul>" % "".join("<li class='%s'><b>%s</b> %s</li>" % (lv.get(f.get("level"), ""), e(str(f.get("level", "")).upper()), e(f.get("text")))
                                                            for f in sn["flags"][:6]))

        def link(u, t):
            return "<a href='%s' target='_blank' rel='noopener noreferrer'>%s</a>" % (e(u), e(t)) if str(u or "").startswith(("http://", "https://")) else e(t)
        fl = sn.get("filings") or []
        h.append("<div class='small'><b>NSE filings, last %s days (%d)</b>" % (e(NW.get("filing_days")), len(fl)))
        h.append("<ul>%s</ul>" % "".join("<li>%s%s %s</li>" % ("<b>!</b> " if f.get("important") else "", e((f.get("time_ist") or "")[:10]), link(f.get("link"), f.get("subject")))
                                          for f in fl[:8]) if fl else "<p class='muted'>none</p>")
        h.append("</div>")
        hd = sn.get("headlines") or []
        h.append("<div class='small'><b>Headlines, last %s days (%d)</b>" % (e(NW.get("headline_days")), len(hd)))
        h.append("<ul>%s</ul>" % "".join("<li>%s %s <span class='muted'>%s</span></li>" % (e((x.get("time_ist") or "")[:10]), link(x.get("link"), x.get("title")), e(x.get("publisher")))
                                          for x in hd[:6]) if hd else "<p class='muted'>none</p>")
        h.append("</div>")
    h.append("<p class='small muted'>Headlines are leads, not facts: open the filing or story before acting.</p>")
    return "".join(h)


def screener(D):
    S = D["scan"]
    C = S.get("candidates") or []
    WL = S.get("watchlist") or []
    rules = ((D.get("card") or {}).get("entry") or {}).get("when") or []
    h = ["<h2 class='sec'>Screener</h2>"]
    reg = S.get("regime") or {}
    if reg.get("state"):
        gate = ("<span class='muted small'>regime gate on: risk x%s</span>" % e(reg.get("risk_multiplier"))) if reg.get("gate") \
            else "<span class='muted small'>regime gate off (shown for context)</span>"
        h.append("<div class='card'><h2>Market %s %s</h2><div class='small muted'>%s</div>%s</div>" % (
            badge(reg["state"], reg.get("label")), gate, " &middot; ".join(e(f) for f in reg.get("facts") or []),
            ("<div class='small' style='margin-top:6px'>Top sectors: <b>%s</b></div>" % ", ".join(e(s) for s in S["top_sectors"])) if S.get("top_sectors") else ""))
    layers = S.get("layers") or []
    mx = max([l.get("count") or 0 for l in layers] + [1])
    rows = []
    for l in layers:
        why = ""
        if l.get("rejects"):
            why = "<div class='why'>out: %s</div>" % ", ".join("%s %s" % (e(k), e(v)) for k, v in l["rejects"][:4])
        rows.append("<div class='row'><div class='n'>%s</div><div><div class='bar' style='width:%.1f%%'>%s</div>%s</div></div>"
                    % (e(l.get("count")), max(4, (l.get("count") or 0) / mx * 100), e(l.get("label")), why))
    h.append("<div class='card funnel'><h2>The funnel</h2>%s</div>" % "".join(rows))
    ok = [c for c in C if c.get("status") != "REJECT"]
    h.append("<div class='card'><h2>Trade plans <span class='muted small'>%d triggered, %d passed validation</span></h2>" % (len(C), len(ok)))
    if C:
        h.append("<div class='wrap'><table><thead><tr><th class='l'>Status</th><th class='l'>Symbol</th><th class='l'>Sector</th><th class='l'>Order</th>"
                 "<th>Entry</th><th>Stop</th><th>Stop %</th><th>Target</th><th>R:R</th><th>Qty</th><th>Position &#8377;</th><th>Risk &#8377;</th><th>RS</th></tr></thead><tbody>")
        for c in C:
            sz = c.get("sizing") or {}
            en, st = c.get("entry"), c.get("stop")
            stop_pct = (en - st) / en * 100 if not bad(en) and not bad(st) and en else None
            typ = (c.get("signal") or {}).get("type")
            h.append("<tr><td class='l'>%s</td><td class='l'><a href='#s-%s'><b>%s</b></a>%s</td><td class='l'>%s</td><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                badge(c.get("status")), e(c.get("symbol")), e(c.get("symbol")), " " + badge("pending", "live") if c.get("state") == "pending" else "",
                e((c.get("sector") or "")[:26]), e({"market": "market", "stop": "buy-stop", "limit": "limit"}.get(typ, typ)),
                inr(en, 2), inr(st, 2), num(stop_pct, 1), inr(c.get("target"), 2) if c.get("target") else "trail",
                num(c.get("rr"), 1) if c.get("rr") else "-", sz.get("qty") if sz.get("ok") else "-",
                inr(sz.get("position_value")) if sz.get("ok") else "-", inr(sz.get("risk_rupees")) if sz.get("ok") else "-",
                "-" if bad(c.get("rs_rank")) else e(c.get("rs_rank"))))
        h.append("</tbody></table></div>")
    else:
        h.append("<p class='muted'>No stock triggered every entry rule on the last bar. Zero trades is a normal, healthy result.</p>")
    h.append("</div>")
    for c in C:
        sz = c.get("sizing") or {}
        kpi = ""
        if sz.get("ok"):
            kpi = "<div class='kpi' style='margin:8px 0'><div><b>%s</b><span>shares</span></div><div><b>&#8377;%s</b><span>position</span></div><div><b>&#8377;%s</b><span>at risk</span></div>%s</div>" % (
                e(sz.get("qty")), inr(sz.get("position_value")), inr(sz.get("risk_rupees")),
                "<div><b>&#8377;%s</b><span>net at target</span></div>" % inr(sz["net_at_target"]) if not bad(sz.get("net_at_target")) else "")
        checks = "".join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (badge(k.get("status")), e(k.get("check")), e(k.get("detail"))) for k in c.get("checks") or [])
        h.append("<div class='card' id='s-%s'><h3>%s <span class='muted small'>%s &middot; %s</span> %s %s <span class='muted small'>signal bar %s</span></h3>%s%s"
                 "<table class='checks'><tbody>%s</tbody></table>%s</div>" % (
                     e(c.get("symbol")), e(c.get("symbol")), e(c.get("name")), e(c.get("sector")), badge(c.get("status")),
                     badge(c.get("state"), "order still live" if c.get("state") == "pending" else "new signal"), e(c.get("signal_bar")),
                     chart_svg(c.get("chart")), kpi, checks, news_html(S.get("news"), c.get("symbol"))))
    h.append("<div class='card'><h2>Near misses <span class='muted small'>passed every filter; dots = entry rules true on the last bar</span></h2>")
    if WL:
        h.append("<div class='wrap'><table><thead><tr><th class='l'>Symbol</th><th class='l'>Sector</th><th class='l'>Rules true</th><th>Close</th><th>1m %</th><th>ATR %</th><th>Traded &#8377; cr</th><th>RS</th></tr></thead><tbody>")
        for x in WL:
            dots = "<span class='dots'>%s</span> %s/%s" % ("".join("<span class='%s'></span>" % ("y" if f else "") for f in x.get("flags") or []),
                                                          e(x.get("rules_true")), e(x.get("rules_total"))) if x.get("rules_true") is not None else "-"
            sym = "<b>%s</b>" % e(x.get("symbol"))
            if x.get("chart"):
                sym = "<a href='#s-%s'>%s</a>" % (e(x.get("symbol")), sym)
            h.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td class='l'>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                sym, e((x.get("sector") or "")[:26]), dots, inr(x.get("close"), 2), cls(x.get("ret_21")), num(x.get("ret_21"), 1, True),
                num(x.get("atr_pct"), 1), num(x.get("tv_cr"), 0), "-" if bad(x.get("rs_rank")) else e(x.get("rs_rank"))))
        h.append("</tbody></table></div></div>")
        charted = [x for x in WL if x.get("chart")]
        if charted:
            h.append("<h2 class='sec'>Charts of the near misses</h2><div class='grid-charts'>")
            for x in charted:
                fl = x.get("flags") or []
                rl = "".join("<li class='%s'>%s %s</li>" % ("pos" if (i < len(fl) and fl[i]) else "neg", "&#10003;" if (i < len(fl) and fl[i]) else "&#10007;", e(r))
                             for i, r in enumerate(rules))
                h.append("<div class='card' id='s-%s'><h3>%s <span class='muted small'>%s &middot; %s</span></h3>%s<div class='small'><b>%s of %s</b> entry rules true on the last bar:<ul>%s</ul></div></div>" % (
                    e(x.get("symbol")), e(x.get("symbol")), e(x.get("name")), e(x.get("sector")), chart_svg(x.get("chart")),
                    e(x.get("rules_true")), e(x.get("rules_total")), rl))
            h.append("</div>")
    else:
        h.append("<p class='muted'>No stock passed the filters.</p></div>")
    NW = S.get("news") or {}
    if NW.get("sectors"):
        secs = []
        for sec, hs in NW["sectors"].items():
            secs.append("<div class='small' style='margin:8px 0'><b>%s</b>%s</div>" % (e(sec), "<ul>%s</ul>" % "".join(
                "<li>%s %s <span class='muted'>%s</span></li>" % (e((x.get("time_ist") or "")[:10]), e(x.get("title")), e(x.get("publisher"))) for x in hs[:4]) if hs else " <span class='muted'>no headlines</span>"))
        h.append("<div class='card'><h2>Sector news</h2>%s</div>" % "".join(secs))
    if S.get("warnings"):
        h.append("<div class='card small muted'>%s</div>" % "<br>".join("&bull; " + e(w) for w in S["warnings"]))
    return "".join(h)


def _srow(name, s):
    if not s or not s.get("n"):
        return "<tr><td class='l'>%s</td><td colspan='9' class='muted l'>no trades</td></tr>" % name
    pf = s.get("profit_factor")
    return ("<tr><td class='l'>%s</td><td>%s</td><td>%s%%</td><td class='pos'>%s</td><td class='neg'>%s</td><td class='%s'><b>%s</b></td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>") % (
        name, e(s.get("n")), num(s.get("win_rate"), 1), num(s.get("avg_win_r"), 2, True), num(s.get("avg_loss_r"), 2, True),
        cls(s.get("expectancy_r")), num(s.get("expectancy_r"), 2, True), num(s.get("gross_expectancy_r"), 2, True),
        "&infin;" if pf is None else num(pf, 2), num(s.get("max_dd_r"), 1), num(s.get("avg_bars"), 1))


def backtest(D):
    B = D.get("backtest")
    S = D["scan"]
    h = ["<h2 class='sec'>Backtest</h2>"]
    if not B:
        h.append("<div class='card'><h2>No backtest yet</h2><p class='muted'>Run <code>screener.py backtest --strategy %s</code> and rescan: the results appear here.</p></div>" % e(S.get("strategy")))
        return "".join(h)
    w = B.get("with_funnel") or {}
    a = w.get("all") or {}
    oos = w.get("out_of_sample") or {}
    pf = B.get("portfolio_funnel") or {}
    bm = B.get("benchmark") or {}
    vd = B.get("verdict") or {}
    win = B.get("window") or ["?", "?"]
    h.append("<div class='card'><h2>Verdict %s</h2><div class='verdict'>%s</div><p class='small muted'>%s chart, %s to %s (%s sessions), %s stocks, %s signals. Filters: %s. Card version %s. "
             "Net of Indian charges and slippage; 1R = the money at risk from entry to stop.</p></div>" % (
                 badge(vd.get("tone", "neutral")), "<br>".join(e(x) for x in vd.get("lines") or []), e(B.get("timeframe")), e(win[0]), e(win[-1]),
                 e(B.get("sessions")), e(B.get("stocks")), e(B.get("signals")), e(", ".join(B.get("filters_on") or [])), e(B.get("card_hash"))))
    h.append("<div class='card'><div class='kpi'><div><b>%s</b><span>trades (with filters)</span></div><div><b>%s%%</b><span>win rate</span></div>"
             "<div><b class='%s'>%sR</b><span>expectancy, net</span></div><div><b class='%s'>%sR</b><span>out-of-sample (%s)</span></div>"
             "<div><b>%s%%</b><span>portfolio CAGR</span></div><div><b>%s%%</b><span>Nifty 500 CAGR</span></div><div><b class='neg'>%s%%</b><span>max drawdown</span></div></div></div>" % (
                 e(a.get("n") or 0), num(a.get("win_rate"), 1), cls(a.get("expectancy_r")), num(a.get("expectancy_r"), 2, True),
                 cls(oos.get("expectancy_r")), num(oos.get("expectancy_r"), 2, True), e(oos.get("n") or 0),
                 num(pf.get("cagr_pct"), 1), num(bm.get("cagr_pct"), 1), num(pf.get("max_drawdown_pct"), 1)))
    so = B.get("setup_only") or {}
    rows = [_srow("Rules alone", so.get("all")), _srow("Rules + filters", a), _srow("&nbsp; in-sample", w.get("in_sample")),
            _srow("&nbsp; out-of-sample" + (" (from %s)" % e(B["oos_from"]) if B.get("oos_from") else ""), oos)]
    rows += [_srow("Rules alone, %s market" % k, (so.get("by_regime") or {}).get(k)) for k in ("bullish", "neutral", "bearish")]
    rows += [_srow("&nbsp; " + e(y), s) for y, s in (w.get("by_year") or {}).items()]
    h.append("<div class='card'><h2>Per trade</h2><div class='wrap'><table><thead><tr><th class='l'>Slice</th><th>Trades</th><th>Win</th><th>Avg win</th><th>Avg loss</th>"
             "<th>Expectancy</th><th>Gross</th><th>PF</th><th>Max DD R</th><th>Bars</th></tr></thead><tbody>%s</tbody></table></div></div>" % "".join(rows))
    eq = ""
    cv, bc = pf.get("curve") or [], bm.get("curve") or []
    if len(cv) > 1 and len(bc) > 1 and cv[0][1] and bc[0][1]:
        eq = line_svg([("Strategy (100 start)", "eq-s", [(p[0], p[1] / cv[0][1] * 100) for p in cv]),
                       ("Nifty 500 (100 start)", "eq-b", [(p[0], p[1] / bc[0][1] * 100) for p in bc])])
    h.append("<div class='card'><h2>Portfolio with your limits vs the Nifty 500</h2>%s<p class='small muted'>&#8377;%s &rarr; &#8377;%s, %s trades taken, %s skipped for limits, "
             "in the market %s%% of days. Nifty 500 is the price index (no dividends).</p></div>" % (
                 eq, inr(pf.get("start_capital")), inr(pf.get("final_equity")), e(pf.get("trades")), e(pf.get("skipped_for_limits")), num(pf.get("time_in_market_pct"), 0)))
    mix = a.get("exit_mix") or {}
    if mix:
        h.append("<div class='card small'><h3>How trades ended</h3>%s</div>" % " &middot; ".join("%s %s" % (e(k), e(v)) for k, v in mix.items()))
    return "".join(h)


def forward(D):
    F = D.get("forward")
    S = D["scan"]
    h = ["<h2 class='sec'>Forward test</h2>"]
    if not F:
        h.append("<div class='card'><h2>No forward test running</h2><p class='muted'>Start one with <code>screener.py forward start --strategy %s</code>. "
                 "It freezes the card and paper-trades every new signal from then on, on bars nobody has seen yet.</p></div>" % e(S.get("strategy")))
        return "".join(h)
    s = F.get("with_filters") or {}
    ref = F.get("backtest_ref") or {}
    h.append("<div class='card'><h2>Forward test since %s</h2>%s<div class='kpi'><div><b>%s</b><span>sessions</span></div><div><b>%s</b><span>signals</span></div>"
             "<div><b>%s</b><span>closed trades</span></div><div><b>%s</b><span>open now (%sR)</span></div><div><b class='%s'>%s</b><span>forward expectancy</span></div>"
             "<div><b>%s</b><span>backtest out-of-sample</span></div></div><p class='small muted'>%s%s</p></div>" % (
                 e((F.get("meta") or {}).get("start")),
                 "<p class='neg small'>The card was edited after this test started. This test keeps running the frozen version.</p>" if F.get("card_changed") else "",
                 e(F.get("sessions") or 0), e(F.get("signals") or 0), e(s.get("n") or 0), e(F.get("open_trades") or 0), num(F.get("open_r"), 2, True),
                 cls(s.get("expectancy_r")), (num(s.get("expectancy_r"), 2, True) + "R") if s.get("n") else "-",
                 (num((ref.get("out_of_sample") or {}).get("expectancy_r"), 2, True) + "R") if ref else "-",
                 ("Only %s closed trades: too few to judge. Keep it running. " % s["n"]) if s.get("n") and s["n"] < 30 else "", e(F.get("note") or "")))
    rows = [t for t in F.get("trades") or [] if t.get("in_funnel")]
    if rows:
        body = "".join("<tr><td class='l'><b>%s</b></td><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='l'>%s</td><td>%s</td><td class='l'>%s</td><td class='%s'>%s</td></tr>" % (
            e(t.get("symbol")), e(t.get("signal_date")), inr(t.get("entry"), 2), inr(t.get("stop0"), 2), inr(t.get("target"), 2) if t.get("target") else "-",
            "open" if t.get("open") else e(t.get("exit_date")), inr(t.get("exit"), 2), e(t.get("exit_reason")), cls(t.get("r_net")), num(t.get("r_net"), 2, True))
            for t in reversed(rows))
        h.append("<div class='card'><h2>Paper trades</h2><div class='wrap'><table><thead><tr><th class='l'>Symbol</th><th class='l'>Signal</th><th>Entry</th><th>Stop</th><th>Target</th>"
                 "<th class='l'>Exit</th><th>Exit &#8377;</th><th class='l'>How</th><th>R net</th></tr></thead><tbody>%s</tbody></table></div></div>" % body)
    else:
        h.append("<div class='card'><h2>Paper trades</h2><p class='muted'>No trade has filled yet that passed your filters.</p></div>")
    return "".join(h)


def strategy(D):
    return ("<h2 class='sec'>Strategy</h2><div class='card'><h2>The strategy in plain words</h2><pre>%s</pre></div>"
            "<div class='card'><details><summary>Strategy card (JSON, shareable)</summary><pre>%s</pre></details></div>"
            % (e(D.get("card_words")), e(json.dumps(D.get("card"), indent=2, default=str))))


def render(D):
    """{"h1", "sub", "nav", "screener", "backtest", "forward", "strategy"} as HTML strings."""
    out = {}
    out["h1"], out["sub"] = header(D["scan"])
    out["nav"] = nav()
    for k, f in (("screener", screener), ("backtest", backtest), ("forward", forward), ("strategy", strategy)):
        try:
            out[k] = f(D)
        except Exception as ex:  # noqa: BLE001  one broken section must never blank the page
            out[k] = "<div class='card'><p class='neg'>This section could not be drawn (%s). The data is in scan.json.</p></div>" % e(ex)
    return out
