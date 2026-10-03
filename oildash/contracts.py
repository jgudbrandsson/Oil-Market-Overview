"""NYMEX WTI (CL) contract calendar: which delivery month is M1 on a trade date.

EIA publishes only rolling "contract 1..4" prices, and Yahoo lists contracts by
symbol, so both fetchers need the same mapping between trade date, position on
the curve and delivery month.

CL rule: trading ends 3 business days before the 25th calendar day of the month
before delivery; if the 25th is not a business day, 4 business days before it.
Business days here are weekdays minus the US exchange holidays below. CME's own
holiday calendar can differ on a rare day; that only shifts the roll by a day.
"""

from datetime import date, timedelta

ROOT = "CL"
MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th given weekday (Mon=0) of a month; n=-1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year: int) -> set[date]:
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # a Saturday New Year's Day is not made up
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    return days


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    y, m = divmod(year * 12 + month - 1 + n, 12)
    return y, m + 1


def last_trade_date(year: int, month: int) -> date:
    """Last trading day of the CL contract for delivery in (year, month)."""
    py, pm = add_months(year, month, -1)
    d = date(py, pm, 25)
    remaining = 3 if is_business_day(d) else 4
    while remaining:
        d -= timedelta(days=1)
        if is_business_day(d):
            remaining -= 1
    return d


def front_month(trade_date: date) -> tuple[int, int]:
    """Delivery month of contract 1 on a trade date: the earliest contract still
    trading. On its last trading day the expiring contract is still contract 1."""
    y, m = add_months(trade_date.year, trade_date.month, 1)
    while last_trade_date(y, m) < trade_date:
        y, m = add_months(y, m, 1)
    return y, m


def contract_month(trade_date: date, position: int) -> str:
    """'YYYY-MM' delivery month of contract `position` (1 = front) on a trade date."""
    y, m = add_months(*front_month(trade_date), position - 1)
    return f"{y:04d}-{m:02d}"


def yahoo_symbol(year: int, month: int) -> str:
    """Yahoo ticker for a CL contract, e.g. (2026, 12) -> 'CLZ26.NYM'."""
    return f"{ROOT}{MONTH_CODES[month - 1]}{year % 100:02d}.NYM"
