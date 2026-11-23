"""Bullish engulfing at support in an uptrend (candlestick).

    WHEN:   the stock is above its 200 SMA, the last 3 sessions pulled back (lower closes), and
            today is a bullish engulfing candle whose low is within 1 ATR of the 50 SMA or of the
            last confirmed swing low (support).
    ENTER:  buy-stop above the engulfing candle's high, valid 2 sessions.
    STOP:   below the engulfing candle's low.
    EXIT:   target 2R; trail under the last swing low; time stop 20 sessions.
"""

NAME = "Engulfing at support"
STYLE = "candlestick"
DESCRIPTION = "Bullish engulfing candle near the 50 SMA or a swing low, in an uptrend."
WARMUP = 210


def entry(b):
    if not b.close[0] > b.sma(200)[0]:
        return None
    if not (b.close[1] < b.close[2] < b.close[3]):
        return None
    if not b.bullish_engulfing(0):
        return None
    atr = b.atr(14)[0]
    s50 = b.sma(50)[0]
    swing = b.last_pivot_low(5, 5)
    near = abs(b.low[0] - s50) <= atr or (swing == swing and abs(b.low[0] - swing) <= atr)
    if not near:
        return None
    return {"type": "stop", "entry": b.high[0] + 0.05, "stop": b.low[0] - 0.05, "valid_days": 2,
            "reason": "bullish engulfing at support after a 3-day pullback"}


EXIT = {"target_r": 2.0, "trail": "pivot", "time_stop": 20, "breakeven_at_r": None}
