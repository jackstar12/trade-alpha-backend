import operator
from datetime import datetime
import sqlalchemy as sa
import enum

from lib.db.dbsync import Base, BaseMixin
from sqlalchemy import Column, Integer, DateTime
from lib.db.models.mixins.serializer import Serializer


class OP(enum.Enum):
    greater = operator.ge
    less = operator.le
    equal = operator.eq


value = int | float | bool | str


class Expression:
    var: str
    cmp: value
    op: OP


# Example goal:
# Winrate > 50% && Kelly Optimization = True


class Rule(Base, Serializer, BaseMixin):
    id: int = Column(Integer, primary_key=True)
    creation_date: datetime = Column(DateTime(timezone=True))
    finish_date: datetime = Column(DateTime(timezone=True))
    conditions = Column(sa.JSON, nullable=False)
    expression = Column(sa.JSON, nullable=False)
    expressions: list[Expression]
