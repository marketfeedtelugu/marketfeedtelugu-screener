---
name: marketfeed-screener
description: Build-your-own stock screener for Indian stocks (NSE). Claude interviews the trader - universe (Nifty 50, Next 50, 100, 200, 500, Midcap 150, Smallcap 250, F&O stocks, sector indices or their own list), chart timeframe (5m, 15m, 1h, 2h, daily, weekly), capital and risk rules, the marketfeed funnel preset or their own filters, and their exact entry, stop, target and exit rules - and writes it all into one shareable strategy card (readable JSON rules like "rsi(14) crosses above 60", "weekly(close > sma(30))"; Python only as a fallback). Then it pulls the data (free NSE lists, filings and Yahoo bars, Zerodha Kite history optional), screens the universe, validates and sizes every trade plan, checks NSE filings and Google News India headlines for each plan (red flags turn it to REVIEW), backtests the card after Indian charges with in-sample / out-of-sample and regime splits, runs a frozen forward test (paper trades on new bars), and writes an interactive dashboard with the funnel, the trade plans and a chart per stock with entry, stop, target, resistance and past signals marked. Use when the user says "build my screener", "screen stocks with my conditions", "find stocks that match my setup", "scan Nifty 100 for X", "my entry and exit rules", "make a strategy card", "backtest my conditions", "forward test my strategy", "show me the chart with entry and stop", "any news on my trade plans", or types /marketfeed-screener.
---

# marketfeed screener

A screener template for anyone. The **trader** brings their universe, capital, risk rules, filters and
entry / exit conditions. This skill connects everything else: it pulls the data, screens the universe,
marks entry, stop and target on every chart, sizes the trade from their rules, checks NSE filings and
news headlines for every trade plan, backtests the rules, and forward tests them on new bars.

The goal is not to predict which stock goes up. It finds the stocks that match the trader's **own**
rules, exactly and repeatably, and shows honestly whether those rules have worked.

Education and tooling, not investment advice. It never places an order.

`SKILL_DIR` is this skill's folder. Run everything with:

```bash
python3 "SKILL_DIR/screener.py" <command>
```

Workspace: `~/marketfeed-screener` (or `$MF_SCREENER_HOME`, or `--home DIR`): `strategies/` (cards),
`cache/`, `runs/`, `backtests/`, `forward/`. Python 3.8+, no packages to install.

## How it fits together

| Step | Command | Output |
|---|---|---|
| 1. Interview → strategy card | `card new NAME --from EXAMPLE`, edit, `card show NAME` | `strategies/NAME.json` |
| 2. Check the rules compile and fire | `card check NAME` | signal frequency, sample signals |
| 3. Data the card needs | `data --strategy NAME` | universe list, daily bars, intraday bars, NSE lists |
| 4. Backtest | `backtest --strategy NAME` | verdict, per-trade R, IS/OOS, regimes, years, portfolio vs Nifty 500 |
| 5. Screen today | `scan --strategy NAME` | funnel, trade plans, News check, `runs/NAME/<bar>/dashboard.html` |
| 6. Forward test | `forward start --strategy NAME` | frozen card; every later scan updates the paper trades |

Run `status` first: it shows what exists. `universes` lists every universe, `rules` the whole rule
vocabulary, `card list` the examples.

## Step 1: the interview. ASK, in this order; never assume

Use AskUserQuestion in batches, typical value as the first option, one line of explanation each. Never
guess capital, risk or rules. If the user pastes everything at once, fill the card from it and ask only
about the gaps.

**Batch 1: where and on what chart**
1. **Universe**: Nifty 50, Next 50, 100, 200, 500, Midcap 150, Smallcap 250, F&O stocks, a sector index
   (Bank, IT, Pharma, Auto, FMCG, Metal, Realty, Energy, PSU Bank, Defence...), or their own list
   (`screener.py universes`). "Nifty 150" usually means Midcap 150: confirm.
2. **Timeframe** for the entry rules: 5m, 15m, 1h, 2h, daily, weekly. Say the data limits: free Yahoo
   data keeps ~60 days of 5m/15m and ~2-3 years of 1h; daily and weekly go back 10 years. With Zerodha
   Kite connected, longer intraday history can be imported (see "Kite").
3. **Product**: swing (delivery, held overnight) or intraday (MIS, flat by 15:15).

**Batch 2: money and risk** (read it back in rupees afterwards)
4. **Capital** for this strategy (only money they can afford to lose).
5. **Risk per trade** % (typical 0.5-1; 2 is aggressive).
6. **Max open positions** and **max total open risk** % (typical 4-6 and 4-6%).
7. **Max in one stock** % (typical 20-25) and **max per sector** (typical 2).
8. Optional: price ceiling, brokerage per order, DP charge (defaults ₹0 and ₹15.93).

**Batch 3: filters, the method**
9. **marketfeed funnel** (our method: market regime gate, top-3 sectors by relative strength, RS rank
   70+, above the 200 DMA, liquidity ≥ ₹10 cr/day, ATR 1-6%, no ASM/GSM; shown layer by layer) **or
   their own filters** (only basic tradability, plus any daily-chart rules they give, e.g. "above the
   200 DMA", "weekly uptrend", "RS above 80"). Either way, any single layer can be switched on or off or
   changed (`filters.<key>`, see references/rules.md).

**Batch 4: the setup, until every part is exact** (a vague rule cannot be tested)
10. **WHEN**: each entry condition with numbers. "RSI above 60": crosses above, or stays above? Which
    length? On which chart?
11. **ENTER**: next bar's open, a buy-stop above a level, or a buy-limit at a level; how many bars it
    stays live.
12. **STOP**: where the idea is wrong (below the signal low, the swing low, N × ATR, a %), and buffer.
13. **TARGET and EXIT**: fixed R target, a price target, a trailing stop, an exit rule ("close below the
    20 EMA"), breakeven, a time stop.
14. **Rank** (optional): when several fire at once, which first (RS rank, volume ratio...)?

Translate the words with references/rules.md (the "Translating a trader's words" table) and write the
card: `card new NAME --from <closest example>`, then edit `strategies/NAME.json`. Examples:
`marketfeed_momentum` (our funnel preset), `ema_pullback`, `breakout_52w_weekly` (weekly filter),
`hourly_trend_pullback` (1h chart, daily filter), `orb_15m_intraday` (15m, intraday product), `_template`.
They are examples, not recommendations; several lose money after costs and the backtest says so.

Then **read the card back** with `card show NAME` (plain words: money, filters, entry, order, stop,
target, exits) and list every assumption you made. Get a clear yes before testing. If a rule truly
cannot be written as a card rule, write a Python setup (references/python-setups.md) and point the card
at it with `"python": "NAME.py"`.

## Steps 2-4: check, data, backtest

```bash
python3 "SKILL_DIR/screener.py" card check NAME      # compiles; fires on what % of bars? errors?
python3 "SKILL_DIR/screener.py" data --strategy NAME # first run about a minute (500 stocks)
python3 "SKILL_DIR/screener.py" backtest --strategy NAME [--from 2020-01-01] [--to ...]
```

`card check` before `data` only compiles; after `data` it also counts signals on 40 liquid stocks. A rule
that never fires, or fires on more than 5% of bars, needs another look with the user.

`data` downloads only what the card needs: the universe list (refreshed weekly), daily bars, the Nifty
500 list when a filter ranks against the market, sector indices for the marketfeed funnel, the card's
intraday bars, NSE surveillance and board meetings. (News is fetched by `scan`, not `data`.) Yahoo sometimes returns the newest daily candle
without a close; `data` fills it from NSE's official end-of-day files (published around 6 PM IST).
Check the "Last daily bar" line is the latest session.

The backtest runs the card on every stock and every completed bar, with Indian charges (delivery or
intraday) and slippage, conservative fills (a gap through the stop fills at the open; a bar touching
both stop and target counts as the stop), and reports per trade in R:

- **rules alone** vs **rules + filters**: do the filters help?
- **in-sample** vs **out-of-sample** (the last 30%): did it hold on data it was not tuned on?
- by **market regime** and by **year**, win rate, average win and loss, expectancy, profit factor,
  drawdown, how trades ended;
- a **portfolio** run with the card's capital and limits against the Nifty 500.

Explain the verdict in plain words. **Insufficient** (under 30 trades or 15 out-of-sample),
**negative** or **mixed** = do not trade it. **Positive** = evidence, not proof: forward test it first.
If the user tweaks rules after seeing results, warn: every tweak fits the rules to this data; change for
a reason, and re-test on a later window (`--from`). Always say the limits: survivorship bias (today's
index list used for the past), free Yahoo data, intraday history depth, taxes not included.

## Step 5: the screen and the dashboard

```bash
python3 "SKILL_DIR/screener.py" data --strategy NAME    # after the close (daily/weekly) or during the day (intraday)
python3 "SKILL_DIR/screener.py" scan --strategy NAME
```

Present it **as a funnel**: universe → (regime) → (sectors) → tradable → (strength) → (their filters) →
triggered → validated, with the count and main reasons at each layer. Then each trade plan: PASS /
REVIEW / REJECT checks (stop vs ATR, reward:risk, room to resistance, extension, results dates,
NSE filings and headlines, portfolio limits, gap rule), the order, entry, stop, target, and the sizing arithmetic (quantity, ₹
position, ₹ at risk, which limit capped it). "Live" plans are earlier signals whose order is still
valid and unfilled. Near misses are stocks that passed every filter with all but one entry rule true.

Open the dashboard for the user (`runs/NAME/<bar>/dashboard.html`; in the Claude app the file pane or a
browser opens it). Tabs: **Screener** (funnel, sortable plans, near misses with ✓/✗ per rule, and a
chart for every stock: candles, volume, the card's moving averages, entry / stop / target / resistance
with the risk and reward zones, and every past signal in view with its outcome), **Backtest**,
**Forward test**, **Strategy** (plain words + the shareable card JSON). Light and dark mode, phone-sized.

`explain SYMBOL --strategy NAME` answers "why didn't X show up?": the layer it stopped at, or which
entry rules were true and false.

### News and catalysts (the script collects, Claude reads)

`scan` runs a news layer after validation for every PASS and REVIEW trade plan (`--no-news` skips it):

- **NSE corporate announcements, last 30 days** (the company's own filings, SEBI LODR Reg. 30), tagged
  results, board meeting, dividend/bonus/split, buyback, deal, order win, rating, management,
  regulatory, capital, insider/SAST, risk. Routine filings (lost share certificates, newspaper copies,
  trading window) are left out. If NSE blocks its API, NSE's public RSS of today's announcements is used.
- **Google News India headlines, last 14 days**, minus evergreen SEO pages ("share price today",
  "prediction for Monday", "stocks to watch in 2027"...).
- **Headlines for each sector in play** (the top sectors and the plans' sectors).

Each plan gets a **News** check:
- **REVIEW** for a filing or headline that cuts the other way: regulatory action, default or fraud,
  a pledge, a management exit, a rating or capital or deal filing, a block or bulk deal.
- **REVIEW** when the feeds did not answer.
- **PASS** otherwise.

Good news never upgrades a check or adds a stock: it is a catalyst, not a buy signal. The backtest and
forward test do not use news (old headlines cannot be replayed without bias). An `--asof` replay reads
only news published before its last bar closed, so later news never leaks in. The dashboard shows each
plan's flags, filings (linked to the NSE PDF) and headlines, plus the sector headlines.

Claude then reads what was flagged: open the filing or story behind each REVIEW item, and give one or two
dated lines per plan with sources. Search the web (WebSearch) only for what the feeds could not answer,
or to explain a sector's move. Flag a results date inside the holding period. Save what you learn:

```bash
python3 "SKILL_DIR/screener.py" news --strategy NAME                 # re-check the latest scan (e.g. before the open)
python3 "SKILL_DIR/screener.py" news --strategy NAME --note SYM "6 Nov: Q2 results beat; Nomura positive (CNBC TV18)."
```

Headlines are leads, not facts. Never present a headline as the reason to buy.

### Manual checks before any order

The trader decides. Walk through: is the setup still valid on their broker's chart; bid-ask spread and
depth; is the stop where the idea is wrong (not only where the risk fits); results or ex-dates in the
holding period; the gap rule (opens above entry + 0.5 ATR → skip). Zero trades is a normal result.

## Step 6: forward test

```bash
python3 "SKILL_DIR/screener.py" forward start --strategy NAME      # freezes the card now
python3 "SKILL_DIR/screener.py" forward status --strategy NAME
```

`start` freezes a copy of the card with its hash. Every scan afterwards re-runs the FROZEN rules over all
bars that closed after the start: each signal is filled, stopped, targeted or timed out by the same
simulator as the backtest, so it does not depend on the user scanning every day. The dashboard compares
forward expectancy and win rate with the backtest of the same card version. Editing the card does not
change a running test; `--restart` starts over with the new rules (the clock resets). Under 30 closed
trades: too early to judge.

## Kite (optional, for longer intraday history)

If the user has Zerodha Kite connected (the `kite` MCP), fetch candles with its historical-data tool
and save the response to a JSON file, then:

```bash
python3 "SKILL_DIR/screener.py" import-bars RELIANCE --tf 5m --file /path/to/candles.json
```

Imported bars are merged into the cache and marked as Kite data; Yahoo's later bars append after them.
Never place, modify or cancel orders through Kite from this skill.

## Other commands

```bash
python3 "SKILL_DIR/screener.py" size --strategy NAME --entry 1234 --stop 1180 [--target 1350]
python3 "SKILL_DIR/screener.py" position add SYM --qty 40 --entry 1234 --stop 1180   # for heat and sector limits
python3 "SKILL_DIR/screener.py" position list | position stop SYM --stop 1210 | position close SYM
python3 "SKILL_DIR/screener.py" scan --strategy NAME --asof 2026-07-15                # replay a past date
```

## Sharing a strategy

A card is the whole screener in one file. To share: send `strategies/NAME.json` (and the `.py` file if it
uses one). The receiver puts it in their `strategies/` folder, changes `capital` to their own numbers,
and runs `data`, `backtest`, `scan`. Never share someone's capital or positions without asking.

## How to talk about results

- Say "matched the rules" or "passed the checks". Never "a buy", "a winner" or "will go up".
- Never pick a stock for the user. Present the plans, the checks and the arithmetic; they decide.
- Show the sizing formula and which cap limited it.
- Say the data date and source, and when anything is partial ("14 of 15 charts").
- Quote backtest numbers with their sample size and the out-of-sample result. Never promise returns.
- If the regime gate says bearish: no new long trades; cash is a position.

## Safety

- Education and tooling, not investment advice; point the user to a SEBI-registered adviser for
  personal advice.
- It never places, modifies or cancels an order, and never logs in to a broker.
- Long only. Delivery or intraday cash equity; no F&O orders, leverage or short selling.
- Tax: listed-equity gains under 12 months are short-term capital gains; frequent trading can be
  business income. Not tax advice.
