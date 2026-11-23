"""Breakout from a tight base on volume (range breakout).

    WHEN:   the last 20 sessions before today stayed inside a range no wider than 15% of price,
            today closed above that range's high, in the upper third of its bar, on volume at
            least 1.5x the 50-day average, and the stock is above its 200 SMA.
    ENTER:  at the next open (market).
    STOP:   the base's midpoint or 2 ATR below the close, whichever is closer (tighter).
    EXIT:   target 3R; breakeven at +1.5R; time stop 20 sessions.
"""

NAME = "Tight base breakout"
STYLE = "range breakout"
DESCRIPTION = "Close above a tight 20-day base on 1.5x volume, in an uptrend."
WARMUP = 210


def entry(b):
    if not b.close[0] > b.sma(200)[0]:
        return None
    base_hi = b.highest(20)[1]          # 20 sessions BEFORE today
    base_lo = b.lowest(20)[1]
    if (base_hi - base_lo) / b.close[1] > 0.15:
        return None
    if not (b.close[0] > base_hi and b.close_position(0) >= 0.66 and b.volume_ratio(50) >= 1.5):
        return None
    atr = b.atr(14)[0]
    stop = max((base_hi + base_lo) / 2, b.close[0] - 2 * atr)
    return {"type": "market", "stop": stop,
            "reason": "closed above a %.0f%% 20-day base on %.1fx volume"
                      % ((base_hi - base_lo) / b.close[1] * 100, b.volume_ratio(50))}


EXIT = {"target_r": 3.0, "trail": None, "time_stop": 20, "breakeven_at_r": 1.5}
