"""Pullback to a rising 20 EMA (price action, trend continuation).

    WHEN:   the stock is in an uptrend (close > 50 SMA > 200 SMA, 20 EMA rising), it pulled back
            to touch the 20 EMA in the last 3 sessions, and today closed green above yesterday's high.
    ENTER:  buy-stop just above today's high, valid 2 sessions.
    STOP:   below the lowest low of the pullback (last 5 sessions), at least 1 ATR below entry.
    EXIT:   trail: exit at the next open after a close below the 20 EMA; time stop 25 sessions.
"""

NAME = "Pullback to rising 20 EMA"
STYLE = "price action"
DESCRIPTION = "Uptrend stock dips to its rising 20 EMA, then closes back above the prior day's high."
WARMUP = 210


def entry(b):
    e20, s50, s200 = b.ema(20), b.sma(50), b.sma(200)
    uptrend = b.close[0] > s50[0] > s200[0] and e20[0] > e20[5]
    if not uptrend:
        return None
    touched = any(b.low[k] <= e20[k] * 1.005 for k in range(0, 3))
    turn = b.is_green(0) and b.close[0] > b.high[1]
    if not (touched and turn):
        return None
    atr = b.atr(14)[0]
    trigger = b.high[0] + 0.05
    stop = min(b.lowest(5)[0], trigger - atr) - 0.05
    return {"type": "stop", "entry": trigger, "stop": stop, "valid_days": 2,
            "reason": "touched the rising 20 EMA, closed above yesterday's high"}


EXIT = {"target_r": None, "trail": "ema:20", "time_stop": 25, "breakeven_at_r": None}
