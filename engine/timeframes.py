"""Timeframes: one stock's bars on the strategy's chart (5m, 15m, 1h, 2h, 1d or 1w), plus maps from each
bar to the last COMPLETED bar of every higher timeframe, so a rule like weekly(close > sma(30)) on a
daily chart, or daily(close > sma(200)) on a 15-minute chart, never sees a bar that has not closed.

    base 1d      daily()  = itself            weekly() = last completed week (the week ends on its last session)
    base 1w      weekly() = itself
    base 5m/15m  hourly() = last completed hour of the session (9:15-10:15, ... 15:15-15:30)
    intraday     daily()  = the PREVIOUS session's daily bar (today's daily bar closes at 15:30)
                 weekly() = the last week completed before that

The funnel filters (regime, sectors, liquidity, RS) are daily facts and use the same daily map.
"""

import bisect
import datetime as dt

from . import data as D
from .bars import NAN, History

OPEN = D.SESSION_OPEN      # 9:15 in minutes
CLOSE = D.SESSION_CLOSE    # 15:30


def _mins(t):
    return int(t[11:13]) * 60 + int(t[14:16])


def _date(t):
    return dt.date(int(t[:4]), int(t[5:7]), int(t[8:10]))


def _group(doc, keys):
    """Group consecutive bars with equal keys. Returns (agg doc, group_of list, last member index list)."""
    out = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    group_of, last_member = [], []
    prev = object()
    for j, k in enumerate(keys):
        if k != prev:
            out["t"].append(doc["t"][j])
            out["o"].append(doc["o"][j])
            out["h"].append(doc["h"][j])
            out["l"].append(doc["l"][j])
            out["c"].append(doc["c"][j])
            out["v"].append(doc["v"][j])
            last_member.append(j)
            prev = k
        else:
            g = len(out["t"]) - 1
            out["h"][g] = max(out["h"][g], doc["h"][j])
            out["l"][g] = min(out["l"][g], doc["l"][j])
            out["c"][g] = doc["c"][j]
            out["v"][g] += doc["v"][j]
            last_member[g] = j
        group_of.append(len(out["t"]) - 1)
    return out, group_of, last_member


def weekly(doc, today=None):
    """Weekly bars from completed daily bars (t = the week's first session). Returns
    (weekly doc, completed-week map for every daily bar, last daily index of every week, complete flags)."""
    if not doc or not doc.get("t"):
        return None, [], [], []
    keys = [_date(t).isocalendar()[:2] for t in doc["t"]]
    agg, group_of, last_member = _group(doc, keys)
    today = today or D.now_ist().date()
    G = len(agg["t"])
    complete = [True] * G
    if G:
        ld = _date(doc["t"][last_member[-1]])
        if not (ld.weekday() == 4 or today.isocalendar()[:2] > ld.isocalendar()[:2]):
            complete[-1] = False
    wmap = []
    for j, g in enumerate(group_of):
        wmap.append(g if (j == last_member[g] and complete[g]) else g - 1)
    return agg, wmap, last_member, complete


def intraday_agg(doc, base_min, target_min):
    """Coarser intraday bars (e.g. 1h from 5m, 2h from 1h), anchored at 9:15 each session.
    Returns (agg doc, completed-group map per base bar, complete flags)."""
    keys = [(t[:10], (_mins(t) - OPEN) // target_min) for t in doc["t"]]
    agg, group_of, last_member = _group(doc, keys)
    n, G = len(doc["t"]), len(agg["t"])
    complete = []
    for g in range(G):
        j = last_member[g]
        end = _mins(doc["t"][j]) + base_min
        aligned = (end - OPEN) % target_min == 0 or end >= CLOSE
        complete.append(aligned or j < n - 1)
    gmap = []
    for j, g in enumerate(group_of):
        end = _mins(doc["t"][j]) + base_min
        done_here = j == last_member[g] and complete[g] and ((end - OPEN) % target_min == 0 or end >= CLOSE
                                                             or j < n - 1)
        gmap.append(g if done_here else g - 1)
    return agg, gmap, complete


def _hist(sym, doc, meta, tf, rs=None, ic=None):
    h = History(sym, doc["t"], doc["o"], doc["h"], doc["l"], doc["c"], doc["v"], meta=meta,
                index_close=ic, rs_rank=rs)
    h.tf = tf
    h.higher = {}
    return h


def _trim_complete(doc, complete):
    """Drop a trailing incomplete group from an aggregated doc."""
    if complete and not complete[-1]:
        return {k: doc[k][:-1] for k in "tohlcv"}
    return doc


def build(p, panel, sym, tf, asof=None, today=None, need_hourly=False):
    """The History a strategy runs on for one stock, with .tf, .higher {tf: (History, map)} and
    .dmap (panel daily index of the last completed daily bar at each bar, -1 if none)."""
    daily = panel.hist.get(sym)
    if daily is None:
        return None
    meta = daily.meta
    today = today or (_date(asof) if asof else D.now_ist().date())
    if not hasattr(daily, "tf"):
        daily.tf = "1d"
        daily.higher = {}
    # weekly from daily (cached on the daily History)
    wk = daily._cache.get(("weekly", today))
    if wk is None:
        wdoc, wmap, wlast, wcomp = weekly({"t": daily.dates, "o": daily.o, "h": daily.h, "l": daily.l,
                                           "c": daily.c, "v": daily.v}, today)
        wdoc_c = _trim_complete(wdoc, wcomp)
        # weekly RS and Nifty 500 close at each week's last session
        rs_w = [daily.rs_rank[wlast[g]] for g in range(len(wdoc_c["t"]))]
        ic_w = [daily.index_close[wlast[g]] for g in range(len(wdoc_c["t"]))]
        wh = _hist(sym, wdoc_c, meta, "1w", rs_w, ic_w)
        wh.higher["1w"] = (wh, None)
        wh.week_last_daily = wlast[:len(wdoc_c["t"])]
        wk = (wh, wmap)
        daily._cache[("weekly", today)] = wk
    wh, wmap = wk
    daily.higher = {"1d": (daily, None), "1w": (wh, wmap)}     # daily filter rules can use weekly()
    if not hasattr(daily, "dmap"):
        daily.dmap = [panel.mpos.get(d, -1) for d in daily.dates]
    day_pos = {d: j for j, d in enumerate(daily.dates)}
    if tf == "1d":
        return daily
    if tf == "1w":
        wh.dmap = [panel.mpos.get(daily.dates[j], -1) for j in wh.week_last_daily]
        wh.higher = {"1w": (wh, None)}
        return wh
    # ---- intraday
    if tf == "2h":
        src = D.completed_intraday(D.load_intraday(p, "1h", sym), 60, asof)
        if not src or not src.get("t"):
            return None
        agg, _, comp = intraday_agg(src, 60, 120)
        doc = _trim_complete(agg, comp)
        base_min = 120
    else:
        doc = D.completed_intraday(D.load_intraday(p, tf, sym), D.INTRADAY[tf][2], asof)
        if not doc or not doc.get("t"):
            return None
        base_min = D.INTRADAY[tf][2]
    # daily index (stock's own and panel's) of the last session BEFORE each bar's date
    ddates = daily.dates
    sd_map, pm_map = [], []
    cache = {}
    for t in doc["t"]:
        d = t[:10]
        if d not in cache:
            k = bisect.bisect_left(ddates, d) - 1
            m = bisect.bisect_left(panel.dates, d) - 1
            cache[d] = (k, m)
        k, m = cache[d]
        sd_map.append(k)
        pm_map.append(m)
    rs = [panel.rs[sym][m] if m >= 0 else NAN for m in pm_map]
    h = _hist(sym, doc, meta, tf, rs, None)
    h.dmap = pm_map
    h.higher = {"1d": (daily, sd_map), "1w": (wh, [wmap[k] if k >= 0 else -1 for k in sd_map])}
    if tf in ("5m", "15m") or need_hourly:
        if tf in ("5m", "15m"):
            agg, gmap, comp = intraday_agg(doc, base_min, 60)
            hh = _hist(sym, agg, meta, "1h")
            h.higher["1h"] = (hh, gmap)
        elif tf == "1h":
            h.higher["1h"] = (h, None)
    if tf == "1h":
        h.higher["1h"] = (h, None)
    h.day_pos = day_pos
    return h
