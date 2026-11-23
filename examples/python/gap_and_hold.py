"""Gap and hold after a catalyst (news validated by price).

News is the reason a stock moves; price tells you whether the market believes it. This setup never
reads headlines. It trades the footprint a real catalyst leaves (results, order win, policy):

    WHEN:   3 sessions ago the stock gapped up at least 3% on at least 2x its 50-day volume and
            closed in the upper half of that day's range; in the 2 sessions since, no close
            below that gap day's low (the gap held).
    ENTER:  buy-stop above the highest high since the gap day, valid 2 sessions.
    STOP:   below the gap day's low.
    EXIT:   trail under the 10-day low; time stop 20 sessions.

Before trading a hit, read what the catalyst was (NSE announcements): a one-off gain or a block
deal is not the same as an earnings upgrade.
"""

NAME = "Gap and hold"
STYLE = "catalyst"
DESCRIPTION = "A 3%+ gap on 2x volume that held for 2 sessions: news confirmed by price."
WARMUP = 210


def entry(b):
    g = 2                                  # the gap day was 2 bars before today
    if b.gap_pct(g) < 3 or b.volume_ratio(50, g) < 2 or b.close_position(g) < 0.5:
        return None
    gap_low = b.low[g]
    if any(b.close[k] < gap_low for k in range(0, g)):
        return None
    if b.gap_pct(0) > 3 or b.gap_pct(1) > 3:   # a second gap: different situation
        return None
    hi = max(b.high[k] for k in range(0, g + 1))
    return {"type": "stop", "entry": hi + 0.05, "stop": gap_low - 0.05, "valid_days": 2,
            "reason": "gapped %.1f%% on %.1fx volume on %s and held" % (b.gap_pct(g), b.volume_ratio(50, g), b.date[g])}


EXIT = {"target_r": None, "trail": "low:10", "time_stop": 20, "breakeven_at_r": None}
