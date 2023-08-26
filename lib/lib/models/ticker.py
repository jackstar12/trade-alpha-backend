from decimal import Decimal
from typing import NamedTuple

from lib.db.models.client import ExchangeInfo


class Ticker(NamedTuple):
    symbol: str
    src: ExchangeInfo
    price: Decimal
