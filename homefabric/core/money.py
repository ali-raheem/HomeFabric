"""Money is stored as integer minor units plus an ISO 4217 currency code."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Currencies whose minor unit is not 2 decimal places.
_EXPONENTS = {"JPY": 0, "KRW": 0, "ISK": 0, "CLP": 0, "VND": 0, "BHD": 3, "KWD": 3, "OMR": 3,
              "JOD": 3, "TND": 3}
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥"}

COMMON_CURRENCIES = ["GBP", "EUR", "USD", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK",
                     "PLN", "JPY", "INR", "ZAR"]


def exponent(currency: str) -> int:
    return _EXPONENTS.get((currency or "").upper(), 2)


def to_minor(amount, currency: str) -> int:
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount.") from exc
    quantum = Decimal(1).scaleb(-exponent(currency))
    return int((value.quantize(quantum, rounding=ROUND_HALF_UP)).scaleb(exponent(currency)))


def from_minor(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-exponent(currency))


def format_money(minor, currency: str) -> str:
    if minor is None or not currency:
        return ""
    value = from_minor(minor, currency)
    text = f"{value:,.{exponent(currency)}f}"
    symbol = _SYMBOLS.get(currency.upper())
    return f"{symbol}{text}" if symbol else f"{text} {currency.upper()}"


def totals_by_currency(rows) -> dict[str, int]:
    """Sum (minor, currency) pairs per currency; unlike currencies are never combined."""
    totals: dict[str, int] = {}
    for minor, currency in rows:
        if minor is None or not currency:
            continue
        totals[currency] = totals.get(currency, 0) + minor
    return totals
