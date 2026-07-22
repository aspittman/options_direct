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
EXIT_DTE=7
OPTION_STOP_LOSS_PERCENT=0.50
OPTION_TRAILING_STOP_PERCENT=0.25
MAX_PREMIUM_PER_TRADE=100
REGULAR_MAX_PREMIUM_PER_TRADE=0
MAX_100_PREMIUM_PER_TRADE=100
MAX_TOTAL_OPTION_PREMIUM=500
MAX_POSITIONS=2
ALLOW_DUPLICATE_CONTRACTS=false
ALLOW_MULTIPLE_CONTRACTS_PER_UNDERLYING=false
```

Percent settings are decimal fractions. Position limits and premium totals apply
only to option contracts submitted by OptionsDirect; stock positions and other
bots' positions are excluded. The analytics CSV records realized and unrealized
P/L in separate columns and the cycle log reports results both by contract and
by underlying.

The live paper bot runs two named variants in the same Alpaca paper account:

- `regular` uses the normal strategy. Its per-trade premium limit defaults to
  `0`, meaning no per-trade cap (the account-wide total-premium guard still
  applies).
- `max_100` uses the same signal, contract selection, and exit rules, but rejects
  entries whose estimated one-contract premium exceeds $100.

Both variants submit separately tagged paper orders. Alpaca combines quantities
when both variants own the same contract, while `logs/trade_analytics.csv` keeps
the confirmed fill price and virtual quantity for each variant. Runtime summaries
include `by_strategy` realized and unrealized P/L based on those paper fills.
Changing `MAX_PREMIUM_PER_TRADE` is retained for compatibility with older setups;
the two live variants use the two strategy-specific settings above.

Run the options backtester:

```bash
python backtester.py --years 1
python backtester.py --years 3
python backtester.py --years 5
```

Each run prints two summaries: the regular one-contract simulation and a second
simulation that only enters contracts costing $100 or less. The regular results
are written to `logs/options_backtest_trades.csv` and
`logs/options_backtest_equity_curve.csv`; the $100-max results are written to
`logs/options_backtest_trades_100_max.csv` and
`logs/options_backtest_equity_curve_100_max.csv`. Each summary includes win rate,
total P/L, profit factor, expectancy, maximum drawdown, and symbol-level results.
Historical backtests remain separate from live paper analytics: they provide many
years of fast, estimated testing, while the live analytics file measures the
actual fills returned by Alpaca paper trading from this point forward.

View both live paper strategies without placing orders or running a historical
simulation:

```bash
python3 backtester.py --paper-results
```

This reports confirmed completed trades, win rate, realized and unrealized P/L,
open virtual positions, and pending orders separately for `regular` and
`max_100`.
