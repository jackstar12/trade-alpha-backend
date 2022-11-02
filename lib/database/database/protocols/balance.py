import typing
from datetime import datetime
from decimal import Decimal


class Balance(typing.Protocol):
    time: datetime
    realized: Decimal
    unrealized: Decimal
    extra_currencies: typing.Optional[list]
    currency: str

    @property
    def total_transfers_corrected(self):
        return self.unrealized

    def __add__(self, other: 'Balance'):
        return Balance.construct(
            realized=self.realized + other.realized,
            unrealized=self.unrealized + other.unrealized,
            time=min(self.time, other.time) if self.time else None,
            extra_currencies=self.extra_currencies + other.extra_currencies
        )
