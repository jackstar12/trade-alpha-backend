from datetime import datetime
from decimal import Decimal

import pytz
from sqlalchemy import Column, Integer, ForeignKey, String, Table, orm, Numeric
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import relationship, Session
from sqlalchemy.ext.hybrid import hybrid_property

from balancebot.common.database import Base
from balancebot.common.database_async import async_session
from balancebot.common.dbmodels.amountmixin import AmountMixin
from balancebot.common.dbmodels.pnldata import PnlData
from balancebot.common.dbmodels.serializer import Serializer
from balancebot.common.dbmodels.execution import Execution
from balancebot.common.enums import Side, ExecType
from balancebot.common.messenger import NameSpace, Category

trade_association = Table('trade_association', Base.metadata,
                          Column('trade_id', ForeignKey('trade.id', ondelete="CASCADE"), primary_key=True),
                          Column('label_id', ForeignKey('label.id', ondelete="CASCADE"), primary_key=True)
                          )


class Trade(Base, Serializer):
    __tablename__ = 'trade'
    __serializer_forbidden__ = ['client', 'initial']

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id', ondelete="CASCADE"), nullable=False)
    client = relationship('Client')
    labels = relationship('Label', secondary=trade_association, backref='trades')

    symbol = Column(String, nullable=False)
    entry = Column(Numeric, nullable=False)

    qty = Column(Numeric, nullable=False)
    open_qty = Column(Numeric, nullable=False)
    transferred_qty = Column(Numeric, nullable=True)

    exit = Column(Numeric, nullable=True)
    realized_pnl = Column(Numeric, nullable=True, default=Decimal('0.0'))

    max_pnl_id = Column(Integer, ForeignKey('pnldata.id', ondelete='SET NULL'), nullable=True)
    max_pnl = relationship('PnlData', lazy='noload', foreign_keys=max_pnl_id, uselist=False)

    min_pnl_id = Column(Integer, ForeignKey('pnldata.id', ondelete='SET NULL'), nullable=True)
    min_pnl = relationship('PnlData', lazy='noload', foreign_keys=min_pnl_id, uselist=False)

    tp = Column(Numeric, nullable=True)
    sl = Column(Numeric, nullable=True)

    order_count = Column(Integer, nullable=True)

    executions = relationship('Execution',
                              foreign_keys='[Execution.trade_id]',
                              backref='trade',
                              lazy='noload',
                              cascade='all, delete')

    pnl_data = relationship('PnlData',
                            lazy='noload',
                            cascade="all, delete",
                            back_populates='trade',
                            foreign_keys="PnlData.trade_id")

    initial_execution_id = Column(Integer, ForeignKey('execution.id', ondelete="SET NULL"), nullable=True)

    initial: Execution = relationship(
        'Execution',
        lazy='joined',
        foreign_keys=initial_execution_id,
        post_update=True,
        primaryjoin='Execution.id == Trade.initial_execution_id',
        uselist=False
    )

    memo = Column(String, nullable=True)

    def _compare_pnl(self, old: PnlData, new: PnlData, cmp_func):
        if not old or cmp_func(new.total, old.total):
            Session.object_session(self).add(new)
            # TODO: Should the PNL objects be persisted in db before publishing them?
            #self._messenger.pub_channel(NameSpace.TRADE, Category.SIGNIFICANT_PNL, channel_id=trade.client_id,
            #                            obj={'id': self.id, 'pnl': new.amount})
            return new
        return old

    def update_pnl(self, price: float, realtime=True, commit=False, now: datetime = None):
        if not now:
            now = datetime.now(pytz.utc)
        upnl = self.calc_upnl(price)
        self.current_pnl = PnlData(
            trade_id=self.id,
            unrealized=upnl,
            realized=self.realized_pnl,
            time=now,
        )
        self.max_pnl = self._compare_pnl(self.max_pnl, self.current_pnl, Decimal.__ge__)
        self.min_pnl = self._compare_pnl(self.min_pnl, self.current_pnl, Decimal.__le__)
        if realtime:
            self._messenger.pub_channel(NameSpace.TRADE, Category.UPNL, channel_id=self.client_id,
                                        obj={'id': self.id, 'upnl': upnl})

    @orm.reconstructor
    def init_on_load(self):
        self.current_pnl: PnlData = None

    def __init__(self, upnl: Decimal = None, *args, **kwargs):
        self.current_pnl: PnlData = PnlData(
            unrealized=upnl,
            realized=self.realized_pnl,
            time=datetime.now(pytz.utc)
        )
        super().__init__(*args, **kwargs)

    @classmethod
    def is_data(cls):
        return True

    @hybrid_property
    def is_open(self):
        return self.open_qty > Decimal('0.0')

    @hybrid_property
    def risk_to_reward(self):
        return (self.tp - self.entry) / (self.entry - self.sl)

    @hybrid_property
    def realized_r(self):
        return (self.exit - self.entry) / (self.entry - self.sl)

    @hybrid_property
    def fomo_ratio(self):
        return self.max_pnl.amount / self.min_pnl.amount

    @hybrid_property
    def greed_ratio(self):
        return self.max_pnl.amount / self.realized_pnl

    async def serialize(self, data=True, full=True, *args, **kwargs):
        s = await super().serialize(*args, data=data, full=full, **kwargs)
        if s:
            s['status'] = 'open' if self.open_qty > Decimal('0.0') else 'win' if self.realized_pnl > 0.0 else 'loss'
        return s

    def calc_rpnl(self):
        realized_qty = self.qty - self.open_qty - self.transferred_qty
        return (self.exit * realized_qty - self.entry * realized_qty) * (Decimal('1') if self.initial.side == Side.BUY else Decimal('-1'))

    def calc_upnl(self, price: Decimal):
        return (price * self.open_qty - self.entry * self.open_qty) * (Decimal('1') if self.initial.side == Side.BUY else Decimal('-1'))


def trade_from_execution(execution: Execution):
    return Trade(
        entry=execution.price,
        qty=execution.qty,
        open_qty=execution.qty if execution.type != ExecType.TRADE else Decimal(0),
        transferred_qty=execution.qty if execution.type == ExecType.TRANSFER else Decimal(0),
        initial=execution,
        symbol=execution.symbol,
        executions=[execution]
    )
