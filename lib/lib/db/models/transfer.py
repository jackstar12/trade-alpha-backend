import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Column, Integer, ForeignKey, String
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property

from lib.db.dbsync import Base, FKey, fkey_name, BaseMixin
from lib.db.enums import Side, MarketType
from lib.models import BaseModel


class TransferType(enum.Enum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"


class RawTransfer(BaseModel):
    amount: Decimal
    time: datetime
    coin: str
    fee: Optional[Decimal]
    market_type: Optional[MarketType]


class Transfer(Base, BaseMixin):
    __tablename__ = "transfer"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, FKey("client.id", ondelete="CASCADE"), nullable=False)
    execution_id = Column(
        Integer,
        ForeignKey(
            "execution.id",
            ondelete="CASCADE",
            name=fkey_name(__tablename__, "execution_id"),
        ),
        nullable=False,
    )

    note = Column(String, nullable=True)
    coin = Column(String, nullable=True)

    client = relationship("Client")
    execution = relationship(
        "Execution",
        foreign_keys=execution_id,
        uselist=False,
        cascade="all, delete",
        lazy="joined",
        back_populates="transfer",
    )

    @hybrid_property
    def time(self):
        return self.execution.time

    @hybrid_property
    def commission(self):
        return self.execution.commission

    @hybrid_property
    def size(self):
        return self.execution.effective_size

    @hybrid_property
    def amount(self):
        return self.execution.effective_qty

    # balance = relationship(
    #    'Balance',
    #    back_populates='transfer',
    #    lazy='joined',
    #    uselist=False
    # )

    @hybrid_property
    def type(self) -> TransferType:
        return (
            TransferType.DEPOSIT
            if self.execution.side == Side.BUY
            else TransferType.WITHDRAW
        )

    def __repr__(self):
        return f"{self.type.value} {self.amount}USD ({self.coin})"
