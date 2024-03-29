from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

import pytz
import sqlalchemy.exc
from aioredis import Redis
from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    String,
    Table,
    orm,
    Numeric,
    delete,
    DateTime,
    func,
    case,
    extract,
    or_,
    and_,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, aliased

from lib.utils import weighted_avg, join_args
from lib.db.models.mixins.filtermixin import FilterMixin, FilterParam
from lib.db.models.types import Document
from lib.models.document import Operator
from lib.models.trade import BasicTrade
from lib.db.redis import TableNames

if TYPE_CHECKING:
    from lib.db.models import Balance

from lib.db.models.pnldata import PnlData
from lib.db.dbsync import Base, BaseMixin, FKey
from lib.db.models.mixins.serializer import Serializer
from lib.db.models.execution import Execution
from lib.db.enums import Side, ExecType, Status, TradeSession

from lib import utils
from lib.db.models.symbol import CurrencyMixin

trade_association = Table(
    "trade_association",
    Base.metadata,
    Column("trade_id", FKey("trade.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", FKey("label.id", ondelete="CASCADE"), primary_key=True),
)


# class TradeType(Enum):
#     SPOT = "spot"
#     FUTURES = "futures"
#     TRANSFER = "transfer"


class InternalTradeModel(BasicTrade):
    id: int
    client_id: int


class Trade(Base, Serializer, BaseMixin, CurrencyMixin, FilterMixin):
    __tablename__ = "trade"
    __serializer_forbidden__ = ["client"]
    __model__ = InternalTradeModel

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, FKey("client.id", ondelete="CASCADE"), nullable=False)
    client = relationship("Client", lazy="noload")
    labels = relationship(
        "Label", lazy="noload", secondary=trade_association, backref="trades"
    )

    symbol = Column(String, nullable=False)

    entry: Decimal = Column(Numeric, nullable=False)
    qty: Decimal = Column(Numeric, nullable=False)
    open_qty: Decimal = Column(Numeric, nullable=False)
    transferred_qty: Decimal = Column(Numeric, nullable=True)
    open_time: datetime = Column(DateTime(timezone=True), nullable=False)
    close_time: datetime = Column(DateTime(timezone=True), nullable=True)

    exit: Decimal = Column(Numeric, nullable=True)
    realized_pnl: Decimal = Column(Numeric, nullable=True, default=Decimal(0))
    total_commissions: Decimal = Column(Numeric, nullable=True, default=Decimal(0))
    settle = Column(String(5), nullable=False)

    init_balance_id = Column(
        Integer, ForeignKey("balance.id", ondelete="SET NULL"), nullable=False
    )
    init_balance = relationship(
        "Balance",
        lazy="raise",
        foreign_keys=init_balance_id,
        passive_deletes=True,
        uselist=False,
    )

    init_amount = relationship(
        "Amount",
        lazy="noload",
        primaryjoin="and_(Trade.init_balance_id == foreign(Amount.balance_id), Trade.settle == foreign(Amount.currency))",
        passive_deletes=True,
        uselist=False,
    )

    max_pnl_id = Column(
        Integer, ForeignKey("pnldata.id", ondelete="SET NULL"), nullable=True
    )
    max_pnl: Optional[PnlData] = relationship(
        "PnlData",
        lazy="raise",
        foreign_keys=max_pnl_id,
        passive_deletes=True,
        post_update=True,
    )

    min_pnl_id = Column(
        Integer, ForeignKey("pnldata.id", ondelete="SET NULL"), nullable=True
    )
    min_pnl: Optional[PnlData] = relationship(
        "PnlData",
        lazy="raise",
        foreign_keys=min_pnl_id,
        passive_deletes=True,
        post_update=True,
    )

    tp: Decimal = Column(Numeric, nullable=True)
    sl: Decimal = Column(Numeric, nullable=True)

    order_count = Column(Integer, nullable=True)

    executions: list[Execution] = relationship(
        "Execution",
        foreign_keys="[Execution.trade_id]",
        back_populates="trade",
        lazy="raise",
        passive_deletes=True,
        order_by="Execution.time",
    )

    pnl_data: list[PnlData] = relationship(
        "PnlData",
        lazy="noload",
        back_populates="trade",
        foreign_keys="PnlData.trade_id",
        passive_deletes=True,
        order_by="PnlData.time",
    )

    initial_execution_id = Column(
        Integer, FKey("execution.id", ondelete="SET NULL"), nullable=True
    )
    initial: Execution = relationship(
        "Execution",
        lazy="joined",
        foreign_keys=initial_execution_id,
        post_update=True,
        passive_deletes=True,
        primaryjoin="Execution.id == Trade.initial_execution_id",
    )

    notes = Column(Document, nullable=True)

    @hybrid_property
    def count(self):
        return func.count().label("count")

    @hybrid_property
    def gross_win(self):
        return func.sum(
            case({self.realized_pnl > 0: self.realized_pnl}, else_=0)
        ).label("gross_win")

    @hybrid_property
    def gross_loss(self):
        return func.sum(
            case({self.realized_pnl < 0: self.realized_pnl}, else_=0)
        ).label("gross_loss")

    @hybrid_property
    def total_commissions_stmt(self):
        return func.sum(self.total_commissions)

    @orm.reconstructor
    def init_on_load(self):
        self.live_pnl: Optional[PnlData] = None
        try:
            self.latest_pnl: PnlData = utils.list_last(self.pnl_data, None)
        except sqlalchemy.exc.InvalidRequestError:
            self.latest_pnl = None

    def __init__(self, upnl: Optional[Decimal] = None, *args, **kwargs):
        self.live_pnl: PnlData = PnlData(
            unrealized=upnl, realized=self.realized_pnl, time=datetime.now(pytz.utc)
        )
        self.latest_pnl: PnlData = utils.list_last(self.pnl_data, None)
        super().__init__(*args, **kwargs)

    @hybrid_property
    def label_ids(self):
        return [str(label.id) for label in self.labels]

    @hybrid_property
    def side(self):
        return (self.initial or self.executions[0]).side

    @side.expression
    def side(self):
        return Execution.side

    @hybrid_property
    def net_pnl(self):
        return self.realized_pnl - self.total_commissions

    @hybrid_property
    def size(self):
        return self.qty / self.entry if self.inverse else self.entry * self.qty

    @property
    def sessions(self):
        result = []
        hour = self.open_time.hour
        if hour >= 22 or hour < 9:
            result.append(TradeSession.ASIA)
        if 8 <= hour < 16:
            result.append(TradeSession.LONDON)
        if 13 <= hour < 22:
            result.append(TradeSession.NEW_YORK)
        return result

    @classmethod
    def is_(cls, session: TradeSession):
        hour = extract("hour", cls.open_time)
        if session == TradeSession.ASIA:
            return hour >= 22 or hour < 9
        if session == TradeSession.LONDON:
            return and_(8 <= hour, hour < 16)
        if session == TradeSession.NEW_YORK:
            return 13 <= hour and hour < 22
        return False

    @hybrid_property
    def weekday(self):
        return self.open_time.weekday()

    @weekday.expression
    def weekday(cls):
        return extract("dow", cls.open_time)

    @hybrid_property
    def account_gain(self):
        return self.net_pnl / self.init_amount.realized

    @hybrid_property
    def net_gain(self):
        return self.net_pnl / self.init_amount.realized

    @hybrid_property
    def account_size(self):
        return self.size / self.init_amount.realized

    @hybrid_property
    def compact_pnl_data(self):
        return [pnl_data.compact for pnl_data in self.pnl_data]

    @hybrid_property
    def pnl_history(self):
        return self.compact_pnl_data

    @classmethod
    def is_data(cls):
        return True

    # @property
    # def init_amount(self):
    #    return self.init_balance.get_currency(ccy=self.settle)

    @hybrid_property
    def is_open(self):
        return self.open_qty > Decimal(0)

    @hybrid_property
    def risk_to_reward(self):
        if self.tp and self.sl:
            return (self.tp - self.entry) / (self.entry - self.sl)

    @hybrid_property
    def realized_r(self):
        if self.sl:
            return (self.exit - self.entry) / (self.entry - self.sl)

    @property
    def commission_ratio(self):
        return self.realized_pnl / self.total_commissions

    @hybrid_property
    def fomo_ratio(self):
        if self.max_pnl.total != self.min_pnl.total:
            return 1 - (self.realized_pnl - self.min_pnl.total) / (
                self.max_pnl.total - self.min_pnl.total
            )
        else:
            return 0

    @hybrid_property
    def greed_ratio(self):
        if self.max_pnl.total > 0:
            return 1 - abs(self.realized_pnl) / self.max_pnl.total
        elif self.max_pnl.total < 0:
            return 1 - self.realized_pnl / self.max_pnl.total
        return 0

    @hybrid_property
    def status(self):
        return (
            Status.OPEN
            if self.open_qty != 0
            else Status.WIN
            if self.realized_pnl > 0
            else Status.LOSS
        )

    @hybrid_property
    def realized_qty(self):
        # Subtracting the transferred qty is important because
        # "trades" which were initiated by a transfer should not provide any pnl.
        return self.qty - self.open_qty - self.transferred_qty

    @hybrid_property
    def duration(self):
        return self.close_time - self.open_time if self.close_time else None

    @classmethod
    def apply(cls, param: FilterParam, stmt):
        if param.field == "label_ids":
            return stmt.join(
                trade_association,
                and_(
                    trade_association.columns.trade_id == cls.id,
                    or_(
                        trade_association.columns.label_id == value
                        if param.op == Operator.INCLUDES
                        else not cls.is_(value)
                        for value in param.values
                    ),
                ),
            )
        col = getattr(cls, param.field)
        if col == cls.side:
            alias = aliased(Execution)
            return stmt.join(
                alias,
                and_(
                    cls.initial_execution_id == alias.id,
                    or_(*[param.cmp_func(alias.side, value) for value in param.values]),
                ),
            )
        elif col == cls.sessions:
            return stmt.where(
                *[
                    cls.is_(value)
                    if param.op == Operator.INCLUDES
                    else not cls.is_(value)
                    for value in param.values
                ]
            )
        else:
            raise ValueError

    @classmethod
    def validator(cls, field: str):
        if field == "sessions":
            return TradeSession
        if field == "label_ids":
            return int

    def calc_pnl(self, qty: Decimal, exit: Decimal):
        diff = (
            1 / self.entry - 1 / Decimal(exit)
            if self.inverse
            else Decimal(exit) - self.entry
        )
        raw = diff * qty
        return raw * -1 if self.initial.side == Side.SELL else raw

    def calc_upnl(self, price: Decimal):
        return self.calc_pnl(self.open_qty, price)

    @property
    def redis_key(self):
        return join_args(TableNames.TRADE, self.id)

    async def set_live_pnl(self, redis: Redis):
        await redis.hset(
            self.redis_key, key="upnl", value=float(self.live_pnl.unrealized)
        )

    def update_pnl(
        self,
        upnl: int | Decimal,
        force=False,
        now: Optional[datetime] = None,
        extra_currencies: Optional[dict[str, Decimal]] = None,
    ):
        if not now:
            now = datetime.now(pytz.utc)
        self.live_pnl = PnlData(
            trade_id=self.id,
            trade=self,
            unrealized=upnl,
            realized=self.realized_pnl,
            time=now,
            extra_currencies={
                currency: rate
                for currency, rate in extra_currencies.items()
                if currency != self.settle
            }
            if extra_currencies
            else None,
        )

        if not self.max_pnl:
            self.max_pnl = PnlData(
                trade=self,
                realized=Decimal(0),
                unrealized=Decimal(0),
                time=self.open_time,
            )
        if not self.min_pnl:
            self.min_pnl = PnlData(
                trade=self,
                realized=Decimal(0),
                unrealized=Decimal(0),
                time=self.open_time,
            )

        significant = False

        live = self.live_pnl.total
        if (
            not self.latest_pnl
            or force
            or self.max_pnl.total == self.min_pnl.total
            or abs((live - self.latest_pnl.total) / self.size) > Decimal(0.05)
        ):
            self.pnl_data.append(self.live_pnl)
            self.latest_pnl = self.live_pnl
            latest = self.latest_pnl.total
            if latest:
                if latest > self.max_pnl.total:
                    self.max_pnl = self.latest_pnl
                if latest < self.min_pnl.total:
                    self.min_pnl = self.latest_pnl
            significant = True
        else:
            if self._replace_pnl(self.max_pnl, self.live_pnl, Decimal.__ge__):
                significant = True
            if self._replace_pnl(self.min_pnl, self.live_pnl, Decimal.__le__):
                significant = True

        return significant

    def add_execution(
        self, execution: Execution, current_balance: Optional[Balance] = None
    ):
        self.executions.append(execution)
        new = None

        if execution.type in (ExecType.FUNDING, ExecType.LIQUIDATION):
            self.realized_pnl += execution.realized_pnl or 0
            self.total_commissions += execution.commission or 0

        if execution.type in (ExecType.TRADE, ExecType.TRANSFER):
            if execution.side == self.initial.side:
                self.entry = weighted_avg(
                    (self.entry, execution.price), (self.qty, execution.qty)
                )
                self.qty += execution.qty
                self.open_qty += execution.qty
                if execution.commission:
                    self.total_commissions += execution.commission
            elif execution.reduce:
                if execution.qty > self.open_qty:
                    new_exec = Execution(
                        qty=execution.qty - self.open_qty,
                        symbol=execution.symbol,
                        price=execution.price,
                        side=execution.side,
                        time=execution.time,
                        type=execution.type,
                        settle=execution.settle,
                    )
                    # Because the execution is "split" we also have to assign
                    # the commissions accordingly
                    if execution.commission:
                        new_exec.commission = (
                            execution.commission * new_exec.qty / execution.qty
                        )
                        execution.commission -= new_exec.commission
                    execution.qty = self.open_qty

                    new = Trade.from_execution(
                        new_exec, self.client_id, current_balance
                    )

                if self.exit is None:
                    self.exit = execution.price
                else:
                    realized_qty = self.qty - self.open_qty
                    self.exit = weighted_avg(
                        (self.exit, execution.price), (realized_qty, execution.qty)
                    )

                if execution.realized_pnl is None:
                    execution.realized_pnl = self.calc_pnl(
                        execution.qty, execution.price
                    )

                self.open_qty -= execution.qty
                self.realized_pnl += execution.realized_pnl

                if execution.commission:
                    self.total_commissions += execution.commission

                if self.open_qty.is_zero():
                    self.update_pnl(Decimal(0), force=True, now=execution.time)
            else:
                new = Trade.from_execution(execution, self.client_id, current_balance)

        return new

    async def reverse_to(self, date: datetime, db: AsyncSession) -> Optional[Trade]:
        """
        Method used for setting a trade back to a specific point in time.
        Used when an invalid series of executions is detected (e.g. websocket shut down
        without notice)

        :param date: the date to reverse
        :param db: database session
        :return:
        """
        if self.executions[-1].time > date:
            self.__realtime__ = False
            await db.delete(self)

            if date > self.open_time:
                # First, create a new copy based on the same initial execution
                new_trade = Trade.from_execution(
                    self.initial, self.client_id, self.init_balance
                )
                new_trade.__realtime__ = False

                # Then reapply the executions that are not due for deletion
                # (important that initial is excluded in this case)
                for execution in self.executions[1:]:
                    if execution.time < date:
                        new_trade.add_execution(execution, self.init_balance)
                    else:
                        await db.delete(execution)

                self.__realtime__ = False
                await db.execute(
                    delete(PnlData).where(
                        PnlData.trade_id == self.id, PnlData.time > date
                    )
                )

                db.add(new_trade)

                return new_trade

    @classmethod
    def _replace_pnl(cls, old: PnlData, new: PnlData, cmp_func):
        if not old or cmp_func(new.total, old.total):
            old.unrealized = new.unrealized
            old.realized = new.realized
            old.time = new.time
            return True
        return False

    @classmethod
    def from_execution(
        cls,
        execution: Execution,
        client_id: int,
        current_balance: Optional[Balance] = None,
    ):
        trade = Trade(
            entry=execution.price,
            qty=execution.qty,
            open_time=execution.time,
            open_qty=execution.qty,
            transferred_qty=execution.qty
            if execution.type == ExecType.TRANSFER
            else Decimal(0),
            initial=execution,
            total_commissions=execution.commission,
            symbol=execution.symbol,
            executions=[execution],
            inverse=execution.inverse,
            settle=execution.settle,
            client_id=client_id,
            init_balance=current_balance,
            realized_pnl=0,
        )
        execution.trade = trade

        trade.max_pnl = PnlData(
            trade=trade, realized=Decimal(0), unrealized=Decimal(0), time=execution.time
        )
        trade.min_pnl = PnlData(
            trade=trade, realized=Decimal(0), unrealized=Decimal(0), time=execution.time
        )
        return trade


@event.listens_for(Trade, "before_update")
def before_update(mapper, connection, trade: Trade):
    if not trade.is_open and not trade.close_time:
        trade.close_time = trade.executions[-1].time
