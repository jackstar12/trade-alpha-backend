from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from lib.db.models.authgrant import ChapterGrant
from lib.db.models.user import User
from lib.db.models.mixins.editsmixin import EditsMixin
from lib.db.models.mixins.serializer import Serializer
from lib.db.models.types import Platform
from lib.db.dbsync import Base, BaseMixin
from lib.db.redis import TableNames


class ActionType(Enum):
    CLIENT = TableNames.CLIENT.value
    TRADE = TableNames.TRADE.value
    JOURNAL = TableNames.JOURNAL.value
    BALANCE = TableNames.BALANCE.value
    EVENT = TableNames.EVENT.value
    EXECUTION = TableNames.EXECUTION.value
    CHAPTER_GRANT = ChapterGrant.__tablename__


class ActionTrigger(Enum):
    ONCE = "once"
    RECURRING = "recurring"


class Action(Base, BaseMixin, EditsMixin, Serializer):
    __tablename__ = "action"

    id = sa.Column(sa.Integer, primary_key=True)
    user_id = sa.Column(sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    user: User = relationship("User", lazy="raise")

    name = sa.Column(sa.String, nullable=True)
    type = sa.Column(sa.Enum(ActionType), nullable=False)
    topic = sa.Column(sa.String, nullable=False)
    platform = sa.Column(Platform, nullable=False)
    message = sa.Column(sa.String, nullable=True)
    delay = sa.Column(sa.Interval, nullable=True)
    trigger_type = sa.Column(sa.Enum(ActionTrigger), nullable=False)
    _trigger_ids = sa.Column("trigger_ids", JSONB, nullable=True)

    @hybrid_property
    def trigger_ids(self):
        return self._trigger_ids or {}

    @trigger_ids.setter
    def trigger_ids(self, value):
        self._trigger_ids = value or None

    @hybrid_property
    def all_ids(self):
        res = {"user_id": self.user_id}
        res |= self.trigger_ids
        return res
