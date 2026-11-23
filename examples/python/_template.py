"""Your setup, in plain words first (Claude fills this in with you):

    WHEN:   <the condition on the daily chart, e.g. "close breaks above the 20-day high on 1.5x volume">
    ENTER:  <market at next open / buy-stop above X / buy-limit at Y>, valid N sessions
    STOP:   <where you are wrong, e.g. "below the 10-day low">
    EXIT:   <target at 2R / trail under the 20 EMA / time stop after 15 sessions>

Rules for writing it (see references/setup-api.md):
  - b.close[0] is the bar that just closed, b.close[1] the one before. Never negative indexes.
  - Return None for "no trade", or a dict with at least a "stop" below the entry.
  - Decide everything from the daily bar that has closed. You place the order for tomorrow.
"""

NAME = "My setup"
STYLE = "price action"          # free text: indicator / price action / breakout / candlestick / catalyst / mixed
DESCRIPTION = "One line a stranger understands."
WARMUP = 200                    # bars of history needed before the rules can work


def entry(b):
    # ---- the condition
    if not (b.close[0] > b.ema(20)[0]):
        return None
    # ---- the order
    trigger = b.high[0]                       # buy-stop above today's high
    stop = b.lowest(5)[0]                     # below the 5-day low
    return {
        "type": "stop",                       # "market" (next open), "stop" (buy-stop), "limit" (buy-limit)
        "entry": trigger,                     # omit for "market"
        "stop": stop,
        "valid_days": 1,                      # how many sessions the order stays live
        "reason": "close above the 20 EMA",   # shown in the report
    }


EXIT = {
    "target_r": 2.0,          # fixed target at 2R; None for no fixed target
    "trail": None,            # None | "ema:20" | "low:10" | "atr:3" | "pivot"
    "time_stop": 20,          # exit at the close after N bars; None to disable
    "breakeven_at_r": None,   # e.g. 1.0: move the stop to the entry at +1R
}


# Optional. Called at every close while the trade is open. Return None, or
#   {"exit": "close" | "next_open", "reason": "..."}   to get out
#   {"stop": price}                                   to raise the stop (never lowered)
# def exit(b, trade):
#     if trade.bars_held >= 5 and trade.r_now < 0.5:
#         return {"exit": "next_open", "reason": "not working after 5 days"}
#     return None
