from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from lib.db.enums import IntervalType
from lib.models import BaseModel
from lib.models.balance import Balance
from lib.models.gain import Gain


class IntervalBase(BaseModel):
    start: date
    length: Optional[IntervalType]
    gain: Gain
    offset: Decimal


class Interval(BaseModel):
    start: date
    length: Optional[IntervalType]
    gain: Gain
    start_balance: Balance
    end_balance: Balance
    offset: Decimal

    class Config:
        orm_mode = True

    @classmethod
    def create(
        cls,
        prev: Balance,
        current: Balance,
        offset: Decimal,
        length: Optional[IntervalType] = None,
    ) -> Interval:
        return cls(
            start=current.time.date(),
            gain=current.gain_since(prev, offset),
            start_balance=prev,
            end_balance=current,
            offset=offset,
            length=length,
        )

    def __add__(self, other):
        return self.create(
            self.start_balance + other.start_balance,
            self.end_balance + other.end_balance,
            offset=self.offset + other.offset,
        )


class FullInterval(Interval):
    start_balance: Balance
    end_balance: Balance
