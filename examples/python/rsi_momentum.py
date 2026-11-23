"""RSI momentum with volume (indicators).

    WHEN:   RSI(14) crosses above 60 today, close is above the 20 EMA, volume is at least 1.5x
            the 50-day average, and ADX(14) is above 20 (a trend, not chop).
    ENTER:  buy-stop above today's high, valid 1 session.
    STOP:   2 ATR below the entry.
    EXIT:   target 2R; time stop 15 sessions.

This is the "RSI > 60, price > 20 EMA, volume 1.5x" example: test it before you trust it.
"""

NAME = "RSI 60 momentum"
STYLE = "indicator"
DESCRIPTION = "RSI crosses above 60 with price over the 20 EMA, 1.5x volume and ADX above 20."
WARMUP = 210


def entry(b):
    rsi = b.rsi(14)
    if not b.crossed_above(rsi, 60):
        return None
    adx, _, _ = b.adx(14)
    if not (b.close[0] > b.ema(20)[0] and b.volume_ratio(50) >= 1.5 and adx[0] > 20):
        return None
    trigger = b.high[0] + 0.05
    stop = trigger - 2 * b.atr(14)[0]
    return {"type": "stop", "entry": trigger, "stop": stop, "valid_days": 1,
            "reason": "RSI %.0f crossed 60, %.1fx volume, ADX %.0f" % (rsi[0], b.volume_ratio(50), adx[0])}


EXIT = {"target_r": 2.0, "trail": None, "time_stop": 15, "breakeven_at_r": None}
