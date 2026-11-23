"""Panel: the whole market on every date, so each funnel layer can be computed for any day.

Used two ways with the SAME code:
  * the live funnel looks at the last date (or --asof),
  * the backtester asks "would this stock have passed the funnel on that day?"

Layer 1  regime(m)          Nifty 500 trend + breadth + India VIX -> bullish / neutral / bearish
Layer 2  sectors(m)         sectors ranked by relative strength against the Nifty 500, built bottom-up
Layer 3  eligible(...)      liquidity, price, affordability, volatility, gap behaviour (+ NSE lists live)
Layer 4  rs rank            1-99 percentile of a 1/3/6-month momentum score across the universe
"""

import math
import statistics

from .bars import NAN, History, isnan, sma_list, ema_list, wilder_list, true_range_list, pivots

RS_WEIGHTS = ((21, 0.2), (63, 0.4), (126, 0.4))     # 1m, 3m, 6m
SECTOR_WEIGHTS = ((5, 0.2), (21, 0.3), (63, 0.5))   # 1w, 1m, 3m


def pct(a, b):
    return (a / b - 1) * 100 if b and not isnan(a) and not isnan(b) else NAN


class Panel:
    def __init__(self, universe, price_docs, index_docs, min_bars=150):
        """universe: list of stock dicts; price_docs: {symbol: {t,o,h,l,c,v}} (already cut at as-of);
        index_docs: {"NIFTY500": doc, "NIFTY50": doc, "SENSEX": doc, "INDIAVIX": doc}."""
        idx = index_docs["NIFTY500"]
        self.dates = list(idx["t"])
        self.M = len(self.dates)
        self.mpos = {d: m for m, d in enumerate(self.dates)}
        self.idx = idx
        self.idx_c = idx["c"]
        self.idx_sma50 = sma_list(self.idx_c, 50)
        self.idx_sma200 = sma_list(self.idx_c, 200)
        self.index_docs = index_docs
        vix = index_docs.get("INDIAVIX")
        self.vix = self._align(vix) if vix else [NAN] * self.M
        self.meta = {s["symbol"]: s for s in universe}
        self.hist = {}
        self.no_data = []
        for s in universe:
            doc = price_docs.get(s["symbol"])
            if not doc or len(doc.get("t", [])) < 2:
                self.no_data.append(s["symbol"])
                continue
            ic = [self.idx_c[self.mpos[d]] if d in self.mpos else NAN for d in doc["t"]]
            self.hist[s["symbol"]] = History(s["symbol"], doc["t"], doc["o"], doc["h"], doc["l"], doc["c"],
                                             doc["v"], meta=s, index_close=ic)
        self.min_bars = min_bars
        self._build()

    def _align(self, doc):
        pos = {d: i for i, d in enumerate(doc["t"])}
        out, last = [], NAN
        for d in self.dates:
            i = pos.get(d)
            if i is not None:
                last = doc["c"][i]
            out.append(last)
        return out

    # ------------------------------------------------------------ precompute per date
    def _build(self):
        M = self.M
        self.jmap = {}            # symbol -> list len M: stock bar index on that date (or -1)
        above50 = [0] * M
        above200 = [0] * M
        n50 = [0] * M
        n200 = [0] * M
        adv = [0] * M
        dec = [0] * M
        hi52 = [0] * M
        lo52 = [0] * M
        scores = [[] for _ in range(M)]
        self.rets = {}            # symbol -> {n: list len M}
        for sym, h in self.hist.items():
            jm = [-1] * M
            for j, d in enumerate(h.dates):
                m = self.mpos.get(d)
                if m is not None:
                    jm[m] = j
            self.jmap[sym] = jm
            c = h.c
            s50 = h.cached(("sma", 50, "close"), lambda: sma_list(c, 50))
            s200 = h.cached(("sma", 200, "close"), lambda: sma_list(c, 200))
            r = {n: [NAN] * M for n in (5, 21, 63, 126)}
            for m in range(M):
                j = jm[m]
                if j < 0:
                    continue
                if not isnan(s50[j]):
                    n50[m] += 1
                    above50[m] += c[j] > s50[j]
                if not isnan(s200[j]):
                    n200[m] += 1
                    above200[m] += c[j] > s200[j]
                if j >= 1:
                    if c[j] > c[j - 1]:
                        adv[m] += 1
                    elif c[j] < c[j - 1]:
                        dec[m] += 1
                if j >= 250:
                    w = c[j - 250:j]
                    if c[j] > max(w):
                        hi52[m] += 1
                    elif c[j] < min(w):
                        lo52[m] += 1
                for n in r:
                    if j >= n:
                        r[n][m] = pct(c[j], c[j - n])
                if j >= 126:
                    sc = sum(wt * r[n][m] for n, wt in RS_WEIGHTS)
                    scores[m].append((sc, sym))
            self.rets[sym] = r
        self.breadth50 = [above50[m] / n50[m] * 100 if n50[m] else NAN for m in range(M)]
        self.breadth200 = [above200[m] / n200[m] * 100 if n200[m] else NAN for m in range(M)]
        self.adv, self.dec, self.hi52, self.lo52 = adv, dec, hi52, lo52
        # RS percentile per date, written into each History so setups can read b.rs_rank
        self.rs = {sym: [NAN] * M for sym in self.hist}
        for m in range(M):
            lst = sorted(scores[m])
            k = len(lst)
            for rank, (_, sym) in enumerate(lst):
                self.rs[sym][m] = 1 + int(98 * rank / (k - 1)) if k > 1 else 50
        for sym, h in self.hist.items():
            jm = self.jmap[sym]
            arr = [NAN] * len(h.c)
            for m, j in enumerate(jm):
                if j >= 0:
                    arr[j] = self.rs[sym][m]
            h.rs_rank = arr
        # index returns for relative strength
        self.idx_ret = {n: [pct(self.idx_c[m], self.idx_c[m - n]) if m >= n else NAN for m in range(M)]
                        for n in (5, 21, 63, 126)}
        self._sector_cache = {}

    # ------------------------------------------------------------ layer 1: regime
    def regime(self, m, cfg):
        c, s50, s200 = self.idx_c[m], self.idx_sma50[m], self.idx_sma200[m]
        s50_prev = self.idx_sma50[m - 10] if m >= 10 else NAN
        b50, b200, vix = self.breadth50[m], self.breadth200[m], self.vix[m]
        rising = not isnan(s50_prev) and s50 > s50_prev
        falling = not isnan(s50_prev) and s50 < s50_prev
        facts = []
        if isnan(s200):
            state = "neutral"
            facts.append("not enough index history for a 200-day average")
        elif c > s200 and c > s50 and rising and b50 >= cfg.get("bull_breadth", 50):
            state = "bullish"
        elif c < s200 and (falling or b50 < cfg.get("bear_breadth", 40)):
            state = "bearish"
        else:
            state = "neutral"
        facts.append("Nifty 500 %s its 200-day average (%s vs %s)" % ("above" if c > s200 else "below",
                                                                     fmt(c), fmt(s200)))
        facts.append("%s its 50-day average, which is %s" % ("above" if c > s50 else "below",
                                                            "rising" if rising else "falling" if falling else "flat"))
        facts.append("%.0f%% of stocks above their 50-day average, %.0f%% above their 200-day" % (b50, b200))
        structure = self.index_structure(m)
        facts.append("swing structure: %s" % structure)
        mult = cfg.get("regime_risk", {}).get(state, {"bullish": 1.0, "neutral": 0.5, "bearish": 0.0}[state])
        vix_high = cfg.get("vix_high", 22)
        hv = not isnan(vix) and vix >= vix_high
        if hv and mult > 0:
            mult *= 0.5
            facts.append("India VIX %.1f is at or above %s: risk halved" % (vix, vix_high))
        elif not isnan(vix):
            facts.append("India VIX %.1f" % vix)
        label = {"bullish": "Bullish", "neutral": "Sideways / neutral", "bearish": "Bearish"}[state]
        return {"state": state, "label": label, "risk_multiplier": round(mult, 3), "date": self.dates[m], "close": c,
                "sma50": s50, "sma200": s200, "sma50_rising": rising, "breadth50": b50, "breadth200": b200,
                "vix": vix, "high_vix": hv, "structure": structure, "advancers": self.adv[m],
                "decliners": self.dec[m], "new_highs": self.hi52[m], "new_lows": self.lo52[m], "facts": facts}

    def index_structure(self, m):
        if not hasattr(self, "_idx_piv"):
            ph, pl = pivots(self.idx["h"], self.idx["l"], 5, 5)   # stored at confirmation bar: causal
            self._idx_piv = (sorted(ph.items()), sorted(pl.items()))
        highs = [p for k, p in self._idx_piv[0] if k <= m][-2:]
        lows = [p for k, p in self._idx_piv[1] if k <= m][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "not enough swings"
        hh, hl = highs[1][1] > highs[0][1], lows[1][1] > lows[0][1]
        if hh and hl:
            return "higher highs and higher lows"
        if not hh and not hl:
            return "lower highs and lower lows"
        return "mixed (%s high, %s low)" % ("higher" if hh else "lower", "higher" if hl else "lower")

    # ------------------------------------------------------------ layer 2: sectors
    def sectors(self, m, min_members=5):
        if m in self._sector_cache:
            return self._sector_cache[m]
        groups = {}
        for sym, h in self.hist.items():
            j = self.jmap[sym][m]
            if j < 63:
                continue
            groups.setdefault(h.meta.get("sector") or "Other", []).append(sym)
        rows = []
        for sec, syms in groups.items():
            if len(syms) < min_members:
                continue
            row = {"sector": sec, "members": len(syms)}
            score = 0.0
            for n, wt in SECTOR_WEIGHTS:
                vals = [self.rets[s][n][m] for s in syms if not isnan(self.rets[s][n][m])]
                med = statistics.median(vals) if vals else NAN
                ex = med - self.idx_ret[n][m] if not isnan(med) else NAN
                row["ret_%d" % n] = med
                row["ex_%d" % n] = ex
                score += wt * (ex if not isnan(ex) else 0)
            row["score"] = score
            above = 0
            for s in syms:
                h = self.hist[s]
                j = self.jmap[s][m]
                s50 = h.cached(("sma", 50, "close"), lambda: sma_list(h.c, 50))[j]
                above += (not isnan(s50) and h.c[j] > s50)
            row["breadth50"] = above / len(syms) * 100
            # participation: how many members beat the index over 1 month (a few heavyweights vs most stocks)
            beat = [self.rets[s_][21][m] > self.idx_ret[21][m] for s_ in syms if not isnan(self.rets[s_][21][m])]
            row["participation"] = sum(beat) / len(beat) * 100 if beat else NAN
            # 6-month relative strength and multi-period consistency
            v6 = [self.rets[s_][126][m] for s_ in syms if not isnan(self.rets[s_][126][m])]
            row["ex_126"] = statistics.median(v6) - self.idx_ret[126][m] if v6 and not isnan(self.idx_ret[126][m]) else NAN
            row["consistent"] = all(not isnan(row.get("ex_%d" % n, NAN)) and row["ex_%d" % n] > 0 for n in (5, 21, 63, 126))
            # volume expansion: median of (10-day average volume / 50-day average volume)
            vr = []
            for s_ in syms:
                h = self.hist[s_]
                j = self.jmap[s_][m]
                if j >= 50:
                    a50 = sum(h.v[j - 49:j + 1]) / 50
                    if a50 > 0:
                        vr.append(sum(h.v[j - 9:j + 1]) / 10 / a50)
            row["vol_ratio"] = statistics.median(vr) if vr else NAN
            rows.append(row)
        rows.sort(key=lambda r: -r["score"])
        for k, r in enumerate(rows, 1):
            r["rank"] = k
        self._sector_cache[m] = rows
        return rows

    def top_sectors(self, m, n):
        """The top n sectors that are actually beating the Nifty 500 (score > 0)."""
        return [r["sector"] for r in self.sectors(m)[:n] if r["score"] > 0]

    # ------------------------------------------------------------ per-stock facts on a date
    def stock_facts(self, sym, m):
        h = self.hist[sym]
        j = self.jmap[sym][m]
        if j < 0:
            return None
        c = h.c
        atr = h.cached(("atr", 14), lambda: wilder_list(true_range_list(h.h, h.l, h.c), 14))
        s50 = h.cached(("sma", 50, "close"), lambda: sma_list(c, 50))
        s200 = h.cached(("sma", 200, "close"), lambda: sma_list(c, 200))
        e20 = h.cached(("ema", 20, "close"), lambda: ema_list(c, 20))
        tv = [c[k] * h.v[k] for k in range(max(0, j - 19), j + 1)]
        tv_med = statistics.median(tv) / 1e7 if tv else NAN       # rupee crore
        gaps = 0
        for k in range(max(1, j - 59), j + 1):
            if abs(h.o[k] / c[k - 1] - 1) * 100 >= 4.0:
                gaps += 1
        return {"j": j, "close": c[j], "atr": atr[j], "atr_pct": atr[j] / c[j] * 100 if not isnan(atr[j]) else NAN,
                "sma50": s50[j], "sma200": s200[j], "ema20": e20[j], "tv_cr": tv_med, "big_gaps_60d": gaps,
                "bars": j + 1, "rs_rank": self.rs[sym][m],
                "ret_21": self.rets[sym][21][m], "ret_63": self.rets[sym][63][m], "ret_126": self.rets[sym][126][m],
                "ex_63": self.rets[sym][63][m] - self.idx_ret[63][m] if not isnan(self.rets[sym][63][m]) else NAN}

    # ------------------------------------------------------------ layers 3 + 4 for one stock
    def screen(self, sym, m, f, profile, extras=None, top=None):
        """One stock through layers 2-4, in the order the video tells it:
        strong sector first, then "is it tradable for me?", then "is the stock itself strong?".
        Returns (last_layer_passed, reason, facts): 'universe' (failed the sector layer), 'sector'
        (failed eligibility), 'eligible' (failed RS / trend), or 'watchlist' with reason None."""
        meta = self.meta[sym]
        fx = self.stock_facts(sym, m)
        if fx is None:
            return "universe", "no price on this date", None
        if fx["bars"] < self.min_bars:
            return "universe", "less than %d sessions of history" % self.min_bars, fx
        # ---- layer 2 applied to the stock: in a leading sector, or an RS leader itself
        top = top if top is not None else self.top_sectors(m, f.get("top_sectors", 3))
        leader = not isnan(fx["rs_rank"]) and fx["rs_rank"] >= f.get("leader_rs", 95)
        if f.get("top_sectors", 3) > 0 and meta.get("sector") not in top and not leader:   # 0 = layer off
            return "universe", "not in a leading sector", fx
        # ---- layer 3: eligibility (tradable for this trader)
        caps = f.get("caps", ["large", "mid", "small"])
        if meta.get("cap") and caps and meta.get("cap") not in caps:
            return "sector", "%s-cap (excluded)" % meta.get("cap"), fx
        if meta.get("series", "EQ") != "EQ":
            return "sector", "series %s (trade-to-trade)" % meta.get("series"), fx
        if extras:
            sv = extras.get("surveillance", {}).get(sym)
            if sv and f.get("exclude_surveillance", True):
                return "sector", "under NSE surveillance: %s" % sv, fx
            if meta.get("band") in [str(b) for b in f.get("exclude_bands", ["2", "5"])]:
                return "sector", "%s%% price band (stop may not fill in a circuit)" % meta.get("band"), fx
        if fx["close"] < f.get("min_price", 50):
            return "sector", "price below Rs %s" % f.get("min_price", 50), fx
        if isnan(fx["tv_cr"]) or fx["tv_cr"] < f.get("min_traded_value_cr", 10):
            return "sector", "median traded value under Rs %s cr a day" % f.get("min_traded_value_cr", 10), fx
        ceiling = profile.get("price_ceiling")
        if ceiling and fx["close"] > ceiling:
            return "sector", "price above your Rs %s ceiling" % fmt(ceiling), fx
        cap = profile.get("capital", 0) * profile.get("max_position_pct", 25) / 100
        if cap and fx["close"] > cap:
            return "sector", "one share costs more than your max position (Rs %s)" % fmt(cap), fx
        if isnan(fx["atr_pct"]) or fx["atr_pct"] < f.get("atr_pct_min", 1.0):
            return "sector", "too quiet: ATR under %s%% of price" % f.get("atr_pct_min", 1.0), fx
        if fx["atr_pct"] > f.get("atr_pct_max", 6.0):
            return "sector", "too wild: ATR over %s%% of price" % f.get("atr_pct_max", 6.0), fx
        if fx["big_gaps_60d"] > f.get("max_big_gaps", 3):
            return "sector", "%d gaps of 4%%+ in 60 sessions" % fx["big_gaps_60d"], fx
        # ---- layer 4: stock relative strength and trend
        if isnan(fx["rs_rank"]) or fx["rs_rank"] < f.get("min_rs", 70):
            return "eligible", "RS rank %s under %s" % ("n/a" if isnan(fx["rs_rank"]) else int(fx["rs_rank"]),
                                                        f.get("min_rs", 70)), fx
        trend = f.get("trend", "above200")
        if trend == "above200" and not (fx["close"] > fx["sma200"]):
            return "eligible", "below its 200-day average", fx
        if trend == "stage2" and not (fx["close"] > fx["sma50"] > fx["sma200"]):
            return "eligible", "not in a stage-2 uptrend (close > 50 DMA > 200 DMA)", fx
        return "watchlist", None, fx


def fmt(x, dec=0):
    if x is None or isnan(x):
        return "n/a"
    x = float(x)
    neg = x < 0
    x = abs(x)
    s = ("%." + str(dec) + "f") % x
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")
