from __future__ import annotations

import itertools
import typing
from datetime import datetime
from decimal import Decimal
from typing import List, Generator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.transfer import Transfer
from database.dbasync import db_all
from database.dbmodels import BalanceDB, TransferDB
from database.enums import IntervalType
from database.errors import UserInputError
from database.models.balance import Balance
from database.models.interval import Interval

if TYPE_CHECKING:
    from database.dbmodels.client import Client


def is_same(a: datetime, b: datetime, length: IntervalType):
    result = a.year == b.year and a.month == b.month
    if length == IntervalType.MONTH:
        return result
    result = result and a.isocalendar().week == b.isocalendar().week
    if length == IntervalType.WEEK:
        return result
    return result and a.day == b.day


def create_intervals(history: list[BalanceDB],
                     transfers: list[TransferDB],
                     ccy: str = None):
    results: dict[IntervalType, list[Interval]] = {}
    recent: dict[IntervalType, BalanceDB] = {}
    offsets: dict[IntervalType, Decimal] = {}

    # TODO: Optimize transfers
    offset_gen = transfer_gen(transfers, ccy=ccy, reset=True)

    # Initialise generator
    offset_gen.send(None)

    for prev, current in itertools.pairwise(history):
        try:
            cur_offset = offset_gen.send(current.time)
            for length in IntervalType:
                offsets[length] = offsets.get(length, 0) + cur_offset
        except StopIteration:
            pass
        for length in IntervalType:
            if length in recent:
                if not is_same(current.time, prev.time, length):
                    results.setdefault(length, []).append(
                        Interval.create(
                            prev=recent[length].get_currency(ccy),
                            current=prev.get_currency(ccy),
                            offset=offsets.get(length, 0),
                            length=length
                        )
                    )
                    offsets[length] = Decimal(0)
                    recent[length] = prev
            else:
                recent[length] = prev

    for length, prev in recent.items():
        results.setdefault(length, []).append(
            Interval.create(
                prev=prev.get_currency(ccy),
                current=current.get_currency(ccy),
                offset=offsets.get(length, 0),
                length=length
            )
        )

    return results


async def calc_daily(client: Client,
                     amount: int = None,
                     throw_exceptions=False,
                     since: datetime = None,
                     to: datetime = None,
                     currency: str = None,
                     db: AsyncSession = None):
    """
    Calculates daily balance changes for a given client.
    """

    history: list[Balance] = await db_all(
        client.daily_balance_stmt(amount=amount, since=since, to=to),
        session=db
    )

    if len(history) == 0:
        if throw_exceptions:
            raise UserInputError(reason='Got no data for this user')
        else:
            return []

    intervals = create_intervals(
        history,
        [t for t in client.transfers if history[0].time < t.time < history[-1].time],
        ccy=currency or client.currency
    )

    return intervals[IntervalType.DAY]


_KT = typing.TypeVar('_KT')
_VT = typing.TypeVar('_VT')


def _add_safe(d: dict[_KT, _VT], key: _KT, val: _VT):
    d[key] = d.get(key, 0) + val


TOffset = Decimal


def transfer_gen(transfers: list[Transfer],
                 ccy: str = None,
                 reset=False) -> Generator[Decimal, typing.Optional[datetime], None]:
    offsets: TOffset = Decimal(0)

    next_time: typing.Optional[datetime] = yield
    if transfers:
        for transfer in transfers:
            if not ccy or transfer.coin == ccy:
                while next_time < transfer.time:
                    next_time = yield offsets
                    if reset:
                        offsets = Decimal(0)
                offsets += transfer.size if not ccy else transfer.amount
            # offsets += transfer.amount if ccy == transfer.coin else transfer.size
            # _add_safe(offsets, ccy, transfer.size)
            # if transfer.extra_currencies:
            #    for ccy, amount in transfer.extra_currencies.items():
            #        _add_safe(offsets, ccy, Decimal(amount))

    # One last yield in case the next_time was beyond the last transfer
    yield offsets
