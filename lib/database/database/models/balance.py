from __future__ import annotations

import itertools
from datetime import datetime
from decimal import Decimal
from typing import Optional

from database.models import OrmBaseModel, OutputID, BaseModel
from database.models.gain import Gain
from core.utils import calc_percentage_diff, safe_cmp_default, round_ccy


class AmountBase(OrmBaseModel):
    currency: Optional[str]
    realized: Decimal
    unrealized: Decimal
    client_id: Optional[OutputID]
    rate: Optional[Decimal]

    def _assert_equal(self, other: 'AmountBase'):
        assert self.currency == other.currency

    def gain_since(self, other: 'AmountBase', offset: Decimal) -> Gain:
        self._assert_equal(other)
        gain = (self.total - other.realized) - (offset or 0)
        return Gain.construct(
            absolute=gain,
            relative=calc_percentage_diff(other.realized, gain)
        )

    @property
    def total_transfers_corrected(self):
        return self.unrealized

    @property
    def total(self):
        return self.realized + self.unrealized

    def __repr__(self):
        return f'{self.total}{self.currency}'

    def __add__(self, other: 'AmountBase'):
        self._assert_equal(other)
        return Amount(
            realized=self.realized + other.realized,
            unrealized=self.unrealized + other.unrealized,
            currency=self.currency,
            rate=(
                (self.rate * self.realized + other.rate * other.realized) / (self.realized + other.realized)
                if self.rate and other.rate else self.rate or other.rate
            )
        )


class Amount(AmountBase):
    time: datetime

    def __add__(self, other: 'Amount'):
        self._assert_equal(other)
        return Amount(
            realized=self.realized + other.realized,
            unrealized=self.unrealized + other.unrealized,
            currency=self.currency,
            time=safe_cmp_default(max, self.time, other.time),
            rate=(
                (self.rate * self.realized + other.rate * other.realized) / (self.realized + other.realized)
                if (self.rate and other.rate and (self.realized or other.realized)) else self.rate or other.rate
            )
        )


class BalanceGain(BaseModel):
    other: Optional[Balance]
    total: Optional[Gain]
    extra: Optional[list[Gain]]


class Balance(Amount):
    extra_currencies: Optional[list[AmountBase]]
    gain: Optional[BalanceGain]

    def __add__(self, other: Balance):
        self._assert_equal(other)
        return self.add(self, other)

    @classmethod
    def add(cls, *balances: Balance):

        currencies = set(
            (amount.currency for balance in balances if balance.extra_currencies for amount in balance.extra_currencies)
        )
        extra_currencies = []
        for currency in currencies:
            amount = None
            for balance in balances:
                if amount:
                    amount = amount + balance.get_currency(currency)
                else:
                    amount = balance.get_currency(currency)
            extra_currencies.append(amount)

        return Balance(
            realized=sum(balance.realized for balance in balances),
            unrealized=sum(balance.unrealized for balance in balances),
            time=max(balance.time for balance in balances),
            extra_currencies=extra_currencies,
            currency=balances[0].currency
        )

    def set_gain(self, other: Balance, offset: Decimal):
        self.gain = BalanceGain(
            other=other,
            total=self.gain_since(other, offset),
        )

    def to_string(self, display_extras=False):
        ccy = self.currency
        string = f'{round_ccy(self.unrealized, ccy)}{ccy}'

        if self.extra_currencies and display_extras:
            currencies = " / ".join(
                f'{amount.unrealized}{amount.currency}'
                for amount in self.extra_currencies
            )
            string += f'({currencies})'

        return string

    def get_currency(self, currency: Optional[str]) -> Amount:
        if currency:
            realized = unrealized = rate = Decimal(0)
            if self.extra_currencies:
                for amount in self.extra_currencies:
                    if amount.currency == currency:
                        realized = amount.realized
                        unrealized = amount.unrealized
                        rate = amount.rate
                        break
            return Amount(
                realized=realized,
                unrealized=unrealized,
                currency=currency,
                time=self.time,
                rate=rate
            )
        else:
            return self


BalanceGain.update_forward_refs()
