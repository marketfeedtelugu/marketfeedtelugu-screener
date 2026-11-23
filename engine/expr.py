"""Strategy-card expressions: a small, safe rule language for screening, entries, stops and exits.

A trader's words become one-line rules that anyone can read and share:

    close > sma(50) > sma(200)                 trend (comparisons chain like Python)
    rsi(14) crosses above 60                   momentum turn
    volume > 1.5 * avg_volume(50)              volume spike
    close > highest(20)[1]                     breakout above the 20-bar high before this bar
    any(low <= ema(20) * 1.005, 3)             touched the 20 EMA in the last 3 bars
    weekly(close > sma(30))                    a higher timeframe, as of its last COMPLETED bar
    bullish_engulfing and close_position > 0.7 candles
    time >= 930 and time <= 1430               intraday time window (HHMM)

Rules are parsed into a tree and evaluated bar by bar. There is no eval(): only the names listed in
FUNCS and SERIES exist. `x[k]` means k bars ago; negative k is rejected, so a rule cannot see the
future. A value without enough history is nan, and any comparison with nan is False.
"""

import difflib
import math
import re

from .bars import (NAN, isnan, sma_list, ema_list, wilder_list, true_range_list, rsi_list, rolling_max,
                   rolling_min, stdev_list, adx_lists, supertrend_lists)


class ExprError(Exception):
    pass


# ---------------------------------------------------------------- tokens and parser

TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Za-z_][A-Za-z_0-9]*)|(>=|<=|==|!=|>|<|\+|-|\*|/|\(|\)|\[|\]|,))")
KEYWORDS = {"and", "or", "not", "crosses", "above", "below", "true", "false"}


def tokenize(s):
    pos, out = 0, []
    s = s.strip()
    while pos < len(s):
        m = TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise ExprError("Cannot read %r at position %d in: %s" % (s[pos:pos + 10], pos, s))
        num, ident, op = m.groups()
        if num is not None:
            out.append(("num", float(num)))
        elif ident is not None:
            low = ident.lower()
            out.append(("kw", low) if low in KEYWORDS else ("id", low))
        else:
            out.append(("op", op))
        pos = m.end()
    out.append(("end", None))
    return out


class Parser:
    def __init__(self, text, extra=()):
        self.text = text
        self.extra = set(extra)
        self.toks = tokenize(text)
        self.i = 0

    def peek(self, kind=None, val=None):
        t = self.toks[self.i]
        if kind and t[0] != kind:
            return False
        if val is not None and t[1] != val:
            return False
        return True

    def take(self, kind=None, val=None):
        t = self.toks[self.i]
        if (kind and t[0] != kind) or (val is not None and t[1] != val):
            want = val or kind
            got = t[1] if t[1] is not None else "end of rule"
            raise ExprError("Expected %s but found %r in: %s" % (want, got, self.text))
        self.i += 1
        return t

    def parse(self):
        node = self.or_()
        if not self.peek("end"):
            t = self.toks[self.i]
            hint = ""
            if t[0] == "id":
                near = difflib.get_close_matches(t[1], list(KEYWORDS) + list(FUNCS), n=2, cutoff=0.7)
                hint = (" Did you mean %s?" % " or ".join(near)) if near else \
                    " Join rules with 'and' / 'or', or put them on separate lines."
            raise ExprError("Unexpected %r in: %s.%s" % (t[1], self.text, hint))
        return node

    def or_(self):
        parts = [self.and_()]
        while self.peek("kw", "or"):
            self.take()
            parts.append(self.and_())
        return parts[0] if len(parts) == 1 else ("or", parts)

    def and_(self):
        parts = [self.not_()]
        while self.peek("kw", "and"):
            self.take()
            parts.append(self.not_())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def not_(self):
        if self.peek("kw", "not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.add()
        if self.peek("kw", "crosses"):
            self.take()
            d = self.take("kw")[1]
            if d not in ("above", "below"):
                raise ExprError("Write 'crosses above' or 'crosses below' in: %s" % self.text)
            return ("cross", d, left, self.add())
        pairs = []
        while self.peek("op") and self.toks[self.i][1] in (">", "<", ">=", "<=", "==", "!="):
            op = self.take()[1]
            right = self.add()
            pairs.append(("cmp", op, left, right))
            left = right
        if not pairs:
            return left
        return pairs[0] if len(pairs) == 1 else ("and", pairs)

    def add(self):
        node = self.mul()
        while self.peek("op") and self.toks[self.i][1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek("op") and self.toks[self.i][1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek("op", "-"):
            self.take()
            inner = self.unary()
            if inner[0] == "num":
                return ("num", -inner[1])
            return ("neg", inner)
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek("op", "["):
            self.take()
            neg = False
            if self.peek("op", "-"):
                self.take()
                neg = True
            t = self.take("num")
            self.take("op", "]")
            if neg or t[1] != int(t[1]):
                raise ExprError("[%s%g] looks into the future or is not a whole number of bars. Use [1] for the "
                                "bar before, [2] for two bars ago." % ("-" if neg else "", t[1]))
            node = ("idx", node, int(t[1]))
        return node

    def primary(self):
        t = self.toks[self.i]
        if t[0] == "num":
            self.take()
            return ("num", t[1])
        if t[0] == "kw" and t[1] in ("true", "false"):
            self.take()
            return ("num", 1.0 if t[1] == "true" else 0.0)
        if t[0] == "op" and t[1] == "(":
            self.take()
            node = self.or_()
            self.take("op", ")")
            return node
        if t[0] == "id":
            self.take()
            name = t[1]
            name = ALIASES.get(name, name)
            if self.peek("op", "("):
                self.take()
                args = []
                if not self.peek("op", ")"):
                    args.append(self.or_())
                    while self.peek("op", ","):
                        self.take()
                        args.append(self.or_())
                self.take("op", ")")
                return check_call(name, args, self.text)
            if name in self.extra:
                return ("id", name)
            return check_name(name, self.text)
        raise ExprError("Unexpected %r in: %s" % (t[1] if t[1] is not None else "end of rule", self.text))


def parse(text, extra=()):
    """Parse one rule. `extra` names variables the caller provides (e.g. entry, in a stop rule)."""
    if not isinstance(text, (str, int, float)) or (isinstance(text, str) and not text.strip()):
        raise ExprError("Empty rule")
    return Parser(str(text), extra).parse()


# ---------------------------------------------------------------- the vocabulary

SERIES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "traded_value"}
CONTEXT = {"rs_rank": "1-99 relative-strength percentile across the market (daily, 1/3/6-month momentum)",
           "index_close": "Nifty 500 close (daily charts)",
           "time": "bar start time as HHMM, e.g. 915, 1430 (intraday charts)",
           "weekday": "0 = Monday ... 4 = Friday",
           "month": "1-12",
           "bar_index": "how many bars of history exist up to this bar"}
ALIASES = {"crossed_above": "crosses_above", "crossed_below": "crosses_below", "is_green": "green",
           "is_red": "red", "range": "candle_range", "avg_vol": "avg_volume", "dma": "sma", "ma": "sma",
           "hh_hl": "higher_highs_lows", "nr": "narrow_range", "engulfing": "bullish_engulfing",
           "high52": "high_52w", "low52": "low_52w", "st": "supertrend", "st_dir": "supertrend_dir"}

# name: (params with defaults, help). A param named "src" takes a series (close, volume, or any
# expression); other params must be plain numbers.
FUNCS = {
    "sma": ((("n", None), ("src", "close")), "simple moving average"),
    "ema": ((("n", None), ("src", "close")), "exponential moving average"),
    "wma": ((("n", None), ("src", "close")), "weighted moving average"),
    "rsi": ((("n", 14), ("src", "close")), "RSI (Wilder)"),
    "roc": ((("n", None), ("src", "close")), "rate of change over n bars, %"),
    "stdev": ((("n", 20), ("src", "close")), "standard deviation"),
    "highest": ((("n", None), ("src", "high")), "highest value of the last n bars INCLUDING this one; [1] = before this bar"),
    "lowest": ((("n", None), ("src", "low")), "lowest value of the last n bars including this one"),
    "avg_volume": ((("n", 50),), "average volume"),
    "avg_traded_value": ((("n", 20),), "average close x volume, rupees"),
    "volume_ratio": ((("n", 50),), "this bar's volume / the average of the n bars before it"),
    "atr": ((("n", 14),), "average true range"),
    "atr_pct": ((("n", 14),), "ATR as % of the close"),
    "macd": ((("fast", 12), ("slow", 26), ("signal", 9)), "MACD line"),
    "macd_signal": ((("fast", 12), ("slow", 26), ("signal", 9)), "MACD signal line"),
    "macd_hist": ((("fast", 12), ("slow", 26), ("signal", 9)), "MACD histogram"),
    "adx": ((("n", 14),), "ADX trend strength"),
    "plus_di": ((("n", 14),), "+DI"),
    "minus_di": ((("n", 14),), "-DI"),
    "supertrend": ((("n", 10), ("mult", 3)), "Supertrend line"),
    "supertrend_dir": ((("n", 10), ("mult", 3)), "Supertrend direction: 1 up, -1 down"),
    "bb_upper": ((("n", 20), ("k", 2)), "Bollinger upper band"),
    "bb_mid": ((("n", 20), ("k", 2)), "Bollinger middle band"),
    "bb_lower": ((("n", 20), ("k", 2)), "Bollinger lower band"),
    "bb_width": ((("n", 20), ("k", 2)), "Bollinger band width, % of the middle band"),
    "stoch_k": ((("n", 14), ("smooth", 3)), "stochastic %K (smoothed)"),
    "stoch_d": ((("n", 14), ("smooth", 3), ("d", 3)), "stochastic %D"),
    "cci": ((("n", 20),), "commodity channel index"),
    "obv": ((), "on-balance volume"),
    "vwap": ((), "session VWAP (intraday charts; resets each day)"),
    "orh": ((("minutes", 15),), "opening-range high: highest high of the session's first N minutes (intraday; nan until that range is complete)"),
    "orl": ((("minutes", 15),), "opening-range low of the first N minutes (intraday)"),
    "session_high": ((), "highest high so far today, including this bar (intraday)"),
    "session_low": ((), "lowest low so far today, including this bar (intraday)"),
    "session_open": ((), "today's first open (intraday)"),
    "pct_change": ((("n", None),), "close vs n bars ago, %"),
    "gap_pct": ((), "open vs the previous close, %"),
    "range_pct": ((("n", None),), "(highest high - lowest low) / close over n bars, %: base tightness"),
    "rs_vs_index": ((("n", None),), "stock return minus Nifty 500 return over n bars, % points (daily)"),
    "high_52w": ((), "highest high of the last 250 daily bars"),
    "low_52w": ((), "lowest low of the last 250 daily bars"),
    "pivot_high": ((("left", 5), ("right", 5)), "last CONFIRMED swing high (known `right` bars later)"),
    "pivot_low": ((("left", 5), ("right", 5)), "last confirmed swing low"),
    "higher_highs_lows": ((("left", 5), ("right", 5)), "last two swing highs and lows both rising"),
    "narrow_range": ((("n", 7),), "smallest range of the last n bars (NR7)"),
    "green": ((), "close > open"), "red": ((), "close < open"),
    "doji": ((), "tiny body"), "hammer": ((), "hammer candle"),
    "bullish_engulfing": ((), "green body engulfs the prior red body"),
    "bullish_harami": ((), "green inside the prior red body"),
    "piercing_line": ((), "piercing line"), "morning_star": ((), "morning star"),
    "three_white_soldiers": ((), "three white soldiers"), "inside_bar": ((), "inside the prior bar"),
    "body": ((), "|close - open|"), "candle_range": ((), "high - low"),
    "upper_wick": ((), "upper wick"), "lower_wick": ((), "lower wick"),
    "close_position": ((), "where the close sits in the bar: 0 = low, 1 = high"),
    # helpers taking rules or values
    "abs": ((("x", "expr"),), "absolute value"),
    "min": ((("*", "expr"),), "smallest of the values"),
    "max": ((("*", "expr"),), "largest of the values"),
    "near": ((("a", "expr"), ("b", "expr"), ("pct", None)), "a within pct % of b"),
    "between": ((("x", "expr"), ("lo", "expr"), ("hi", "expr")), "lo <= x <= hi"),
    "rising": ((("x", "expr"), ("n", 1)), "x above its value n bars ago"),
    "falling": ((("x", "expr"), ("n", 1)), "x below its value n bars ago"),
    "crosses_above": ((("a", "expr"), ("b", "expr")), "a crossed above b on this bar"),
    "crosses_below": ((("a", "expr"), ("b", "expr")), "a crossed below b on this bar"),
    "any": ((("cond", "expr"), ("n", None)), "cond was true on any of the last n bars (including this one)"),
    "all": ((("cond", "expr"), ("n", None)), "cond was true on all of the last n bars"),
    "count": ((("cond", "expr"), ("n", None)), "how many of the last n bars cond was true"),
    "bars_since": ((("cond", "expr"), ("max", 500)), "bars since cond was last true (0 = this bar)"),
    "daily": ((("x", "expr"),), "evaluate on the daily chart, last COMPLETED daily bar"),
    "weekly": ((("x", "expr"),), "evaluate on the weekly chart, last completed week"),
    "hourly": ((("x", "expr"),), "evaluate on the 1-hour chart, last completed hour"),
}
HIGHER = {"daily": "1d", "weekly": "1w", "hourly": "1h"}


def _suggest(name):
    pool = list(FUNCS) + list(SERIES) + list(CONTEXT)
    close = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
    return (" Did you mean %s?" % " or ".join(close)) if close else ""


def check_name(name, text):
    if name in SERIES or name in CONTEXT:
        return ("id", name)
    if name in FUNCS:
        return check_call(name, [], text)
    raise ExprError("Unknown name %r in: %s.%s" % (name, text, _suggest(name)))


def check_call(name, args, text):
    if name not in FUNCS:
        raise ExprError("Unknown function %r in: %s.%s" % (name, text, _suggest(name)))
    params, _ = FUNCS[name]
    if params and params[0][0] == "*":
        if len(args) < 2:
            raise ExprError("%s() needs at least two values in: %s" % (name, text))
        return ("call", name, args)
    pnames = [p for p, _ in params]
    if pnames[:2] == ["n", "src"] and len(args) == 2 and args[0][0] != "num" and args[1][0] == "num":
        args = [args[1], args[0]]          # highest(close, 20) reads as naturally as highest(20, close)
    if len(args) > len(params):
        raise ExprError("%s() takes at most %d argument(s) (%s) in: %s"
                        % (name, len(params), ", ".join(p for p, _ in params), text))
    full = []
    for k, (pname, default) in enumerate(params):
        if k < len(args):
            a = args[k]
        elif default is None or default == "expr":
            raise ExprError("%s() needs %s in: %s" % (name, pname, text))
        elif pname == "src":
            a = ("id", default)
        else:
            a = ("num", float(default))
        if pname not in ("src",) and default != "expr" and a[0] != "num":
            raise ExprError("%s(): %s must be a plain number in: %s" % (name, pname, text))
        full.append(a)
    return ("call", name, full)


# ---------------------------------------------------------------- evaluation

def truthy(v):
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


class Ctx:
    """Evaluation context for one History (one stock, one timeframe)."""

    __slots__ = ("h", "vars", "_bars", "ctx", "subs")

    def __init__(self, hist, variables=None, ctx=None):
        self.h = hist
        self.vars = variables if variables is not None else {}
        self.ctx = ctx
        self._bars = {}
        self.subs = {}

    def bars(self, i):
        b = self._bars.get(i)
        if b is None:
            b = self.h.bars(i, self.ctx)
            self._bars[i] = b
        return b


def _num(x):
    return NAN if x is None else x


def _key(node):
    return repr(node)


def src_list(node, c):
    """The full-length list behind a src argument: a raw series, or any expression materialised once."""
    h = c.h
    if node[0] == "id" and node[1] in SERIES:
        return series_list(h, node[1])
    key = ("expr_list", _key(node))

    def build():
        sub = Ctx(h, c.vars, c.ctx)
        out = []
        for i in range(len(h.c)):
            v = ev(node, sub, i)
            out.append(float(v) if not isinstance(v, float) else v)
        return out
    return h.cached(key, build)


def series_list(h, name):
    if name == "ohlc4":
        return h.cached(("ohlc4",), lambda: [(a + b + x + y) / 4 for a, b, x, y in zip(h.o, h.h, h.l, h.c)])
    return h.src(name)


def wma_list(src, n):
    out = [NAN] * len(src)
    den = n * (n + 1) / 2
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if any(isnan(x) for x in w):
            continue
        out[i] = sum((k + 1) * x for k, x in enumerate(w)) / den
    return out


def _ival(node):
    return int(node[1])


def _fval(node):
    return float(node[1])


def ind(name, args, c):
    """Full-length indicator list for (name, constant args) on this History, cached."""
    h = c.h
    if name in ("sma", "ema", "wma", "rsi", "roc", "stdev", "highest", "lowest"):
        n, srcn = _ival(args[0]), args[1]
        key = (name, n, _key(srcn))

        def build():
            s = src_list(srcn, c)
            if name == "sma":
                return sma_list(s, n)
            if name == "ema":
                return ema_list(s, n)
            if name == "wma":
                return wma_list(s, n)
            if name == "rsi":
                return rsi_list(s, n)
            if name == "stdev":
                return stdev_list(s, n)
            if name == "highest":
                return rolling_max(s, n)
            if name == "lowest":
                return rolling_min(s, n)
            return [NAN] * n + [(s[i] / s[i - n] - 1) * 100 if s[i - n] else NAN for i in range(n, len(s))]
        return h.cached(key, build)
    if name in ("atr", "atr_pct"):
        n = _ival(args[0])
        a = h.cached(("atr", n), lambda: wilder_list(true_range_list(h.h, h.l, h.c), n))
        if name == "atr":
            return a
        return h.cached(("atrp", n), lambda: [x / y * 100 if not isnan(x) and y else NAN for x, y in zip(a, h.c)])
    if name in ("macd", "macd_signal", "macd_hist"):
        f, s, g = _ival(args[0]), _ival(args[1]), _ival(args[2])

        def build():
            fe, se = ema_list(h.c, f), ema_list(h.c, s)
            m = [a - b if not (isnan(a) or isnan(b)) else NAN for a, b in zip(fe, se)]
            sg = ema_list(m, g)
            hi = [a - b if not (isnan(a) or isnan(b)) else NAN for a, b in zip(m, sg)]
            return m, sg, hi
        trio = h.cached(("macd", f, s, g), build)
        return trio[("macd", "macd_signal", "macd_hist").index(name)]
    if name in ("adx", "plus_di", "minus_di"):
        n = _ival(args[0])
        trio = h.cached(("adx", n), lambda: adx_lists(h.h, h.l, h.c, n))
        return trio[("adx", "plus_di", "minus_di").index(name)]
    if name in ("supertrend", "supertrend_dir"):
        n, m = _ival(args[0]), _fval(args[1])
        pair = h.cached(("st", n, m), lambda: supertrend_lists(h.h, h.l, h.c, n, m))
        return pair[0 if name == "supertrend" else 1]
    if name.startswith("bb_"):
        n, k = _ival(args[0]), _fval(args[1])

        def build():
            m, sd = sma_list(h.c, n), stdev_list(h.c, n)
            up = [a + k * b if not isnan(a) else NAN for a, b in zip(m, sd)]
            lo = [a - k * b if not isnan(a) else NAN for a, b in zip(m, sd)]
            wd = [(u - l_) / a * 100 if not isnan(a) and a else NAN for u, l_, a in zip(up, lo, m)]
            return m, up, lo, wd
        q = h.cached(("bb", n, k), build)
        return q[("bb_mid", "bb_upper", "bb_lower", "bb_width").index(name)]
    if name in ("stoch_k", "stoch_d"):
        n, sm = _ival(args[0]), _ival(args[1])

        def build():
            hi, lo = rolling_max(h.h, n), rolling_min(h.l, n)
            raw = [(x - b) / (a - b) * 100 if not isnan(a) and a != b else NAN for x, a, b in zip(h.c, hi, lo)]
            return sma_list(raw, sm)
        k_ = h.cached(("stk", n, sm), build)
        if name == "stoch_k":
            return k_
        d = _ival(args[2])
        return h.cached(("std", n, sm, d), lambda: sma_list(k_, d))
    if name == "cci":
        n = _ival(args[0])

        def build():
            tp = [(a + b + x) / 3 for a, b, x in zip(h.h, h.l, h.c)]
            ma = sma_list(tp, n)
            out = [NAN] * len(tp)
            for i in range(n - 1, len(tp)):
                md = sum(abs(x - ma[i]) for x in tp[i - n + 1:i + 1]) / n
                out[i] = (tp[i] - ma[i]) / (0.015 * md) if md else NAN
            return out
        return h.cached(("cci", n), build)
    if name == "obv":
        def build():
            out, v = [0.0] * len(h.c), 0.0
            for i in range(1, len(h.c)):
                v += h.v[i] if h.c[i] > h.c[i - 1] else -h.v[i] if h.c[i] < h.c[i - 1] else 0
                out[i] = v
            return out
        return h.cached(("obv",), build)
    if name == "vwap":
        def build():
            out, day, pv, vv = [], None, 0.0, 0.0
            for i, t in enumerate(h.dates):
                d = t[:10]
                if d != day:
                    day, pv, vv = d, 0.0, 0.0
                tp = (h.h[i] + h.l[i] + h.c[i]) / 3
                pv += tp * h.v[i]
                vv += h.v[i]
                out.append(pv / vv if vv else tp)
            return out
        return h.cached(("vwap",), build)
    if name in ("orh", "orl"):
        mins = _ival(args[0])

        def build():
            out, day, rng, done = [], None, None, False
            for i, t in enumerate(h.dates):
                if len(t) <= 10:
                    out.append(NAN)
                    continue
                d, m = t[:10], int(t[11:13]) * 60 + int(t[14:16])
                if d != day:
                    day, rng, done = d, None, False
                if m < 555 + mins:
                    v = h.h[i] if name == "orh" else h.l[i]
                    rng = v if rng is None else (max(rng, v) if name == "orh" else min(rng, v))
                    out.append(NAN)
                else:
                    out.append(rng if rng is not None else NAN)
            return out
        return h.cached((name, mins), build)
    if name in ("session_high", "session_low", "session_open"):
        def build():
            out, day, v = [], None, None
            for i, t in enumerate(h.dates):
                d = t[:10]
                if d != day:
                    day, v = d, None
                if name == "session_open":
                    v = h.o[i] if v is None else v
                elif name == "session_high":
                    v = h.h[i] if v is None else max(v, h.h[i])
                else:
                    v = h.l[i] if v is None else min(v, h.l[i])
                out.append(v)
            return out
        return h.cached((name,), build)
    if name in ("high_52w", "low_52w"):
        n = 250 if getattr(h, "tf", "1d") == "1d" else 52 if getattr(h, "tf", "1d") == "1w" else 250
        return h.cached(("hi" if name == "high_52w" else "lo", n, "high" if name == "high_52w" else "low"),
                        lambda: rolling_max(h.h, n) if name == "high_52w" else rolling_min(h.l, n))
    raise ExprError("no list for %s" % name)


LIST_FUNCS = {"sma", "ema", "wma", "rsi", "roc", "stdev", "highest", "lowest", "atr", "atr_pct", "macd",
              "macd_signal", "macd_hist", "adx", "plus_di", "minus_di", "supertrend", "supertrend_dir",
              "bb_upper", "bb_mid", "bb_lower", "bb_width", "stoch_k", "stoch_d", "cci", "obv", "vwap",
              "high_52w", "low_52w", "orh", "orl", "session_high", "session_low", "session_open"}
CANDLES = {"green": "is_green", "red": "is_red", "doji": "doji", "hammer": "hammer",
           "bullish_engulfing": "bullish_engulfing", "bullish_harami": "bullish_harami",
           "piercing_line": "piercing_line", "morning_star": "morning_star",
           "three_white_soldiers": "three_white_soldiers", "inside_bar": "inside_bar", "body": "body",
           "candle_range": "candle_range", "upper_wick": "upper_wick", "lower_wick": "lower_wick",
           "close_position": "close_position", "gap_pct": "gap_pct"}


def ev(node, c, i):
    """Value of node at absolute bar index i of c.h (float, bool, or nan)."""
    if i < 0:
        return NAN
    t = node[0]
    if t == "num":
        return node[1]
    if t == "id":
        name = node[1]
        if name in c.vars:
            return c.vars[name]
        h = c.h
        if name in ("open", "high", "low", "close", "volume"):
            return _num({"open": h.o, "high": h.h, "low": h.l, "close": h.c, "volume": h.v}[name][i])
        if name in SERIES:
            return _num(series_list(h, name)[i])
        if name == "rs_rank":
            return _num(h.rs_rank[i])
        if name == "index_close":
            return _num(h.index_close[i])
        if name == "bar_index":
            return float(i + 1)
        if name in ("time", "weekday", "month"):
            d = h.dates[i]
            if name == "time":
                return float(d[11:13] + d[14:16]) if len(d) > 10 else NAN
            import datetime as _dt
            dd = _dt.date(int(d[:4]), int(d[5:7]), int(d[8:10]))
            return float(dd.weekday() if name == "weekday" else dd.month)
        raise ExprError("unknown name %s" % name)
    if t == "idx":
        return ev(node[1], c, i - node[2])
    if t == "neg":
        v = ev(node[1], c, i)
        return -v if not isnan(v) else NAN
    if t == "bin":
        a, b = ev(node[2], c, i), ev(node[3], c, i)
        if isnan(a) or isnan(b):
            return NAN
        op = node[1]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        return a / b if b else NAN
    if t == "cmp":
        a, b = ev(node[2], c, i), ev(node[3], c, i)
        if isnan(a) or isnan(b):
            return False
        op = node[1]
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b}[op]
    if t == "cross":
        a0, b0 = ev(node[2], c, i), ev(node[3], c, i)
        a1, b1 = ev(node[2], c, i - 1), ev(node[3], c, i - 1)
        if any(isnan(x) for x in (a0, b0, a1, b1)):
            return False
        return (a0 > b0 and a1 <= b1) if node[1] == "above" else (a0 < b0 and a1 >= b1)
    if t == "and":
        return all(truthy(ev(x, c, i)) for x in node[1])
    if t == "or":
        return any(truthy(ev(x, c, i)) for x in node[1])
    if t == "not":
        return not truthy(ev(node[1], c, i))
    if t == "call":
        return call(node[1], node[2], c, i)
    raise ExprError("bad node %r" % (t,))


def call(name, args, c, i):
    h = c.h
    if name in LIST_FUNCS:
        lst = ind(name, args, c)
        return _num(lst[i]) if i < len(lst) else NAN
    if name in CANDLES:
        b = c.bars(i)
        v = getattr(b, CANDLES[name])(0)
        return v
    if name in ("avg_volume", "avg_traded_value"):
        n = _ival(args[0])
        lst = h.cached(("sma", n, "volume" if name == "avg_volume" else "traded_value"),
                       lambda: sma_list(h.src("volume" if name == "avg_volume" else "traded_value"), n))
        return _num(lst[i])
    if name == "volume_ratio":
        n = _ival(args[0])
        lst = h.cached(("sma", n, "volume"), lambda: sma_list(h.v, n))
        av = lst[i - 1] if i >= 1 else NAN
        return h.v[i] / av if av and not isnan(av) else NAN
    if name == "pct_change":
        n = _ival(args[0])
        if i - n < 0 or not h.c[i - n]:
            return NAN
        return (h.c[i] / h.c[i - n] - 1) * 100
    if name == "range_pct":
        n = _ival(args[0])
        if i - n + 1 < 0:
            return NAN
        hi, lo = max(h.h[i - n + 1:i + 1]), min(h.l[i - n + 1:i + 1])
        return (hi - lo) / h.c[i] * 100 if h.c[i] else NAN
    if name == "rs_vs_index":
        n = _ival(args[0])
        if i - n < 0:
            return NAN
        ic0, ic1 = h.index_close[i], h.index_close[i - n]
        if isnan(ic0) or isnan(ic1) or not ic1 or not h.c[i - n]:
            return NAN
        return (h.c[i] / h.c[i - n] - 1) * 100 - (ic0 / ic1 - 1) * 100
    if name in ("pivot_high", "pivot_low"):
        b = c.bars(i)
        return b.last_pivot_high(_ival(args[0]), _ival(args[1])) if name == "pivot_high" else \
            b.last_pivot_low(_ival(args[0]), _ival(args[1]))
    if name == "higher_highs_lows":
        return c.bars(i).higher_highs_lows(_ival(args[0]), _ival(args[1]))
    if name == "narrow_range":
        n = _ival(args[0])
        if i - n + 1 < 0:
            return False
        return c.bars(i).narrow_range(n, 0)
    if name == "abs":
        v = ev(args[0], c, i)
        return abs(v) if not isnan(v) else NAN
    if name in ("min", "max"):
        vals = [ev(a, c, i) for a in args]
        if any(isnan(v) for v in vals):
            return NAN
        return min(vals) if name == "min" else max(vals)
    if name == "near":
        a, b = ev(args[0], c, i), ev(args[1], c, i)
        if isnan(a) or isnan(b) or not b:
            return False
        return abs(a - b) / abs(b) * 100 <= _fval(args[2])
    if name == "between":
        x, lo, hi = (ev(a, c, i) for a in args)
        if any(isnan(v) for v in (x, lo, hi)):
            return False
        return lo <= x <= hi
    if name in ("rising", "falling"):
        n = _ival(args[1])
        a, b = ev(args[0], c, i), ev(args[0], c, i - n)
        if isnan(a) or isnan(b):
            return False
        return a > b if name == "rising" else a < b
    if name in ("crosses_above", "crosses_below"):
        return ev(("cross", "above" if name == "crosses_above" else "below", args[0], args[1]), c, i)
    if name in ("any", "all", "count"):
        n = _ival(args[1])
        vals = [truthy(ev(args[0], c, i - k)) for k in range(n) if i - k >= 0]
        if name == "any":
            return any(vals)
        if name == "all":
            return len(vals) == n and all(vals)
        return float(sum(vals))
    if name == "bars_since":
        mx = _ival(args[1])
        for k in range(0, mx + 1):
            if i - k < 0:
                break
            if truthy(ev(args[0], c, i - k)):
                return float(k)
        return NAN
    if name in HIGHER:
        tf = HIGHER[name]
        hi = getattr(h, "higher", {}).get(tf)
        if hi is None:
            raise ExprError("%s() is not available on a %s chart (only higher timeframes: %s)"
                            % (name, getattr(h, "tf", "1d"), ", ".join(sorted(getattr(h, "higher", {}))) or "none"))
        h2, mp = hi
        if h2 is h:
            return ev(args[0], c, i)
        j = mp[i] if i < len(mp) else -1
        if j is None or j < 0:
            return NAN
        sub = c.subs.get(tf)
        if sub is None or sub.h is not h2:
            sub = Ctx(h2, c.vars, c.ctx)          # shares the variables (entry, ...) with the caller
            c.subs[tf] = sub
        return ev(args[0], sub, j)
    raise ExprError("function %s is not implemented" % name)


# ---------------------------------------------------------------- static helpers

def walk(node, fn, tf=None):
    fn(node, tf)
    t = node[0]
    if t in ("bin", "cmp"):
        walk(node[2], fn, tf)
        walk(node[3], fn, tf)
    elif t == "cross":
        walk(node[2], fn, tf)
        walk(node[3], fn, tf)
    elif t in ("and", "or"):
        for x in node[1]:
            walk(x, fn, tf)
    elif t in ("not", "neg"):
        walk(node[1], fn, tf)
    elif t == "idx":
        walk(node[1], fn, tf)
    elif t == "call":
        sub_tf = HIGHER.get(node[1], tf)
        for a in node[2]:
            walk(a, fn, sub_tf)


def lookback(node):
    """Rough number of bars a rule needs before it can be true (for the warm-up)."""
    t = node[0]
    if t == "idx":
        return node[2] + lookback(node[1])
    if t == "call":
        name, args = node[1], node[2]
        if name in HIGHER:
            return 2
        nums = [int(a[1]) for a in args if a[0] == "num"]
        base = max(nums) if nums else 1
        if name in ("ema", "rsi", "atr", "atr_pct", "adx", "plus_di", "minus_di", "supertrend", "supertrend_dir",
                    "macd", "macd_signal", "macd_hist"):
            base = int(base * 2)
        if name in ("high_52w", "low_52w"):
            base = 250
        if name in ("pivot_high", "pivot_low", "higher_highs_lows"):
            base = 120
        inner = max([lookback(a) for a in args if a[0] != "num"] or [0])
        return base + inner
    if t in ("bin", "cmp", "cross"):
        return max(lookback(node[2]), lookback(node[3])) + (1 if t == "cross" else 0)
    if t in ("and", "or"):
        return max(lookback(x) for x in node[1])
    if t in ("not", "neg"):
        return lookback(node[1])
    return 1


def uses(node, names):
    found = set()

    def fn(n, tf):
        if n[0] == "id" and n[1] in names:
            found.add(n[1])
        if n[0] == "call" and n[1] in names:
            found.add(n[1])
    walk(node, fn)
    return found


def higher_tfs(node):
    out = set()

    def fn(n, tf):
        if n[0] == "call" and n[1] in HIGHER:
            out.add(HIGHER[n[1]])
    walk(node, fn)
    return out


def overlays(node):
    """Moving averages and bands used on the strategy's own chart, for drawing: [(label, name, args)]."""
    out = []

    def fn(n, tf):
        if tf is not None or n[0] != "call":
            return
        name, args = n[1], n[2]
        if name in ("sma", "ema", "wma") and args[1][0] == "id" and args[1][1] == "close":
            out.append(("%s %d" % (name.upper(), int(args[0][1])), name, args))
        elif name in ("bb_upper", "bb_lower", "supertrend", "vwap"):
            lab = {"bb_upper": "BB upper", "bb_lower": "BB lower", "supertrend": "Supertrend", "vwap": "VWAP"}[name]
            out.append((lab, name, args))
    walk(node, fn)
    seen, uniq = set(), []
    for o in out:
        if o[0] not in seen:
            seen.add(o[0])
            uniq.append(o)
    return uniq


def series_for_chart(hist, name, args, i0, i1):
    c = Ctx(hist)
    lst = ind(name, args, c)
    return [None if isnan(lst[i]) else round(lst[i], 2) for i in range(i0, i1 + 1)]


def vocabulary():
    """Text listing of every name, for `screener.py rules`."""
    lines = ["Series: " + ", ".join(sorted(SERIES)), "Context: " + "; ".join("%s (%s)" % kv for kv in CONTEXT.items()),
             "Functions:"]
    for name, (params, help_) in FUNCS.items():
        ps = ", ".join(p if d in (None, "expr") else ("%s=%s" % (p, d)) for p, d in params) if params else ""
        if params and params[0][0] == "*":
            ps = "a, b, ..."
        lines.append("  %s(%s)  %s" % (name, ps, help_))
    lines.append("Operators: + - * /  > < >= <= == !=  'crosses above' / 'crosses below'  and or not  x[k] = k bars ago")
    return "\n".join(lines)
