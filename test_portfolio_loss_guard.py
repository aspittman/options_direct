import csv
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, MagicMock
import pandas as pd
import portfolio_loss_guard as portfolio


class PortfolioLossGuardTests(unittest.TestCase):
    def test_portfolio_csv_block_crosses_bot_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOSS_GUARD_SCOPE':'portfolio','LOSS_LEDGER_PATHS':'{}'}):
            base=Path(tmp);source=base/'options_direct/logs/trade_analytics.csv';source.parent.mkdir(parents=True)
            fields=['event','strategy','underlying','option_symbol','qty','price','order_side','timestamp']
            with source.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
                for side,price,stamp in [('buy',1,'2026-08-01'),('sell',.8,'2026-08-02')]:
                    writer.writerow(dict(event='ORDER_FILL',strategy='oasis',underlying='ABC',option_symbol='ABC261218C00100000',qty=1,price=price,order_side=side,timestamp=stamp+'T10:00:00'))
            with patch.object(portfolio,'__file__',str(base/'options_covered/portfolio_loss_guard.py')):
                self.assertTrue(portfolio.portfolio_blocked('ABC',date(2026,9,1)))
                self.assertFalse(portfolio.portfolio_blocked('ABC',date(2026,9,2)))
                with patch.dict(os.environ,{'LOSS_GUARD_SCOPE':'bot'}):
                    self.assertFalse(portfolio.portfolio_blocked('ABC',date(2026,9,1)))

    def test_sqlite_short_credit_loss_and_stock_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'covered.sqlite3';db=sqlite3.connect(path)
            db.executescript('CREATE TABLE fills(id INTEGER PRIMARY KEY,underlying TEXT,symbol TEXT,qty REAL,notional REAL,intent TEXT,timestamp TEXT); CREATE TABLE stock_dispositions(underlying TEXT,price REAL,cost_per_share REAL,timestamp TEXT);')
            db.execute("INSERT INTO fills VALUES (1,'ABC','contract',1,1,'sell_to_open','2026-08-01')")
            db.execute("INSERT INTO fills VALUES (2,'ABC','contract',1,1.2,'buy_to_close','2026-08-02')")
            db.execute("INSERT INTO stock_dispositions VALUES ('XYZ',90,100,'2026-08-03')")
            db.commit();db.close()
            self.assertEqual(portfolio.read_loss_dates('options_covered',path),{'ABC':date(2026,8,2),'XYZ':date(2026,8,3)})
            path=Path(tmp)/'secured.sqlite3';db=sqlite3.connect(path)
            db.execute('CREATE TABLE pnl(symbol TEXT,realized REAL,timestamp TEXT)')
            db.execute("INSERT INTO pnl VALUES ('ABC261218P00100000',-20,'2026-08-04')")
            db.commit();db.close()
            self.assertEqual(portfolio.read_loss_dates('options_secured',path),{'ABC':date(2026,8,4)})

    def test_unreadable_configured_history_blocks_entry(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOSS_GUARD_SCOPE':'portfolio','LOSS_LEDGER_PATHS':'{}'}):
            path=Path(tmp)/'options_secured/logs/options_secured.sqlite3';path.parent.mkdir(parents=True);path.write_text('not a database')
            with patch.object(portfolio,'__file__',str(Path(tmp)/'options_direct/portfolio_loss_guard.py')):
                self.assertTrue(portfolio.portfolio_blocked('ABC'))
