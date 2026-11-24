# marketfeed screener: build your own stock screener with Claude

Bring your own rules. This Claude Code skill turns them into a working screener for NSE stocks:

1. **Claude interviews you**: universe (Nifty 50 / Next 50 / 100 / 200 / 500, Midcap 150, Smallcap 250,
   F&O stocks, sector indices, or your own list), chart (5m, 15m, 1h, 2h, daily, weekly), capital and
   risk, the marketfeed funnel or your own filters, and your exact entry, stop, target and exit rules.
2. It writes one **strategy card**, a small readable file such as
   `"when": ["rsi(14) crosses above 60", "weekly(close > sma(30))"]`, that you can share.
3. It **pulls the data** (free NSE lists, filings and Yahoo bars; Zerodha Kite history optional), **screens**
   the universe, and **sizes** each trade from your rules.
4. It **checks the news** for every trade plan: NSE filings (30 days) and India news headlines
   (14 days). Anything that cuts the other way (regulatory action, pledges, management exits, block
   deals) turns the plan to REVIEW, so you read it first.
5. It **backtests** your rules after Indian charges (in-sample vs out-of-sample, by market regime, by
   year, as a portfolio against the Nifty 500).
6. It **forward tests** them: it freezes the card and paper-trades every new signal.
7. You get an **interactive dashboard** with a chart for every stock. Each chart shows entry, stop,
   target, resistance, your moving averages and every past signal with its outcome.

Education and tooling, not investment advice. It never places an order.

## Install (one line)

Needs [Claude Code](https://claude.com/claude-code), git, and Python 3.8+ (already on macOS and most
Linux). Nothing else to install.

macOS / Linux:

```bash
git clone https://github.com/marketfeedtelugu/marketfeedtelugu-screener.git ~/.claude/skills/marketfeed-screener
```

Windows (PowerShell):

```powershell
git clone https://github.com/marketfeedtelugu/marketfeedtelugu-screener.git $HOME\.claude\skills\marketfeed-screener
```

Then start Claude Code and type `/marketfeed-screener`, or say "build my screener".

Update to the latest version:

```bash
git -C ~/.claude/skills/marketfeed-screener pull
```

Your strategies and data live in `~/marketfeed-screener/`, outside the skill folder, so updating never
touches them.

## Use

Claude asks the questions and runs everything. The commands underneath:

```bash
python3 screener.py card new my_setup --from ema_pullback   # start from an example card
python3 screener.py card show my_setup                      # your rules in plain words
python3 screener.py data --strategy my_setup                # download what the card needs
python3 screener.py backtest --strategy my_setup            # does it have an edge after costs?
python3 screener.py scan --strategy my_setup                # today's screen -> dashboard.html
python3 screener.py forward start --strategy my_setup       # paper-trade it from now on
```

Your files live in `~/marketfeed-screener/` (`strategies/`, `runs/`, `backtests/`, `forward/`).

Example cards: `marketfeed_momentum` (the marketfeed funnel preset), `ema_pullback`,
`breakout_52w_weekly`, `hourly_trend_pullback`, `orb_15m_intraday`. They are examples, not
recommendations: several lose money after costs, and the backtest says so.

## Honest limits

- Free Yahoo data is unofficial and can lag. 5m and 15m history is about 60 days, and 1h about 2-3
  years (import Kite history for more).
- Backtests use today's index list for the past (survivorship bias). Inside a bar, the stop is
  assumed to come before the target. Taxes are not included.
- A positive backtest is evidence, not proof: forward test it first.
