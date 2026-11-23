"""Strategy cards: one JSON file that holds a trader's whole screener, readable without code.

    {
      "name": "rsi_breakout", "title": "RSI momentum breakout", "author": "", "description": "",
      "universe": "nifty100",                      # or {"index": "custom", "symbols": ["RELIANCE", "TCS"]}
      "timeframe": "1d",                           # 5m 15m 1h 2h 1d 1w
      "capital": {"capital": 100000, "risk_per_trade_pct": 1, "max_positions": 5, "max_open_risk_pct": 5,
                  "max_position_pct": 25, "max_per_sector": 2, "price_ceiling": null,
                  "product": "delivery",           # or "intraday" (MIS: flat by 15:15, intraday charges)
                  "brokerage_per_order": 0, "dp_per_sell": 15.93, "slippage_pct": 0.1},
      "filters": {"preset": "marketfeed",          # the marketfeed funnel, or "custom" (only your rules)
                  "rules": ["close > sma(200)"],   # your own screening rules on the DAILY chart
                  "min_rs": 70, ...},              # any preset value can be overridden
      "entry": {"when": ["rsi(14) crosses above 60", "close > ema(20)"],    # all must be true
                "order": "buy_stop",               # market (next bar's open) | buy_stop | limit
                "price": "high * 1.001",           # order price for buy_stop / limit
                "valid_bars": 2, "score": "rs_rank"},
      "stop": "lowest(5) * 0.995",                 # a rule; `entry` is the order price
      "target": {"r": 2},                          # or {"price": "highest(250)"} or null
      "exit": {"time_bars": 20, "breakeven_at_r": null,
               "trail_stop": null,                 # e.g. "lowest(10)" or "highest_close - 3 * atr(14)"
               "exit_when": null,                  # e.g. "close < ema(20)": out at the next bar's open
               "flat_by": null},                   # intraday: e.g. "15:15"
      "python": null                               # optional: a setup .py for rules the card cannot express
    }

The card compiles into the same interface as a Python setup (entry(b), EXIT_RULES, exit(b, trade)), so
the scan, the backtest and the forward test all run exactly the rules the trader read and approved.
"""

import copy
import hashlib
import json
import math
import os

from . import data as D
from .bars import isnan
from .expr import parse, ev, truthy, Ctx, ExprError, lookback, overlays, higher_tfs, uses

PRESETS = {
    "marketfeed": {
        "regime": True, "top_sectors": 3, "leader_rs": 95, "min_rs": 70, "trend": "above200",
        "caps": ["large", "mid", "small"], "min_price": 50, "min_traded_value_cr": 10,
        "atr_pct_min": 1.0, "atr_pct_max": 6.0, "max_big_gaps": 3, "exclude_surveillance": True,
        "exclude_bands": ["2", "5"], "min_rr": 2.0, "check_resistance": True, "vix_high": 22,
        "bull_breadth": 50, "bear_breadth": 40, "regime_risk": {"bullish": 1.0, "neutral": 0.5, "bearish": 0.0},
        "min_bars": 150,
    },
    "custom": {
        "regime": False, "top_sectors": 0, "leader_rs": 95, "min_rs": 0, "trend": "none",
        "caps": ["large", "mid", "small", "micro"], "min_price": 20, "min_traded_value_cr": 2,
        "atr_pct_min": 0.0, "atr_pct_max": 100.0, "max_big_gaps": 999, "exclude_surveillance": True,
        "exclude_bands": ["2", "5"], "min_rr": 0.0, "check_resistance": False, "vix_high": 22,
        "bull_breadth": 50, "bear_breadth": 40, "regime_risk": {"bullish": 1.0, "neutral": 1.0, "bearish": 1.0},
        "min_bars": 60,
    },
}
FILTER_HELP = {
    "regime": "market regime gate (Nifty 500 trend, breadth, VIX): no new longs when bearish, half size when sideways",
    "top_sectors": "only stocks in the N strongest sectors by relative strength (0 = off)",
    "leader_rs": "an RS leader at or above this rank passes even outside the top sectors",
    "min_rs": "minimum RS rank 1-99 vs the market (0 = off)",
    "trend": "above200 (close > 200 DMA), stage2 (close > 50 DMA > 200 DMA) or none",
    "caps": "cap buckets allowed: large, mid, small, micro",
    "min_price": "minimum share price, rupees",
    "min_traded_value_cr": "minimum 20-day median traded value, rupee crore a day",
    "atr_pct_min": "minimum daily ATR as % of price", "atr_pct_max": "maximum daily ATR as % of price",
    "max_big_gaps": "maximum gaps of 4%+ in 60 sessions",
    "exclude_surveillance": "drop stocks under NSE ASM/GSM surveillance",
    "exclude_bands": "drop stocks with these price bands (2%, 5% circuits)",
    "min_rr": "minimum reward:risk to the target and to the next resistance (0 = off)",
    "check_resistance": "reject when the next swing high is closer than min_rr",
}
CAPITAL_DEFAULTS = {"capital": None, "risk_per_trade_pct": 1.0, "max_positions": 5, "max_open_risk_pct": 5.0,
                    "max_position_pct": 25.0, "max_per_sector": 2, "price_ceiling": None, "product": "delivery",
                    "brokerage_per_order": 0.0, "dp_per_sell": 15.93, "slippage_pct": 0.10}
ORDERS = {"market": "market", "buy_stop": "stop", "stop": "stop", "buy-stop": "stop", "limit": "limit",
          "buy_limit": "limit", "buy-limit": "limit"}
TRADE_VARS = ("entry", "initial_stop", "bars_held", "highest_close", "highest_high", "risk", "r_now")


class CardError(Exception):
    pass


# ---------------------------------------------------------------- ticks

def tick_size(price):
    """NSE tick size by price band (from 15 Apr 2025; NSE reviews it monthly)."""
    if price < 250:
        return 0.01
    if price < 1000:
        return 0.05
    if price < 5000:
        return 0.10
    if price < 10000:
        return 0.50
    if price < 20000:
        return 1.00
    return 5.00


def to_tick(price, mode):
    t = tick_size(price)
    q = price / t
    q = math.ceil(q - 1e-9) if mode == "up" else math.floor(q + 1e-9) if mode == "down" else round(q)
    return round(q * t, 2)


# ---------------------------------------------------------------- load and validate

def card_path(p, name):
    if os.path.exists(name):
        return name
    n = name[:-5] if name.endswith(".json") else name
    f = os.path.join(p["strategies"], n + ".json")
    if os.path.exists(f):
        return f
    raise CardError("No strategy card called %s in %s. Cards there: %s"
                    % (name, p["strategies"], ", ".join(list_cards(p)) or "none yet"))


def list_cards(p):
    return sorted(f[:-5] for f in os.listdir(p["strategies"]) if f.endswith(".json") and not f.startswith("_"))


def read_card(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except ValueError as e:
        raise CardError("%s is not valid JSON: %s" % (os.path.basename(path), e))


def card_hash(card):
    blob = json.dumps({k: v for k, v in card.items() if k not in ("notes", "author", "description")},
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def normalise(card):
    """Fill defaults and check every field. Returns a new dict; raises CardError with a plain reason."""
    c = copy.deepcopy(card)
    if not c.get("name"):
        raise CardError("The card needs a name (letters, digits, underscores)")
    c.setdefault("title", c["name"].replace("_", " ").title())
    u = c.get("universe") or "nifty500"
    if isinstance(u, str):
        u = {"index": u}
    try:
        u["index"] = D.universe_key(u.get("index", "nifty500"))
    except D.DataError as e:
        raise CardError(str(e))
    if u["index"] == "custom" and not u.get("symbols"):
        raise CardError("universe custom needs a symbols list")
    u["symbols"] = [s.strip().upper() for s in u.get("symbols") or []]
    u["exclude"] = [s.strip().upper() for s in u.get("exclude") or []]
    c["universe"] = u
    try:
        c["timeframe"] = D.tf_key(c.get("timeframe", "1d"))
    except D.DataError as e:
        raise CardError(str(e))
    cap = dict(CAPITAL_DEFAULTS)
    cap.update(c.get("capital") or {})
    if not cap.get("capital") or cap["capital"] <= 0:
        raise CardError("capital.capital is required: the rupees set aside for this strategy")
    if not 0 < cap["risk_per_trade_pct"] <= 5:
        raise CardError("risk_per_trade_pct %.2f is outside 0-5%%. Most traders use 0.5-2%%." % cap["risk_per_trade_pct"])
    if cap["product"] not in ("delivery", "intraday"):
        raise CardError("capital.product must be delivery or intraday")
    c["capital"] = cap
    f = c.get("filters") or {}
    preset = f.get("preset", "marketfeed")
    if preset not in PRESETS:
        raise CardError("filters.preset must be marketfeed or custom")
    full = copy.deepcopy(PRESETS[preset])
    for k, v in f.items():
        if k in ("preset", "rules"):
            continue
        if k not in full:
            raise CardError("Unknown filter %r. Known: %s" % (k, ", ".join(sorted(full))))
        full[k] = v
    full["preset"] = preset
    full["rules"] = list(f.get("rules") or [])
    if full["trend"] not in ("above200", "stage2", "none"):
        raise CardError("filters.trend must be above200, stage2 or none")
    c["filters"] = full
    e = c.get("entry") or {}
    if not c.get("python"):
        when = e.get("when")
        if isinstance(when, str):
            when = [when]
        if not when:
            raise CardError("entry.when needs at least one rule")
        e["when"] = when
        order = ORDERS.get(str(e.get("order", "market")).lower())
        if not order:
            raise CardError("entry.order must be market, buy_stop or limit")
        e["order"] = order
        if order != "market" and not e.get("price"):
            raise CardError("a %s order needs entry.price (e.g. \"high * 1.001\")" % order)
        e["valid_bars"] = int(e.get("valid_bars", 1))
        if not c.get("stop"):
            raise CardError("Every trade needs a stop rule (e.g. \"lowest(5) * 0.995\" or \"entry - 2 * atr(14)\")")
    c["entry"] = e
    t = c.get("target")
    if t is not None and not isinstance(t, dict):
        t = {"r": float(t)} if isinstance(t, (int, float)) else {"price": t}
    c["target"] = t
    x = {"time_bars": None, "breakeven_at_r": None, "trail_stop": None, "exit_when": None, "flat_by": None}
    x.update(c.get("exit") or {})
    if cap["product"] == "intraday" and not x.get("flat_by"):
        x["flat_by"] = "15:15"
    if x.get("flat_by") and c["timeframe"] in ("1d", "1w"):
        raise CardError("flat_by is for intraday charts; a %s strategy holds overnight" % c["timeframe"])
    c["exit"] = x
    return c


def profile_from(card):
    """The sizing / backtest profile the engine expects, built from the card."""
    cap, f = card["capital"], card["filters"]
    charges = {"brokerage_per_order": cap["brokerage_per_order"], "dp_per_sell": cap["dp_per_sell"],
               "slippage_pct": cap["slippage_pct"], "product": cap["product"]}
    return {"capital": cap["capital"], "risk_per_trade_pct": cap["risk_per_trade_pct"],
            "max_positions": cap["max_positions"], "max_open_risk_pct": cap["max_open_risk_pct"],
            "max_position_pct": cap["max_position_pct"], "max_per_sector": cap["max_per_sector"],
            "price_ceiling": cap.get("price_ceiling"), "charges": charges,
            "regime_risk": f["regime_risk"] if f["regime"] else {"bullish": 1.0, "neutral": 1.0, "bearish": 1.0},
            "funnel": f}


# ---------------------------------------------------------------- compile

def _p(text, where, extra=()):
    try:
        return parse(text, extra)
    except ExprError as e:
        raise CardError("%s: %s" % (where, e))


def _check_tfs(node, tf, where):
    order = ["5m", "15m", "1h", "2h", "1d", "1w"]
    for h in higher_tfs(node):
        if order.index(h) < order.index(tf):
            raise CardError("%s: %s() is a lower timeframe than the %s chart; only higher ones can be used"
                            % (where, {"1d": "daily", "1w": "weekly", "1h": "hourly"}[h], tf))
        if h == "1h" and tf == "2h":
            raise CardError("%s: hourly() is not available on a 2h chart" % where)


class CardStrategy:
    """A compiled card with the setup interface used by sim.simulate, the scan and the backtest."""

    def __init__(self, card, path=None, setup_dir=None):
        self.card = card
        self.FILE = path or card.get("name", "card")
        self.SETUP_NAME = card["title"]
        self.NAME = card["name"]
        self.tf = card["timeframe"]
        self.intraday_product = card["capital"]["product"] == "intraday"
        x = card["exit"]
        self.flat_by = None
        if x.get("flat_by"):
            hh, mm = str(x["flat_by"]).replace(".", ":").split(":")
            self.flat_by = int(hh) * 100 + int(mm)
        self.filter_rules = [(r, _p(r, "filters.rules")) for r in card["filters"]["rules"]]
        for r, n in self.filter_rules:
            _check_tfs(n, "1d", "filters.rules")
            if uses(n, {"vwap", "time"}):
                raise CardError("filters.rules run on the daily chart: vwap and time belong in entry.when")
        self.py = None
        if card.get("python"):
            from .sim import load_setup
            pth = card["python"]
            if not os.path.isabs(pth) and setup_dir:
                pth = os.path.join(setup_dir, pth)
            self.py = load_setup(pth)
            self.EXIT_RULES = dict(self.py.EXIT_RULES)
            self.WARMUP_BARS = self.py.WARMUP_BARS
            self.when, self.price, self.stop, self.target_price, self.score = [], None, None, None, None
            self.entry = self.py.entry
            if callable(getattr(self.py, "exit", None)):
                self.exit = self.py.exit
            else:
                self.exit = None
            self.reason = getattr(self.py, "DESCRIPTION", "")
            self.order, self.valid = "python", None
            self.chart_overlays = []
            self.trail = self.exit_when = None
            return
        e = card["entry"]
        self.when = [(r, _p(r, "entry.when")) for r in e["when"]]
        for r, n in self.when:
            _check_tfs(n, self.tf, "entry.when")
        self.order = e["order"]
        self.valid = e["valid_bars"]
        self.price = _p(e["price"], "entry.price") if e.get("price") else None
        self.stop = _p(card["stop"], "stop", ("entry",))
        t = card.get("target") or {}
        self.target_r = float(t["r"]) if t.get("r") else None
        self.target_price = _p(t["price"], "target.price", ("entry",)) if t.get("price") else None
        self.score = _p(e["score"], "entry.score") if e.get("score") else None
        self.trail = _p(x["trail_stop"], "exit.trail_stop", TRADE_VARS) if x.get("trail_stop") else None
        self.exit_when = _p(x["exit_when"], "exit.exit_when", TRADE_VARS) if x.get("exit_when") else None
        for n, where in ((self.stop, "stop"), (self.trail, "exit.trail_stop"), (self.exit_when, "exit.exit_when"),
                         (self.price, "entry.price"), (self.target_price, "target.price")):
            if n is not None:
                _check_tfs(n, self.tf, where)
        nodes = [n for _, n in self.when] + [n for n in (self.price, self.stop, self.target_price, self.trail,
                                                         self.exit_when) if n is not None]
        lb = max(lookback(n) for n in nodes)
        self.WARMUP_BARS = max(20, min(lb + 5, 400))
        self.EXIT_RULES = {"target_r": self.target_r, "trail": None, "time_stop": x.get("time_bars"),
                           "breakeven_at_r": x.get("breakeven_at_r")}
        if not (self.trail or self.exit_when or self.flat_by):
            self.exit = None
        self.reason = " and ".join(r for r, _ in self.when)
        self.chart_overlays = []
        seen = set()
        for n in nodes:
            for o in overlays(n):
                if o[0] not in seen:
                    seen.add(o[0])
                    self.chart_overlays.append(o)

    # ---- per-History evaluation context (indicator lists are cached on the History)
    def _ctx(self, h):
        c = getattr(h, "_card_ctx", None)
        if c is None or c[0] is not self:
            c = (self, Ctx(h))
            h._card_ctx = c
        return c[1]

    def passes_filters(self, daily_hist, j):
        """The card's own screening rules on the daily chart at daily bar j. Returns (ok, failed rule)."""
        if not self.filter_rules:
            return True, None
        c = self._ctx(daily_hist)
        for r, n in self.filter_rules:
            if not truthy(ev(n, c, j)):
                return False, r
        return True, None

    def entry(self, b):   # noqa: F811  (replaced by the Python setup's entry when the card uses one)
        h, i = b._h, b.i
        c = self._ctx(h)
        if self.flat_by is not None and len(h.dates[i]) > 10:
            t = int(h.dates[i][11:13] + h.dates[i][14:16])
            if t >= self.flat_by:
                return None
        for _, n in self.when:
            if not truthy(ev(n, c, i)):
                return None
        if self.order == "market":
            entry, ref = None, h.c[i]
        else:
            v = ev(self.price, c, i)
            if isnan(v) or v <= 0:
                return None
            entry = to_tick(v, "up" if self.order == "stop" else "down")
            ref = entry
        c.vars["entry"] = ref
        s = ev(self.stop, c, i)
        if isnan(s) or s <= 0:
            return None
        stop = to_tick(s, "down")
        if stop >= ref:
            return None
        sig = {"type": self.order, "entry": entry, "stop": stop, "valid_days": self.valid, "reason": self.reason}
        if self.target_price is not None:
            tv = ev(self.target_price, c, i)
            if isnan(tv) or tv <= ref:
                return None
            sig["target"] = to_tick(tv, "down")
        if self.score is not None:
            sc = ev(self.score, c, i)
            sig["score"] = None if isnan(sc) else float(sc)
        if self.intraday_product:
            sig["same_day"] = True
        return sig

    def exit(self, b, trade):   # noqa: F811
        h, i = b._h, b.i
        if self.flat_by is not None and len(h.dates[i]) > 10:
            t = int(h.dates[i][11:13] + h.dates[i][14:16])
            if t >= self.flat_by:
                return {"exit": "close", "reason": "flat by %s" % self.card["exit"]["flat_by"]}
        c = self._ctx(h)
        c.vars.update(entry=trade.entry, initial_stop=trade.initial_stop, bars_held=trade.bars_held,
                      highest_close=trade.highest_close, highest_high=trade.highest_high, risk=trade.risk,
                      r_now=trade.r_now)
        out = {}
        if self.trail is not None:
            v = ev(self.trail, c, i)
            if not isnan(v) and v > 0:
                out["stop"] = to_tick(v, "down")
        if self.exit_when is not None and truthy(ev(self.exit_when, c, i)):
            out["exit"] = "next_open"
            out["reason"] = "exit rule: %s" % self.card["exit"]["exit_when"]
        return out or None


def load(p, name):
    path = card_path(p, name)
    card = normalise(read_card(path))
    return card, CardStrategy(card, path, p["strategies"])


# ---------------------------------------------------------------- plain words

def rupees(x):
    from .panel import fmt
    return "Rs " + fmt(x)


def plain_words(card):
    cap, f, e, x = card["capital"], card["filters"], card["entry"], card["exit"]
    u = card["universe"]
    uni = D.UNIVERSES[u["index"]][1] + ((": " + ", ".join(u["symbols"])) if u["index"] == "custom" else "")
    if u.get("exclude"):
        uni += " (excluding %s)" % ", ".join(u["exclude"])
    risk = cap["capital"] * cap["risk_per_trade_pct"] / 100
    lines = ["%s  (%s chart, %s)" % (card["title"], card["timeframe"], uni)]
    if card.get("description"):
        lines.append("  " + card["description"])
    lines.append("MONEY   %s capital, %.2g%% risk a trade = %s; at most %d positions and %.3g%% open risk (%s); "
                 "%.3g%% (%s) in one stock; %d per sector%s; %s."
                 % (rupees(cap["capital"]), cap["risk_per_trade_pct"], rupees(risk), cap["max_positions"],
                    cap["max_open_risk_pct"], rupees(cap["capital"] * cap["max_open_risk_pct"] / 100),
                    cap["max_position_pct"], rupees(cap["capital"] * cap["max_position_pct"] / 100),
                    cap["max_per_sector"], ("; price ceiling %s" % rupees(cap["price_ceiling"])) if cap.get("price_ceiling") else "",
                    "delivery (CNC), held overnight" if cap["product"] == "delivery" else "intraday (MIS), flat by %s" % x.get("flat_by")))
    if f["preset"] == "marketfeed":
        parts = ["marketfeed funnel"]
    else:
        parts = ["your own filters"]
    parts.append("regime gate %s" % ("ON (no new longs when bearish, half risk when sideways)" if f["regime"] else "off"))
    parts.append("top %d sectors" % f["top_sectors"] if f["top_sectors"] else "all sectors")
    if f["min_rs"]:
        parts.append("RS rank >= %s" % f["min_rs"])
    if f["trend"] != "none":
        parts.append({"above200": "above the 200 DMA", "stage2": "close > 50 DMA > 200 DMA"}[f["trend"]])
    parts.append("%s caps" % "/".join(f["caps"]))
    parts.append("price >= Rs %s, traded value >= Rs %s cr/day" % (f["min_price"], f["min_traded_value_cr"]))
    if f["atr_pct_min"] > 0 or f["atr_pct_max"] < 100:
        parts.append("ATR %s-%s%% of price" % (f["atr_pct_min"], f["atr_pct_max"]))
    if f["max_big_gaps"] < 999:
        parts.append("<= %d gaps of 4%%+ in 60 days" % f["max_big_gaps"])
    if f["exclude_surveillance"]:
        parts.append("no ASM/GSM stocks")
    lines.append("FILTER  " + "; ".join(parts) + ".")
    for r in f["rules"]:
        lines.append("        and on the daily chart: %s" % r)
    if card.get("python"):
        lines.append("ENTRY   Python setup %s (rules in its docstring)" % card["python"])
        return "\n".join(lines)
    lines.append("ENTRY   when ALL of these are true on a completed %s bar:" % card["timeframe"])
    for r in e["when"]:
        lines.append("          - %s" % r)
    order = {"market": "buy at the next bar's open",
             "stop": "buy-stop at %s, live %d bar(s)" % (e.get("price"), e["valid_bars"]),
             "limit": "buy-limit at %s, live %d bar(s)" % (e.get("price"), e["valid_bars"])}[e["order"]]
    lines.append("ORDER   %s" % order)
    lines.append("STOP    %s  (rounded down to the NSE tick)" % card["stop"])
    t = card.get("target")
    lines.append("TARGET  %s" % ("%gR (%g x the risk above the fill)" % (t["r"], t["r"]) if t and t.get("r")
                                  else ("%s" % t["price"]) if t and t.get("price") else "none (exits only)"))
    ex = []
    if x.get("time_bars"):
        ex.append("time stop after %d bars" % x["time_bars"])
    if x.get("breakeven_at_r"):
        ex.append("stop to entry at +%gR" % x["breakeven_at_r"])
    if x.get("trail_stop"):
        ex.append("stop trails up to %s" % x["trail_stop"])
    if x.get("exit_when"):
        ex.append("exit at the next open when %s" % x["exit_when"])
    if x.get("flat_by"):
        ex.append("flat by %s" % x["flat_by"])
    lines.append("EXIT    %s" % ("; ".join(ex) if ex else "stop or target only"))
    if e.get("score"):
        lines.append("RANK    when more trades fire than you can take: highest %s first" % e["score"])
    return "\n".join(lines)
