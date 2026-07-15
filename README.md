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

Run the options backtester:

```bash
python backtester.py --years 1
python backtester.py --years 3
python backtester.py --years 5
```

The backtester writes closed trades to `logs/options_backtest_trades.csv`
and the closed-trade equity curve to `logs/options_backtest_equity_curve.csv`.
The summary includes win rate, total P/L, profit factor, expectancy, maximum
drawdown, and symbol-level results.
