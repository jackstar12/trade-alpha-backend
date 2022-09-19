from __future__ import annotations
from datetime import datetime
from typing import Any, TYPE_CHECKING

import pytz
from sqlalchemy import Column, ForeignKey, Numeric, Integer, DateTime, and_, asc, ForeignKeyConstraint, desc, select
from sqlalchemy.orm import relationship, declared_attr, foreign, aliased

if TYPE_CHECKING:
    from tradealpha.common.dbmodels.balance import Balance as BalanceDB
    from tradealpha.common.models.balance import Balance as BalanceModel
from tradealpha.common.dbsync import Base


class EventRank(Base):
    __tablename__ = 'eventrank'
    client_id = Column(ForeignKey('client.id', ondelete='CASCADE'), primary_key=True)
    event_id = Column(ForeignKey('event.id', ondelete='CASCADE'), primary_key=True)
    value = Column(Integer, nullable=False)
    time = Column(DateTime(timezone=True), primary_key=True, default=lambda: datetime.now(pytz.utc))


class EventScore(Base):
    __tablename__ = 'eventscore'
    client_id = Column(ForeignKey('client.id', ondelete='CASCADE'), primary_key=True)
    event_id = Column(ForeignKey('event.id', ondelete='CASCADE'), primary_key=True)

    abs_value = Column(Numeric, nullable=True)
    rel_value = Column(Numeric, nullable=True)
    init_balance_id = Column(ForeignKey('balance.id', ondelete='CASCADE'), nullable=True)
    last_rank_update = Column(DateTime(timezone=True), nullable=True)
    rekt_on = Column(DateTime(timezone=True), nullable=True)

    init_balance: BalanceDB = relationship('Balance', lazy='noload')
    client = relationship('Client', lazy='noload')
    event = relationship('Event', lazy='noload')

    current_rank = relationship('EventRank',
                                lazy='joined',
                                uselist=False
                                )
    rank_history: list[EventRank]


    __table_args__ = (ForeignKeyConstraint((client_id, event_id, last_rank_update),
                                           ('eventrank.client_id', 'eventrank.event_id', 'eventrank.time')),
                      {})

    def update(self, balance: BalanceModel | BalanceDB):
        gain = balance.total_transfers_corrected - self.init_balance.total_transfers_corrected
        self.abs_value = gain
        self.rel_value = gain / self.init_balance.realized


equ = and_(
    EventScore.client_id == foreign(EventRank.client_id),
    EventScore.event_id == foreign(EventRank.event_id)
)

EventScore.rank_history = relationship(EventRank,
                                       lazy='noload',
                                       primaryjoin=equ,
                                       order_by=asc(EventRank.time))
