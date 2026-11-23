"""Risk: Indian delivery (CNC) charges and position sizing from the user's own rules.

Sizing is arithmetic on the numbers the user gave (capital, risk per trade, caps). It is not
advice on what to buy.

Default charges are for a discount broker's equity delivery trade on NSE, as of 2026. They change;
the user can override every one in profile.json -> charges.
"""

import math

DEFAULT_CHARGES = {
    "brokerage_per_order": 0.0,     # Rs per executed order (many discount brokers: 0 for delivery)
    "brokerage_pct": 0.0,           # % of order value, if your broker charges that way
    "stt_pct": 0.1,                 # Securities Transaction Tax, delivery: 0.1% on buy and on sell
    "exchange_pct": 0.00297,        # NSE transaction charge
    "sebi_per_crore": 10.0,         # SEBI turnover fee
    "stamp_buy_pct": 0.015,         # stamp duty on the buy side
    "gst_pct": 18.0,                # GST on brokerage + exchange + SEBI fees
    "dp_per_sell": 15.93,           # depository charge per stock per day you sell (approx; check your broker)
    "slippage_pct": 0.10,           # assumed slippage on each market or stop fill
}

DEFAULT_PROFILE = {
    "capital": None,
    "risk_per_trade_pct": 1.0,
    "max_positions": 5,
    "max_open_risk_pct": 5.0,
    "max_position_pct": 25.0,
    "max_per_sector": 2,
    "price_ceiling": None,
    "holding_days": 15,
    "charges": DEFAULT_CHARGES,
    "regime_risk": {"bullish": 1.0, "neutral": 0.5, "bearish": 0.0},
    "funnel": {
        "universe": "nifty500",
        "caps": ["large", "mid", "small"],
        "min_price": 50,
        "min_traded_value_cr": 10,
        "atr_pct_min": 1.0,
        "atr_pct_max": 6.0,
        "max_big_gaps": 3,
        "exclude_bands": ["2", "5"],
        "exclude_surveillance": True,
        "top_sectors": 3,
        "leader_rs": 95,
        "min_rs": 70,
        "trend": "above200",
        "min_rr": 2.0,
        "vix_high": 22,
        "bull_breadth": 50,
        "bear_breadth": 40,
        "event_days": 10,
    },
}


def merged_profile(user):
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_PROFILE.items()}
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k].update(v)
        else:
            p[k] = v
    return p


def charges(buy_value, sell_value, cfg=None, sells=1):
    """Total round-trip charges in rupees for one buy of buy_value and sell(s) totalling sell_value."""
    c = dict(DEFAULT_CHARGES)
    c.update(cfg or {})
    turnover = buy_value + sell_value
    if c.get("product") == "intraday":
        # MIS: brokerage is usually 0.03% or Rs 20 an order, whichever is lower; STT 0.025% on the
        # sell side; stamp duty 0.003% on the buy; no DP charge (nothing reaches the demat account)
        cap_ = c.get("intraday_brokerage_cap", 20.0)
        pct_ = c.get("intraday_brokerage_pct", 0.03)
        brokerage = min(cap_, buy_value * pct_ / 100) + min(cap_, sell_value * pct_ / 100)
        stt = sell_value * 0.025 / 100
        stamp = buy_value * 0.003 / 100
        dp = 0.0
    else:
        brokerage = 2 * c["brokerage_per_order"] + turnover * c["brokerage_pct"] / 100
        stt = turnover * c["stt_pct"] / 100
        stamp = buy_value * c["stamp_buy_pct"] / 100
        dp = c["dp_per_sell"] * sells if sell_value > 0 else 0
    exch = turnover * c["exchange_pct"] / 100
    sebi = turnover / 1e7 * c["sebi_per_crore"]
    gst = (brokerage + exch + sebi) * c["gst_pct"] / 100
    total = brokerage + stt + exch + sebi + stamp + gst + dp
    return {"total": total, "brokerage": brokerage, "stt": stt, "exchange": exch, "sebi": sebi,
            "stamp": stamp, "gst": gst, "dp": dp}


def size_position(entry, stop, profile, regime_multiplier=1.0, open_risk=0.0, capital=None, target=None):
    """Quantity from: risk per trade x regime multiplier / (entry - stop), capped by max position size
    and by what is left of the max open risk. Returns every number so the user sees the arithmetic."""
    cap_total = capital if capital is not None else profile["capital"]
    if not cap_total:
        return {"ok": False, "why": "no capital in the profile yet"}
    rps = entry - stop
    if rps <= 0:
        return {"ok": False, "why": "the stop is not below the entry"}
    base_risk = cap_total * profile["risk_per_trade_pct"] / 100
    risk_amt = base_risk * regime_multiplier
    if risk_amt <= 0:
        return {"ok": False, "why": "the market regime allows no new long trades (risk multiplier 0)"}
    q_risk = math.floor(risk_amt / rps)
    max_pos = cap_total * profile["max_position_pct"] / 100
    q_cap = math.floor(max_pos / entry)
    heat_left = cap_total * profile["max_open_risk_pct"] / 100 - open_risk
    q_heat = math.floor(max(0.0, heat_left) / rps)
    qty = min(q_risk, q_cap, q_heat)
    limit = "risk per trade" if qty == q_risk else "max position size" if qty == q_cap else "max open risk"
    if qty < 1:
        return {"ok": False, "why": "fewer than 1 share fits (%s is the limit)" % limit,
                "risk_amount": risk_amt, "risk_per_share": rps}
    value = qty * entry
    out = {"ok": True, "qty": qty, "entry": entry, "stop": stop, "risk_per_share": rps,
           "risk_amount_allowed": risk_amt, "risk_rupees": qty * rps, "position_value": value,
           "position_pct": value / cap_total * 100, "limited_by": limit,
           "regime_multiplier": regime_multiplier, "base_risk": base_risk}
    ch = profile.get("charges")
    if target:
        reward = qty * (target - entry)
        cost_t = charges(value, qty * target, ch)["total"]
        out.update({"target": target, "reward_rupees": reward, "rr": (target - entry) / rps,
                    "net_at_target": reward - cost_t, "net_r_at_target": (reward - cost_t) / (qty * rps)})
    cost_s = charges(value, qty * stop, ch)["total"]
    out["net_at_stop"] = -qty * rps - cost_s
    out["charges_round_trip_est"] = cost_s
    return out
