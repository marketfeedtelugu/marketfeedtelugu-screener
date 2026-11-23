# Python setups (the fallback)

Use a strategy card's rules first (see rules.md). Write Python only when a card cannot express the
idea (multi-step state, custom pattern code). Put the file in `<workspace>/strategies/<name>.py` and point
the card at it with `"python": "<name>.py"`. The card still supplies the universe, timeframe, capital and
filters; the Python file replaces entry, stop, target and exits. Copy one from the skill's
`examples/python/` folder. The scan, the backtester and the forward test all call the same functions.
On intraday charts, `b.date[0]` is "YYYY-MM-DD HH:MM" and one bar is one candle of the card's timeframe.

```python
NAME = "Pullback to rising 20 EMA"      # shown in reports
STYLE = "price action"                  # free text
DESCRIPTION = "One line."
WARMUP = 210                            # bars needed before the rules can work (default 200)

def entry(b):                           # called once per stock per completed bar
    ...
    return None                         # no trade
    return {"type": "stop", "entry": 1234.5, "stop": 1180.0, "valid_days": 2,
            "target": None, "reason": "why it fired", "score": 1.0}

EXIT = {"target_r": 2.0, "trail": None, "time_stop": 20, "breakeven_at_r": None}

def exit(b, trade):                     # optional, called at each close while the trade is open
    return None
```

## The signal dict

| key | meaning |
|---|---|
| `stop` | **required**. Below the entry, or the signal is ignored |
| `type` | `"market"` buys at the next open (no `entry` needed); `"stop"` is a buy-stop that fills when the high reaches `entry` (at the open if it gaps above); `"limit"` is a buy-limit that fills when the low reaches `entry` |
| `entry` | the order price for stop / limit orders |
| `valid_days` | sessions the order stays live (default 1) |
| `target` | a fixed target price (otherwise `EXIT["target_r"]` × risk, or none) |
| `reason` | text shown in the report |
| `score` | optional ranking when more signals fire than the portfolio can take (higher first; RS rank breaks ties) |

## Exits

`EXIT` keys: `target_r` (fixed target in R, or None), `trail` (`"ema:20"`: exit at the next open
after a close below the 20 EMA; `"low:10"`: stop trails the 10-day low; `"atr:3"`: stop trails 3 ATR
under the highest close; `"pivot"`: stop trails the last confirmed swing low), `time_stop` (exit at
the close after N bars), `breakeven_at_r` (stop to entry once the high reaches +N R). The stop only
ever moves up.

`exit(b, trade)` sees `trade.entry`, `.stop`, `.initial_stop`, `.target`, `.bars_held`, `.risk`
(per share), `.close`, `.r_now`, `.highest_close`, `.highest_high`, `.entry_date`. Return
`{"exit": "close"}` or `{"exit": "next_open"}` (with an optional `"reason"`), `{"stop": price}`,
`{"target": price}`, or None.

Simulation is conservative: a gap below the stop fills at the open; a bar that touches both the stop
and the target counts as a stop; market and stop fills pay slippage (profile, default 0.1 %).

## What `b` gives you

Indexing is bars ago, like Pine Script: `b.close[0]` is the bar that just closed and `b.close[1]` the
one before. **Negative indexes raise LookaheadError**, so a setup cannot see the future. A value with
not enough history is `nan`, and any comparison with `nan` is False.

**Price**: `b.open`, `b.high`, `b.low`, `b.close`, `b.volume`, `b.date` (strings `YYYY-MM-DD`),
`b.index_close` (Nifty 500), `b.rs_rank` (1-99 RS percentile that day), `b.bars_available`,
`b.symbol`, `b.meta` (`name`, `sector`, `cap`, `band`), `b.ctx` (`regime`, `sector_rank`).

**Indicators** (all return a series, use `[0]`, `[1]`, ...):
`b.sma(n, src="close")`, `b.ema(n, src)`, `b.rsi(n=14)`, `b.atr(n=14)`, `b.atr_pct(n)`,
`b.macd(12, 26, 9)` → (line, signal, hist), `b.adx(14)` → (adx, +DI, -DI),
`b.supertrend(10, 3)` → (line, direction +1/-1), `b.bollinger(20, 2)` → (mid, upper, lower),
`b.highest(n, src="high")`, `b.lowest(n, src="low")` (both include today: use `[1]` for "before
today"), `b.avg_volume(n)`, `b.avg_traded_value(n)`, `b.roc(n)`.
`src` can be `close`, `open`, `high`, `low`, `volume`, `hl2`, `hlc3`, `traded_value`.

**Numbers at an offset**: `b.pct_change(n, k=0)`, `b.gap_pct(k)`, `b.range_pct(n, k)` (base
tightness), `b.volume_ratio(n=50, k=0)` (volume vs the n days before), `b.rs_vs_index(n, k)`,
`b.crossed_above(a, b_or_number, k)`, `b.crossed_below(...)`.

**Swing points** (confirmed pivots, known only `right` bars later, so no lookahead):
`b.pivot_highs(left=5, right=5, lookback=250)` / `b.pivot_lows(...)` → `[(bars_ago, price), ...]`,
newest first; `b.last_pivot_high()`, `b.last_pivot_low()`, `b.higher_highs_lows()`.

**Candles** (k = bars ago): `b.body(k)`, `b.candle_range(k)`, `b.upper_wick(k)`, `b.lower_wick(k)`,
`b.is_green(k)`, `b.is_red(k)`, `b.close_position(k)` (0 = closed at the low, 1 = at the high),
`b.doji(k)`, `b.hammer(k)`, `b.bullish_engulfing(k)`, `b.bullish_harami(k)`, `b.piercing_line(k)`,
`b.morning_star(k)`, `b.three_white_soldiers(k)`, `b.inside_bar(k)`, `b.narrow_range(n=7, k)`.

## Translating a trader's words

| They say | Ask / write |
|---|---|
| "RSI above 60" | crosses above (`b.crossed_above(b.rsi(14), 60)`) or stays above (`b.rsi(14)[0] > 60`)? |
| "breakout" | above what? the 20-day high before today is `b.highest(20)[1]`; on a close or intraday? |
| "volume spike" | `b.volume_ratio(50) >= 1.5`: which multiple, and against which average? |
| "near support" | which support (a swing low, the 50 DMA)? how near (within 1 ATR, within 2 %)? |
| "uptrend" | `close > sma50 > sma200`? a rising 20 EMA (`b.ema(20)[0] > b.ema(20)[5]`)? higher highs and lows? |
| "stop below the low" | today's low, the 5-day low (`b.lowest(5)[0]`), or the last swing low? Add a small buffer |
| "news / results" | the setup cannot read news. Use the price footprint (gap + volume + hold, see `gap_and_hold`) and check the actual news by hand before acting |

Keep the rules few. Every extra condition fits the past a little better and the future a little
worse.
