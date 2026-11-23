# The rule language and the strategy card

A strategy card is one JSON file in `<workspace>/strategies/<name>.json`. The scan, the backtest and
the forward test all run the same compiled card, so what the user approves is exactly what is tested.
`screener.py rules` prints the full vocabulary. `screener.py card show NAME` reads a card back in plain
words.

## Card fields

| field | what it holds | default |
|---|---|---|
| `name` | file name: letters, digits, underscores | required |
| `title`, `author`, `description` | shown in the dashboard; description = one line a stranger understands | |
| `universe` | `"nifty50"`, `"nifty100"`, `"midcap150"`, `"fno"`, `"bank"`... (`screener.py universes`), or `{"index": "custom", "symbols": ["RELIANCE", "TCS"]}`. `"exclude": [...]` drops symbols | `nifty500` |
| `timeframe` | the chart the entry rules run on: `5m` `15m` `1h` `2h` `1d` `1w` | `1d` |
| `capital.capital` | rupees set aside for this strategy | required |
| `capital.risk_per_trade_pct` | % of capital lost if a stop is hit (0-5) | 1 |
| `capital.max_positions`, `max_open_risk_pct`, `max_position_pct`, `max_per_sector` | portfolio limits | 5, 5, 25, 2 |
| `capital.price_ceiling` | optional max share price (a personal limit) | none |
| `capital.product` | `delivery` (CNC, held overnight) or `intraday` (MIS: flat by 15:15, intraday charges) | delivery |
| `capital.brokerage_per_order`, `dp_per_sell`, `slippage_pct` | costs | 0, 15.93, 0.1 |
| `filters.preset` | `marketfeed` (the funnel) or `custom` (only basic tradability + the user's rules) | marketfeed |
| `filters.rules` | the user's own screening rules, evaluated on the DAILY chart | [] |
| `filters.<key>` | override any preset value (table below) | |
| `entry.when` | list of rules; ALL must be true on a completed bar of `timeframe` | required |
| `entry.order` | `market` (next bar's open), `buy_stop` (fills when the high reaches `price`; at the open if it gaps above), `limit` (fills when the low reaches `price`) | market |
| `entry.price` | rule for the order price, e.g. `"high * 1.001"` | for buy_stop / limit |
| `entry.valid_bars` | bars the order stays live (intraday orders also expire at the close) | 1 |
| `entry.score` | rule to rank signals when more fire than the portfolio can take, e.g. `"rs_rank"` | none |
| `stop` | rule for the stop; `entry` is the order price (or the close for market orders). Rounded down to the NSE tick | required |
| `target` | `{"r": 2}` (2 x the risk above the fill), `{"price": "highest(250)"}`, or `null` | null |
| `exit.time_bars` | exit at the close after N bars | none |
| `exit.breakeven_at_r` | stop to entry once the high reaches +N R | none |
| `exit.trail_stop` | rule the stop ratchets up to every bar (never down), e.g. `"lowest(10)"`, `"highest_close - 3 * atr(14)"` | none |
| `exit.exit_when` | rule that exits at the next bar's open, e.g. `"close < ema(20)"` | none |
| `exit.flat_by` | intraday time to be out, e.g. `"15:15"` (set automatically for `product: intraday`) | none |
| `python` | a setup `.py` in `strategies/` for rules the card cannot express (see python-setups.md); it replaces entry / stop / target / exit | none |

Inside `trail_stop` and `exit_when` these trade values also exist: `entry`, `initial_stop`, `bars_held`,
`highest_close`, `highest_high`, `risk`, `r_now`.

### Filter presets

| key | marketfeed | custom | meaning |
|---|---|---|---|
| `regime` | true | false | Nifty 500 trend + breadth + VIX gate: no new longs when bearish, half risk when sideways |
| `regime_risk` | 1 / 0.5 / 0 | 1 / 1 / 1 | risk multiplier for bullish / neutral / bearish |
| `top_sectors` | 3 | 0 | only the N strongest sectors (relative strength vs the Nifty 500, built from each sector's stocks); 0 = off |
| `leader_rs` | 95 | 95 | an RS leader at or above this passes outside the top sectors |
| `min_rs` | 70 | 0 | minimum RS rank 1-99 (1/3/6-month momentum vs the market); 0 = off |
| `trend` | above200 | none | `above200`, `stage2` (close > 50 DMA > 200 DMA) or `none` |
| `caps` | large, mid, small | + micro | cap buckets (large = Nifty 100, mid = Midcap 150, small = Smallcap 250) |
| `min_price` | 50 | 20 | rupees |
| `min_traded_value_cr` | 10 | 2 | 20-day median traded value, rupee crore a day |
| `atr_pct_min` / `atr_pct_max` | 1 / 6 | 0 / 100 | daily ATR as % of price |
| `max_big_gaps` | 3 | 999 | gaps of 4%+ in 60 sessions |
| `exclude_surveillance` | true | true | NSE ASM/GSM lists |
| `exclude_bands` | 2, 5 | 2, 5 | price bands (a stop may not fill in a circuit) |
| `min_rr` | 2 | 0 | minimum reward:risk to the target and to the next resistance; 0 = off |
| `check_resistance` | true | false | reject when the next swing high is closer than `min_rr` |
| `min_bars` | 150 | 60 | daily history needed |

Filters are daily facts. On an intraday chart they use the PREVIOUS session's daily bar (today's daily
bar is not complete until 15:30). The backtest does exactly the same.

## Writing rules

- `close[1]` is the bar before; `[k]` is k bars ago. Negative indexes are rejected (no future).
- Comparisons chain: `close > sma(50) > sma(200)`.
- `x crosses above y` is true only on the bar where it happened.
- `highest(20)` INCLUDES the current bar. "Breaks above the 20-bar high" is `close > highest(20)[1]`.
- `daily(...)`, `weekly(...)`, `hourly(...)` evaluate on a higher timeframe, as of its last COMPLETED bar
  (a week completes on its last session; an hour at :15). Lower timeframes than the chart are refused.
- A value without enough history is nan and every comparison with nan is False, so rules simply do
  not fire during warm-up. The warm-up is worked out from the rules.
- Stops and order prices are rounded to the NSE tick (₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to
  ₹5,000, ₹0.50 to ₹10,000, ₹1 to ₹20,000, ₹5 above).

## Translating a trader's words

| They say | Ask, then write |
|---|---|
| "RSI above 60" | crosses above (`rsi(14) crosses above 60`) or stays above (`rsi(14) > 60`)? Which length? |
| "breakout" | above what? `close > highest(20)[1]` (20-bar high), `close > highest(250)[1]` (52-week), `close > pivot_high()` (last swing high). On a close, or intrabar (`entry.order: buy_stop`, `price: highest(20)`)? |
| "volume spike" | `volume > 1.5 * avg_volume(50)`: which multiple, which average? |
| "uptrend" | `close > sma(50) > sma(200)`? `rising(ema(20), 5)`? `higher_highs_lows()`? `weekly(close > sma(30))`? |
| "pullback to the 20 EMA" | how near, how recently? `any(low <= ema(20) * 1.005, 3)` |
| "near support" | which support (`pivot_low()`, `sma(50)`)? how near? `near(low, sma(50), 1)` |
| "tight base / VCP" | `range_pct(15) < 8`, `volume < avg_volume(50)`, `bb_width(20, 2) < 6` |
| "MACD crossover" | `macd() crosses above macd_signal()`, above zero? `macd() > 0` |
| "Supertrend buy" | `supertrend_dir(10, 3) == 1 and supertrend_dir(10, 3)[1] == -1` |
| "bullish engulfing at support" | `bullish_engulfing and near(low, pivot_low(), 2)` |
| "opening range breakout" | `close > orh(15) and close[1] <= orh(15)`, before when? `time <= 1045` |
| "above VWAP" | `close > vwap` (intraday charts only) |
| "strong vs Nifty" | `rs_rank >= 80` or `rs_vs_index(63) > 0` |
| "stop below the low" | which low: this bar (`low`), the 5-bar low (`lowest(5)`), the swing low (`pivot_low()`)? buffer? `lowest(5) * 0.995` |
| "stop at 2 ATR" | `entry - 2 * atr(14)` |
| "8% stop" | `entry * 0.92` |
| "trail it" | `exit.trail_stop: "lowest(10)"`, `"ema(20)"`, `"highest_close - 3 * atr(14)"`, or `exit.exit_when: "close < ema(20)"` |
| "news / results / order wins" | the rules cannot read news: use the price footprint (`gap_pct > 3 and volume > 2 * avg_volume(50) and close_position > 0.6`), and read the actual news before acting |
| "PE, ROE, sales growth" | fundamentals are not in this data: screen the universe by hand or pick an index that fits |

Keep the rules few. Every extra condition fits the past a little better and the future a little worse.
