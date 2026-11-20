from datetime import date, timedelta

import pytest

from fundkit.models import Holding
from fundkit.store import Store


def d(offset_days: int) -> date:
    """Test calendar: d(0) is 'today' (2026-11-29)."""
    return date(2026, 11, 29) + timedelta(days=offset_days)


@pytest.fixture
def store_with_cash_3000(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save_holdings([Holding(ticker="CASH", sleeve="cash", shares=3000.0, cost_basis=1.0, lot_date=date(2026, 11, 22))])
    return s
