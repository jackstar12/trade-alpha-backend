from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column, Integer, ForeignKey, Numeric, DateTime, orm
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, object_session, Session

import database.dbmodels as dbmodels
from database.dbmodels.client import ClientQueryMixin
from database.dbmodels.mixins.serializer import Serializer
from database.dbsync import Base, BaseMixin
from database.models.balance import Amount as AmountModel, Balance as BalanceModel
from core.utils import round_ccy

if TYPE_CHECKING:
    from database.dbmodels import Client


class _Common:
    realized: Decimal = Column(Numeric, nullable=False, default=Decimal(0))
    unrealized: Decimal = Column(Numeric, nullable=False, default=Decimal(0))


class Amount(Base, ClientQueryMixin, Serializer, BaseMixin, _Common):
    __tablename__ = 'amount'

    balance_id = Column(ForeignKey('balance.id', ondelete="CASCADE"), primary_key=True)
    balance = relationship('Balance', lazy='raise')
    currency: str = Column(sa.String, primary_key=True)
    rate = Column(Numeric, nullable=True)


class Balance(Base, _Common, Serializer, BaseMixin, ClientQueryMixin):
    """
    Represents the balance of a client at a given time.

    It is divided into mulitple Amount objects.
    The 'realized' field contains the total currently realized equity
    The 'unrealized' field contains the total current equity including unrealized pnl

    If the balance consists of multiple currencies, these are stored in detail in the Amount table (
    """
    __tablename__ = 'balance'
    __model__ = BalanceModel
    __serializer_forbidden__ = ['id', 'error', 'client']

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id', ondelete="CASCADE"), nullable=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)

    client: 'Client' = relationship('Client', lazy='raise', foreign_keys=client_id)
    extra_currencies: list[Amount] = relationship('Amount', lazy='joined', back_populates='balance')

    @hybrid_property
    def client_save(self):
        session: Session = object_session(self)
        if session and self.client_id:
            return session.identity_map.get(
                session.identity_key(dbmodels.Client, self.client_id)
            )
        return self.client

    @hybrid_property
    def total(self):
        return self.unrealized

    @hybrid_property
    def total_transfers_corrected(self):
        return self.unrealized

    @hybrid_property
    def currency(self):
        client = self.client_save
        return client.currency if client else None

    #def serialize(self, full=False, data=True, include_none=True, *args, **kwargs):
    #    d = BalanceModel.from_orm(self).dict()
    #    d['client_id'] = self.client_id
    #    return d

    def get_amount(self, ccy: str = None):
        for amount in self.extra_currencies:
            if amount.currency == ccy:
                return amount
        amt = Amount(currency=ccy, realized=0, unrealized=0)
        self.extra_currencies.append(amt)
        return amt

    def get_currency(self, ccy: str = None) -> AmountModel:
        for amount in self.extra_currencies:
            if amount.currency == ccy:
                return AmountModel(
                    realized=amount.realized,
                    unrealized=amount.unrealized,
                    currency=ccy,
                    time=self.time
                )
        return AmountModel(
            realized=self.realized,
            unrealized=self.unrealized,
            currency=self.client_save.currency,
            time=self.time
        )

    def get_realized(self, ccy: str) -> Decimal:
        amount = self.get_currency(ccy)
        return amount.realized if amount else self.realized

    def get_unrealized(self, ccy: str) -> Decimal:
        amount = self.get_currency(ccy)
        return amount.unrealized if amount else self.unrealized

    def __eq__(self, other):
        if isinstance(other, Balance):
            return self.realized == other.realized and self.unrealized == other.unrealized
        return False

    def __init__(self, error=None, *args, **kwargs):
        self.error = error
        super().__init__(*args, **kwargs)

    @orm.reconstructor
    def reconstructor(self):
        self.error = None

    def to_string(self, display_extras=False):
        ccy = self.client_save.currency
        string = f'{round_ccy(self.unrealized, ccy)}{ccy}'

        if self.extra_currencies and display_extras:
            currencies = " / ".join(
                f'{amount.unrealized}{amount.currency}'
                for amount in self.extra_currencies
            )
            string += f'({currencies})'

        return string

    def __repr__(self):
        return self.to_string(display_extras=False)

    @classmethod
    def is_data(cls):
        return True
