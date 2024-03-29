from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, Iterable, Sequence

from lib.models import OrmBaseModel, OutputID, BaseModel
from lib.models.gain import Gain
from lib.utils import calc_percentage_diff, round_ccy


class AmountBase(OrmBaseModel):
    currency: Optional[str]
    realized: Decimal
    unrealized: Decimal
    client_id: Optional[OutputID]
    rate: Optional[Decimal]
    gain: Optional[Gain]

    def _assert_equal(self, other: "AmountBase"):
        assert self.currency == other.currency

    def set_gain(self, other: Balance, offset: Decimal):
        self.gain = self.gain_since(other, offset)

    def gain_since(self, other: "AmountBase", offset: Decimal) -> Gain:
        self._assert_equal(other)
        gain = (self.realized - other.realized) - (offset or 0)
        return Gain.construct(
            absolute=gain,
            relative=calc_percentage_diff(other.realized, gain),
        )

    @property
    def total_transfers_corrected(self):
        return self.unrealized

    @property
    def total(self):
        return self.realized + self.unrealized

    def __repr__(self):
        return f"{self.total}{self.currency}"

    @classmethod
    def sum(cls, amounts: Iterable[AmountBase]):
        amount = None
        for current in amounts:
            if amount:
                if current.realized:
                    amount = cls.__add__(amount, current)
            else:
                amount = current
        return amount

    def __add__(self, other: AmountBase):
        self._assert_equal(other)
        return AmountBase(
            realized=self.realized + other.realized,
            unrealized=self.unrealized + other.unrealized,
            currency=self.currency,
            rate=(
                (self.rate * self.realized + other.rate * other.realized)
                / (self.realized + other.realized)
                if self.rate and other.rate
                else self.rate or other.rate
            ),
        )


class BalanceGain(BaseModel):
    other: Optional[Balance]
    total: Optional[Gain]
    extra: Optional[list[Gain]]


class Balance(AmountBase):
    time: datetime
    extra_currencies: Optional[list[AmountBase]]
    # gain: Optional[BalanceGain]

    def __add__(self, other: Balance):
        self._assert_equal(other)
        return self.sum((self, other))

    @classmethod
    def sum(cls, balances: Sequence[Balance]):
        currencies = set(
            (
                amount.currency
                for balance in balances
                if balance.extra_currencies
                for amount in balance.extra_currencies
            )
        )

        extra_currencies = [
            AmountBase.sum([balance.get_currency(currency) for balance in balances])
            for currency in currencies
        ]

        return Balance(
            realized=sum(balance.realized for balance in balances),
            unrealized=sum(balance.unrealized for balance in balances),
            time=max(balance.time for balance in balances),
            extra_currencies=extra_currencies,
            currency=balances[0].currency,
        )

    # def set_gain(self, other: Balance, offset: Decimal):
    #    self.gain = BalanceGain(
    #        other=other,
    #        total=self.gain_since(other, offset),
    #    )

    def to_string(self, display_extras=False):
        ccy = self.currency
        string = f"{round_ccy(self.total, ccy)}{ccy}"

        if self.extra_currencies and display_extras:
            currencies = " / ".join(
                f"{amount.total}{amount.currency}" for amount in self.extra_currencies
            )
            string += f"({currencies})"

        return string

    def get_currency(self, currency: Optional[str]) -> Balance:
        if currency and currency != self.currency:
            realized = unrealized = rate = Decimal(0)
            if self.extra_currencies:
                for amount in self.extra_currencies:
                    if amount.currency == currency:
                        realized = amount.realized
                        unrealized = amount.unrealized
                        rate = amount.rate
                        break
            return Balance(
                realized=realized,
                unrealized=unrealized,
                currency=currency,
                time=self.time,
                rate=rate,
                extra_currencies=[],
            )
        else:
            return self


BalanceGain.update_forward_refs()
