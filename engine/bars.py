"""Bars: the one object a setup sees.

A setup's entry(b) and exit(b, trade) functions get a Bars view of ONE stock at ONE
completed daily bar. Indexing works like TradingView's Pine Script:

    b.close[0]   the bar that just closed (today, or the as-of day)
    b.close[1]   the bar before it
    b.close[-1]  ERROR: that is the future. Raises LookaheadError.

Every indicator is computed causally (bar i only uses bars 0..i) and returns a
Series with the same indexing. A value that does not exist yet (not enough
history) is float('nan'); any comparison with nan is False, so a rule simply
does not fire during warm-up.

Pure Python, no numpy or pandas, so the skill runs anywhere Python 3.8+ runs.
"""

import math

NAN = float("nan")


class LookaheadError(Exception):
    """A setup asked for a bar after the current one."""


def isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


class Series:
    """A read-only view of a full-length list, anchored at bar i. s[k] is k bars ago."""

    __slots__ = ("_a", "_i")

    def __init__(self, arr, i):
        self._a = arr
        self._i = i

    def __getitem__(self, k):
        if not isinstance(k, int):
            raise TypeError("Use an integer bars-ago index, e.g. close[0] or close[3]")
        if k < 0:
            raise LookaheadError("Series[%d] would look %d bar(s) into the future" % (k, -k))
        j = self._i - k
        if j < 0:
            return NAN
        v = self._a[j]
        return NAN if v is None else v

    def last(self, n):
        """The last n values, oldest first (includes the current bar)."""
        return [self[k] for k in range(n - 1, -1, -1)]

    def __len__(self):
        return self._i + 1

    def __repr__(self):
        return "Series(now=%r)" % (self[0],)


# ---------------------------------------------------------------- causal indicator math

def sma_list(src, n):
    out = [NAN] * len(src)
    s, cnt, q = 0.0, 0, []
    for i, v in enumerate(src):
        if isnan(v):
            s, cnt, q = 0.0, 0, []
            continue
        q.append(v)
        s += v
        if len(q) > n:
            s -= q.pop(0)
        if len(q) == n:
            out[i] = s / n
    return out


def ema_list(src, n):
    out = [NAN] * len(src)
    k = 2.0 / (n + 1)
    prev = None
    seed = []
    for i, v in enumerate(src):
        if isnan(v):
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def wilder_list(src, n):
    """Wilder's smoothing (RMA), seeded with a simple average."""
    out = [NAN] * len(src)
    prev = None
    seed = []
    for i, v in enumerate(src):
        if isnan(v):
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = (prev * (n - 1) + v) / n
        out[i] = prev
    return out


def true_range_list(h, l, c):
    out = [NAN] * len(c)
    for i in range(len(c)):
        if i == 0:
            out[i] = h[i] - l[i]
        else:
            out[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return out


def rsi_list(c, n=14):
    gains = [NAN] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    losses = [NAN] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    ag, al = wilder_list(gains, n), wilder_list(losses, n)
    out = [NAN] * len(c)
    for i in range(len(c)):
        if isnan(ag[i]) or isnan(al[i]):
            continue
        out[i] = 100.0 if al[i] == 0 else 100.0 - 100.0 / (1 + ag[i] / al[i])
    return out


def rolling_max(src, n):
    out = [NAN] * len(src)
    for i in range(n - 1, len(src)):
        out[i] = max(src[i - n + 1:i + 1])
    return out


def rolling_min(src, n):
    out = [NAN] * len(src)
    for i in range(n - 1, len(src)):
        out[i] = min(src[i - n + 1:i + 1])
    return out


def stdev_list(src, n):
    out = [NAN] * len(src)
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return out


def adx_lists(h, l, c, n=14):
    pdm = [NAN] * len(c)
    mdm = [NAN] * len(c)
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    tr = true_range_list(h, l, c)
    tr[0] = NAN
    atr, sp, sm = wilder_list(tr, n), wilder_list(pdm, n), wilder_list(mdm, n)
    pdi, mdi, dx = [NAN] * len(c), [NAN] * len(c), [NAN] * len(c)
    for i in range(len(c)):
        if isnan(atr[i]) or atr[i] == 0 or isnan(sp[i]) or isnan(sm[i]):
            continue
        pdi[i] = 100 * sp[i] / atr[i]
        mdi[i] = 100 * sm[i] / atr[i]
        s = pdi[i] + mdi[i]
        dx[i] = 0.0 if s == 0 else 100 * abs(pdi[i] - mdi[i]) / s
    return wilder_list(dx, n), pdi, mdi


def supertrend_lists(h, l, c, n=10, mult=3.0):
    """Returns (line, direction) with direction +1 (uptrend) or -1 (downtrend)."""
    tr = true_range_list(h, l, c)
    atr = wilder_list(tr, n)
    line, dirn = [NAN] * len(c), [NAN] * len(c)
    fu = fl = None
    d = 1
    for i in range(len(c)):
        if isnan(atr[i]):
            continue
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * atr[i], mid - mult * atr[i]
        if fu is None:
            fu, fl = bu, bl
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
        if d == 1 and c[i] < fl:
            d = -1
        elif d == -1 and c[i] > fu:
            d = 1
        dirn[i] = d
        line[i] = fl if d == 1 else fu
    return line, dirn


def pivots(src_high, src_low, left, right):
    """Confirmed pivot highs and lows. A pivot at bar j is known only at bar j+right,
    so we store it at the confirmation bar: (conf_index -> (pivot_index, price))."""
    ph, pl = {}, {}
    n = len(src_high)
    for j in range(left, n - right):
        hv, lv = src_high[j], src_low[j]
        win_h = src_high[j - left:j + right + 1]
        win_l = src_low[j - left:j + right + 1]
        if hv == max(win_h) and win_h.count(hv) == 1:
            ph[j + right] = (j, hv)
        if lv == min(win_l) and win_l.count(lv) == 1:
            pl[j + right] = (j, lv)
    return ph, pl


# ---------------------------------------------------------------- per-stock history

class History:
    """Full daily history of one symbol plus an indicator cache."""

    def __init__(self, symbol, dates, o, h, l, c, v, meta=None, index_close=None, rs_rank=None):
        self.symbol = symbol
        self.dates = dates
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v
        self.meta = meta or {}
        self.index_close = index_close or [NAN] * len(c)   # Nifty 500 close aligned to these dates
        self.rs_rank = rs_rank or [NAN] * len(c)           # 1-99 percentile, aligned
        self._cache = {}
        self._pos = {d: i for i, d in enumerate(dates)}

    def __len__(self):
        return len(self.c)

    def index_of(self, date):
        return self._pos.get(date)

    def src(self, name):
        return {"close": self.c, "open": self.o, "high": self.h, "low": self.l, "volume": self.v,
                "hl2": self.cached(("hl2",), lambda: [(a + b) / 2 for a, b in zip(self.h, self.l)]),
                "hlc3": self.cached(("hlc3",), lambda: [(a + b + x) / 3 for a, b, x in zip(self.h, self.l, self.c)]),
                "traded_value": self.cached(("tv",), lambda: [x * y for x, y in zip(self.c, self.v)]),
                }[name]

    def cached(self, key, fn):
        v = self._cache.get(key)
        if v is None:
            v = fn()
            self._cache[key] = v
        return v

    def bars(self, i, ctx=None):
        return Bars(self, i, ctx)


# ---------------------------------------------------------------- the view a setup gets

class Bars:
    """One stock at one completed bar. See the module docstring and references/setup-api.md."""

    def __init__(self, hist, i, ctx=None):
        self._h = hist
        self.i = i
        self.symbol = hist.symbol
        self.meta = hist.meta                 # name, industry (sector), cap, series, band
        self.ctx = ctx or {}                  # extra facts from the funnel (regime, sector_rank...)
        self.open = Series(hist.o, i)
        self.high = Series(hist.h, i)
        self.low = Series(hist.l, i)
        self.close = Series(hist.c, i)
        self.volume = Series(hist.v, i)
        self.date = Series(hist.dates, i)
        self.index_close = Series(hist.index_close, i)
        self.rs_rank = Series(hist.rs_rank, i)

    @property
    def bars_available(self):
        return self.i + 1

    def _s(self, key, fn):
        return Series(self._h.cached(key, fn), self.i)

    # ---- moving averages and oscillators
    def sma(self, n, src="close"):
        return self._s(("sma", n, src), lambda: sma_list(self._h.src(src), n))

    def ema(self, n, src="close"):
        return self._s(("ema", n, src), lambda: ema_list(self._h.src(src), n))

    def rsi(self, n=14, src="close"):
        return self._s(("rsi", n, src), lambda: rsi_list(self._h.src(src), n))

    def atr(self, n=14):
        h = self._h
        return self._s(("atr", n), lambda: wilder_list(true_range_list(h.h, h.l, h.c), n))

    def atr_pct(self, n=14):
        h = self._h
        a = h.cached(("atr", n), lambda: wilder_list(true_range_list(h.h, h.l, h.c), n))
        return self._s(("atrp", n), lambda: [x / y * 100 if not isnan(x) and y else NAN for x, y in zip(a, h.c)])

    def macd(self, fast=12, slow=26, signal=9):
        """-> (macd_line, signal_line, histogram) Series."""
        h = self._h

        def build():
            f, s = ema_list(h.c, fast), ema_list(h.c, slow)
            m = [a - b if not (isnan(a) or isnan(b)) else NAN for a, b in zip(f, s)]
            sg = ema_list(m, signal)
            hist = [a - b if not (isnan(a) or isnan(b)) else NAN for a, b in zip(m, sg)]
            return m, sg, hist
        trio = h.cached(("macd", fast, slow, signal), build)
        return Series(trio[0], self.i), Series(trio[1], self.i), Series(trio[2], self.i)

    def adx(self, n=14):
        """-> (adx, plus_di, minus_di) Series."""
        h = self._h
        trio = h.cached(("adx", n), lambda: adx_lists(h.h, h.l, h.c, n))
        return Series(trio[0], self.i), Series(trio[1], self.i), Series(trio[2], self.i)

    def supertrend(self, n=10, mult=3.0):
        """-> (line, direction) Series; direction +1 up, -1 down."""
        h = self._h
        pair = h.cached(("st", n, mult), lambda: supertrend_lists(h.h, h.l, h.c, n, mult))
        return Series(pair[0], self.i), Series(pair[1], self.i)

    def bollinger(self, n=20, k=2.0):
        """-> (middle, upper, lower) Series."""
        h = self._h

        def build():
            m, sd = sma_list(h.c, n), stdev_list(h.c, n)
            up = [a + k * b if not isnan(a) else NAN for a, b in zip(m, sd)]
            lo = [a - k * b if not isnan(a) else NAN for a, b in zip(m, sd)]
            return m, up, lo
        trio = h.cached(("bb", n, k), build)
        return Series(trio[0], self.i), Series(trio[1], self.i), Series(trio[2], self.i)

    def highest(self, n, src="high"):
        """Highest value of the last n bars INCLUDING the current one. Use highest(n)[1] for 'before today'."""
        return self._s(("hi", n, src), lambda: rolling_max(self._h.src(src), n))

    def lowest(self, n, src="low"):
        return self._s(("lo", n, src), lambda: rolling_min(self._h.src(src), n))

    def avg_volume(self, n=50):
        return self.sma(n, "volume")

    def avg_traded_value(self, n=20):
        return self.sma(n, "traded_value")

    def roc(self, n, src="close"):
        """Percent change over n bars, as a Series."""
        a = self._h.src(src)
        return self._s(("roc", n, src), lambda: [NAN] * n + [(a[i] / a[i - n] - 1) * 100 if a[i - n] else NAN
                                                           for i in range(n, len(a))])

    # ---- one-off numbers at an offset
    def pct_change(self, n, k=0):
        a, b = self.close[k], self.close[k + n]
        return (a / b - 1) * 100 if b and not isnan(b) else NAN

    def gap_pct(self, k=0):
        """Opening gap of bar k against the close before it, in percent."""
        prev = self.close[k + 1]
        return (self.open[k] / prev - 1) * 100 if prev and not isnan(prev) else NAN

    def range_pct(self, n, k=0):
        """(highest high - lowest low) / close over the n bars ending k bars ago, in percent: base tightness."""
        hi = max(self.high[j] for j in range(k, k + n))
        lo = min(self.low[j] for j in range(k, k + n))
        c = self.close[k]
        return (hi - lo) / c * 100 if c else NAN

    def volume_ratio(self, n=50, k=0):
        """Volume of bar k / average volume of the n bars before it."""
        av = self.avg_volume(n)[k + 1]
        return self.volume[k] / av if av and not isnan(av) else NAN

    def crossed_above(self, a, b, k=0):
        """True if Series a crossed above b (a Series or a number) on bar k."""
        b0 = b[k] if isinstance(b, Series) else b
        b1 = b[k + 1] if isinstance(b, Series) else b
        return a[k] > b0 and a[k + 1] <= b1

    def crossed_below(self, a, b, k=0):
        b0 = b[k] if isinstance(b, Series) else b
        b1 = b[k + 1] if isinstance(b, Series) else b
        return a[k] < b0 and a[k + 1] >= b1

    def rs_vs_index(self, n, k=0):
        """Stock return minus Nifty 500 return over n bars, in percentage points."""
        ic0, ic1 = self.index_close[k], self.index_close[k + n]
        if isnan(ic0) or isnan(ic1) or not ic1:
            return NAN
        return self.pct_change(n, k) - (ic0 / ic1 - 1) * 100

    # ---- swing points (confirmed pivots, no lookahead)
    def _piv(self, left, right):
        h = self._h
        return h.cached(("piv", left, right), lambda: pivots(h.h, h.l, left, right))

    def pivot_highs(self, left=5, right=5, lookback=250):
        """Confirmed pivot highs known at this bar: list of (bars_ago, price), newest first."""
        ph, _ = self._piv(left, right)
        out = []
        for conf in range(self.i, max(-1, self.i - lookback), -1):
            if conf in ph:
                j, p = ph[conf]
                out.append((self.i - j, p))
        return out

    def pivot_lows(self, left=5, right=5, lookback=250):
        _, pl = self._piv(left, right)
        out = []
        for conf in range(self.i, max(-1, self.i - lookback), -1):
            if conf in pl:
                j, p = pl[conf]
                out.append((self.i - j, p))
        return out

    def last_pivot_high(self, left=5, right=5):
        p = self.pivot_highs(left, right)
        return p[0][1] if p else NAN

    def last_pivot_low(self, left=5, right=5):
        p = self.pivot_lows(left, right)
        return p[0][1] if p else NAN

    def higher_highs_lows(self, left=5, right=5):
        """True when the last two confirmed pivot highs AND lows are each rising."""
        hs, ls = self.pivot_highs(left, right), self.pivot_lows(left, right)
        return len(hs) >= 2 and len(ls) >= 2 and hs[0][1] > hs[1][1] and ls[0][1] > ls[1][1]

    # ---- candles
    def body(self, k=0):
        return abs(self.close[k] - self.open[k])

    def candle_range(self, k=0):
        return self.high[k] - self.low[k]

    def upper_wick(self, k=0):
        return self.high[k] - max(self.open[k], self.close[k])

    def lower_wick(self, k=0):
        return min(self.open[k], self.close[k]) - self.low[k]

    def is_green(self, k=0):
        return self.close[k] > self.open[k]

    def is_red(self, k=0):
        return self.close[k] < self.open[k]

    def close_position(self, k=0):
        """Where the close sits in the bar's range: 0 = at the low, 1 = at the high."""
        r = self.candle_range(k)
        return (self.close[k] - self.low[k]) / r if r else 0.5

    def doji(self, k=0, max_body=0.1):
        r = self.candle_range(k)
        return r > 0 and self.body(k) / r <= max_body

    def hammer(self, k=0):
        """Small body in the top third, lower wick at least 2x the body, little upper wick."""
        r, bd = self.candle_range(k), self.body(k)
        return r > 0 and self.lower_wick(k) >= 2 * max(bd, r * 0.05) and self.upper_wick(k) <= r * 0.25 \
            and self.close_position(k) >= 0.6

    def bullish_engulfing(self, k=0):
        return self.is_red(k + 1) and self.is_green(k) and self.close[k] >= self.open[k + 1] \
            and self.open[k] <= self.close[k + 1] and self.body(k) > self.body(k + 1)

    def bullish_harami(self, k=0):
        return self.is_red(k + 1) and self.is_green(k) and self.open[k] >= self.close[k + 1] \
            and self.close[k] <= self.open[k + 1]

    def piercing_line(self, k=0):
        mid = (self.open[k + 1] + self.close[k + 1]) / 2
        return self.is_red(k + 1) and self.is_green(k) and self.open[k] < self.close[k + 1] \
            and mid < self.close[k] < self.open[k + 1]

    def morning_star(self, k=0):
        return self.is_red(k + 2) and self.body(k + 1) < self.body(k + 2) * 0.5 and self.is_green(k) \
            and self.close[k] > (self.open[k + 2] + self.close[k + 2]) / 2

    def three_white_soldiers(self, k=0):
        return all(self.is_green(k + j) and self.close_position(k + j) > 0.6 for j in range(3)) \
            and self.close[k] > self.close[k + 1] > self.close[k + 2]

    def inside_bar(self, k=0):
        return self.high[k] < self.high[k + 1] and self.low[k] > self.low[k + 1]

    def narrow_range(self, n=7, k=0):
        """NR7 style: bar k has the smallest range of the last n bars."""
        r = self.candle_range(k)
        return all(r < self.candle_range(k + j) for j in range(1, n))
