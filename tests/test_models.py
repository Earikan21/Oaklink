from datetime import date

import pytest
from pydantic import ValidationError

from fundkit.models import Holding, Targets


def test_targets_default_sum_to_one():
    assert Targets().stocks + Targets().bonds + Targets().cash == pytest.approx(1.0)


def test_targets_reject_bad_sum():
    with pytest.raises(ValidationError):
        Targets(stocks=0.6, bonds=0.3, cash=0.2)


def test_cash_holding_uses_shares_as_dollars():
    h = Holding(ticker="CASH", sleeve="cash", shares=600.0, cost_basis=1.0, lot_date=date(2026, 11, 22))
    assert h.shares == 600.0
