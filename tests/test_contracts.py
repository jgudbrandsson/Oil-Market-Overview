from datetime import date

import pytest

from oildash import contracts


@pytest.mark.parametrize(
    "year, month, expected",
    [
        (2025, 5, date(2025, 4, 22)),  # 25th is a Friday: 3 business days before
        (2025, 11, date(2025, 10, 21)),  # 25th is a Saturday: 4 business days before
        (2026, 1, date(2025, 12, 19)),  # 25th is Christmas
        (2026, 2, date(2026, 1, 20)),  # 25th is a Sunday
        (2026, 11, date(2026, 10, 20)),
    ],
)
def test_last_trade_date(year, month, expected):
    assert contracts.last_trade_date(year, month) == expected


def test_expiring_contract_is_front_month_on_its_last_day():
    assert contracts.contract_month(date(2026, 10, 20), 1) == "2026-11"
    assert contracts.contract_month(date(2026, 10, 21), 1) == "2026-12"


def test_contract_month_rolls_over_the_year():
    assert contracts.contract_month(date(2026, 10, 21), 2) == "2027-01"
    assert contracts.contract_month(date(2026, 10, 21), 12) == "2027-11"


def test_holidays():
    assert date(2026, 4, 3) in contracts.holidays(2026)  # Good Friday
    assert date(2026, 7, 3) in contracts.holidays(2026)  # July 4th on a Saturday
    assert date(2021, 12, 31) not in contracts.holidays(2022)  # Saturday New Year
    assert date(2021, 6, 18) not in contracts.holidays(2021)  # before Juneteenth


def test_yahoo_symbol():
    assert contracts.yahoo_symbol(2026, 12) == "CLZ26.NYM"
    assert contracts.yahoo_symbol(2027, 1) == "CLF27.NYM"
