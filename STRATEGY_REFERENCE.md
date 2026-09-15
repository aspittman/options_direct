> For installation and safe first-run instructions, use [README.md](README.md).
> This document preserves the detailed strategy, configuration and research reference.

## Trailing stops

Option-premium trailing stops are enabled for **oasis only** with
`OPTION_TRAILING_STOP_PERCENT=0.20`:

- Bought calls/puts (direct and inverted): sell when the observed option premium
  falls 20% below its highest observed premium for the current holding.
- Sold covered calls/cash-secured puts: buy back when the ask rebounds 20% above
  its lowest observed buyback price for the current holding.

Regular retains its previous controls: direct/inverted use their existing 30%
fixed option stops and 3% underlying trails; covered/secured keep their 2x-credit
fixed stops without an option-premium trail. Oasis alone uses the 20% fixed stop
and 20% premium trail. The trail starts from its entry premium.
Long-option highs are rebuilt from confirmed fills and durable premium snapshots;
short-option lows are persisted in a small ledger table keyed to the entry order.
The trail never loosens as prices reverse, survives restarts, and resets for a new
trade. Existing fixed stops, regular underlying-price trails, technical exits,
Oasis closing times, collateral controls, and the shared loss block remain active.
Stops are monitored limit-order exits and do not guarantee execution at the trigger.
An existing short option starts from its entry credit/current ask because earlier
unrecorded intraday lows cannot be reconstructed.

## Setup

```bash
git clone https://github.com/aspittman/options_direct.git
cd options_direct

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your Alpaca paper trading credentials:

```bash
APCA_API_KEY_ID=your_alpaca_api_key
APCA_API_SECRET_KEY=your_alpaca_secret_key
ALPACA_PAPER=true
```

The bot also accepts `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` or the older
`API_KEY`/`SECRET_KEY` names, but Alpaca's `APCA_*` names are preferred.

Run the bot:

```bash
python main.py
```

Optional option-risk settings (shown with defaults):

```bash
ENABLE_NEW_ENTRIES=false
EXIT_DTE=30
OPTION_STOP_LOSS_PERCENT=0.30
OPTION_TRAILING_STOP_PERCENT=0.20
UNDERLYING_TRAILING_STOP_PERCENT=0.03
REENTRY_COOLDOWN_DAYS=5
LIMIT_ORDER_TIMEOUT_MINUTES=15
EXIT_LIMIT_TIMEOUT_MINUTES=2
VIRTUAL_STARTING_CAPITAL=25000
MAX_OPTION_PREMIUM_PER_TRADE=500
MAX_CONTRACTS_PER_TRADE=1
MAX_TOTAL_OPTION_PREMIUM=1000
MAX_POSITIONS=2
MAX_POSITIONS_PER_CORRELATION_GROUP=1
BACKTEST_STARTING_CASH=25000
ALLOW_DUPLICATE_CONTRACTS=false
ALLOW_MULTIPLE_CONTRACTS_PER_UNDERLYING=false
```

When `ENABLE_NEW_ENTRIES=false`, the bot continues reconciling fills and managing
all existing exits, but it submits no new buy orders. Set it to `true` only when
you intentionally resume paper entries. `MAX_POSITIONS=2` is enforced globally
across both named strategies, and the total-premium limit is also shared.

Percent settings are decimal fractions. Position limits and premium totals apply
only to option contracts submitted by OptionsDirect; stock positions and other
bots' positions are excluded. The analytics CSV records realized and unrealized
P/L in separate columns and the cycle log reports results both by contract and
by underlying.

This repository identifies itself as `long_call` in logs and Alpaca client order
IDs. It only adopts and manages positive-quantity call positions recorded in its
own ledger. Account equity and buying power never increase its limits: research
returns use an independent $25,000 virtual allocation, and long-call capital
employed is the premium paid.

Long-call-only behavior is enforced twice: contract discovery refuses any
non-call request, and the final order gate parses the OCC option symbol and
rejects anything that is not a call before submitting it to Alpaca.

Every new entry and exit order uses a `long_call_<underlying>_<timestamp>` client
order ID (exit IDs also contain `_x_`). Before canceling a stale order, the bot
retrieves it from Alpaca and verifies that its client order ID starts with
`long_call_`. Foreign and untagged orders are logged and left untouched.

The live paper bot runs two named variants in the same Alpaca paper account.
The active strategies are `regular` and `oasis`. Both buy calls with 60–90 DTE;
contract expiration is separate from the intended holding period.

- `regular` retains the daily trend rules: price above the 50-day SMA above the
  200-day SMA, rising 50-day SMA, and positive MACD (12/26/9) confirmation. It
  evaluates entries once per completed daily candle and holds at most 20 weekdays.
  Exits retain the 3% underlying trailing stop, 8% underlying target, 30% option
  stop, daily technical exits, and 30-DTE expiration management.
- `oasis` replaces new entries for the old `max_100` daily swing strategy. It uses
  completed regular-session **5-minute** IEX candles, with an EMA cloud formed by
  the **9- and 21-period EMAs**. Entry requires price above EMA9 above EMA21, both
  EMAs rising, a higher close, RSI(14) strictly between 50 and 70, and a positive,
  increasing MACD(12/26/9) histogram. The setup must newly turn bullish. Missing,
  incomplete, previous-session, or stale candles cannot trigger entries.
  Technical exits occur below EMA21, on a bearish EMA cloud, or when MACD
  histogram is nonpositive or RSI drops below 50. The option-premium stop is
  **20% below the filled entry price**. Oasis does not use the regular strategy's
  underlying stop/target or five-day cooldown. It retains 30-DTE management.
  No new entries are allowed in the last 30 minutes of the stock session; pending
  buys are canceled. Positions are submitted for closing in the last 15 minutes,
  using Alpaca's next-close timestamp so shortened sessions are respected. Any
  overnight remainder is submitted for closing on the next market-open cycle.

Both retain the bullish daily SPY regime filter and existing contract-quality,
earnings, correlation-group, and capital checks. Entry premium is capped at $500;
the combined limit is two positions and $1,000 of entry premium.

**Shared loss block:** confirmed losing sales in `logs/trade_analytics.csv` block
both strategies from buying any contract on the same underlying through **30
calendar days after the loss date**, with reentry permitted on day 31. The guard
rebuilds FIFO entry lots within each strategy/contract, blocks on any losing matched
slice, includes historical strategies, survives restarts, and cancels pending
buys on blocked underlyings. Winning/breakeven exits do not start or reset this
block. Regular also keeps its existing five-weekday reentry cooldown after exits.
This is a conservative entry restriction, not tax accounting: it cannot inspect
purchases in other accounts, resolve every substantially-identical instrument, or
undo replacement purchases before a loss. The IRS window also includes 30 days
before the loss: https://www.irs.gov/publications/p550.

The runtime checks risk and orders every 60 seconds, plus processing time.
Intraday bars are fetched in batches and refreshed every five minutes. Stops and
end-of-session exits are monitored software rules using marketable limit orders;
execution and a maximum loss of exactly 20% are not guaranteed. Delayed indicative
option quotes limit paper results' usefulness for evaluating intraday execution.
Entries use midpoint day-limit orders, canceled after 15 minutes if unfilled.
Exit limits are canceled/repriced after two minutes; cancellation requests remain
tracked until the broker confirms a terminal status, so late fills are reconciled.

Historical `max_100` ledger entries retain their name and performance. Any remaining
legacy position is managed under its original daily-swing exits; new trades use
`oasis`. No historical swing results are relabeled as Oasis results.

Contract quality is decided before price is tested. The bot first applies DTE,
delta, liquidity, and spread rules, ranks the surviving contracts, and then checks
the preferred contract's total premium. If it costs more than $500, the opportunity
is rejected; the bot does not substitute a cheaper far-OTM contract. Signal-qualified
rejections are written to `logs/rejected_trades.csv` with standardized reasons and
available quote, contract, capital, and regime context.

The default `expanded` universe contains the original 40 symbols plus 30 actively
traded, generally lower-notional stocks and ETFs. Set `UNIVERSE_PROFILE=original`
to restore the original list. Membership is only an affordability-oriented first
pass: every candidate still has to pass the unchanged bullish signal, 60–90 DTE,
delta, liquidity, spread, and actual quoted-premium checks.

Both variants submit separately tagged paper orders. Alpaca combines quantities
when both variants own the same contract, while `logs/trade_analytics.csv` keeps
the confirmed fill price and virtual quantity for each variant. Runtime summaries
include `by_strategy` realized and unrealized P/L based on those paper fills.
Older premium variable names remain code-level compatibility aliases; the two live
variants use `MAX_OPTION_PREMIUM_PER_TRADE`.

Run the options backtester:

```bash
python backtester.py --years 1
python backtester.py --years 3
python backtester.py --years 5
python backtester.py --years 5 --max-option-premium 250
python backtester.py --years 5 --max-option-premium 500
python backtester.py --years 5 --max-option-premium 750
python backtester.py --years 5 --max-option-premium 1000
python backtester.py --years 5 --max-option-premium 500 --compare-universes
python backtester.py --years 5 --compare-signals
python backtester.py --years 2 --alpaca-options swing --max-candidates 100
```

`--compare-signals` compares the existing MA/MACD rules with an experimental
daily pullback swing setup using $100 of underlying exposure per trade. This
isolates entry/exit quality from synthetic option pricing; it is not an option
return simulation and does not authorize changing the live strategy by itself.

`--alpaca-options` uses actual Alpaca daily option bars and expired contract
metadata instead of theoretical option prices. Alpaca option history begins in
February 2024. Candidates without a real entry/exit bar or a qualifying contract
under the premium ceiling are skipped; the command never fabricates a fill.

The historical backtester does **not** simulate Oasis or the new shared loss block.
It remains a comparison of the original daily research models.
Each standard run prints two summaries: the daily trend control and the daily
swing variant, both subject to their configured premium limits. The trend results
are written to `logs/options_backtest_trades.csv` and
`logs/options_backtest_equity_curve.csv`; the daily-swing results are written to
`logs/options_backtest_trades_100_max.csv` and
`logs/options_backtest_equity_curve_100_max.csv`. Each summary includes win rate,
total P/L, profit factor, expectancy, maximum drawdown, and symbol-level results.
It also reports qualified signals, executed trades, capital-only rejection counts,
virtual-capital return, return on premium employed, average and maximum capital
employed, average premium/DTE/hold time, and winner/loss statistics. Synthetic
backtests cannot measure historical spread or liquidity; use `--alpaca-options`
for actual option-bar validation from February 2024 onward.
Historical backtests remain separate from live paper analytics: they provide many
years of fast, estimated testing, while the live analytics file measures the
actual fills returned by Alpaca paper trading from this point forward.
Both historical variants now enforce starting cash, the configured maximum of two
concurrent positions, the shared total-premium ceiling, and their per-trade premium
limits. Option prices remain estimates rather than historical option-chain quotes.

View both live paper strategies without placing orders or running a historical
simulation:

```bash
python3 backtester.py --paper-results
```

This reports confirmed completed trades, win rate, realized and unrealized P/L,
open virtual positions, and pending orders separately for `regular` and
`oasis`, plus historical strategy names present in the ledger.


The loss block now also reads the default ledgers of sibling `options_inverted`,
`options_covered`, and `options_secured` bots. A recorded loss in any of the four
blocks new entries on that underlying across all four, without modifying sibling
ledgers. Set `LOSS_GUARD_SCOPE=bot` to restrict checks to each bot's own ledger.
Use `LOSS_LEDGER_PATHS` as a JSON object mapping bot directory names to absolute
ledger paths if you use nondefault ledger locations. Missing default ledgers are
ignored; an existing unreadable ledger blocks entries until it can be read.
These checks do not cover unrecorded trades or replace tax accounting.
