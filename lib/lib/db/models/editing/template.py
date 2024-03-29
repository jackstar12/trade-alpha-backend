from enum import Enum

import sqlalchemy as sa
from sqlalchemy import orm

from lib.db.models.editing.pagemixin import PageMixin
from lib.db.models.types import Data
from lib.db.dbsync import Base


class TemplateType(Enum):
    CHAPTER = "chapter"
    TRADE = "trade"


class Template(Base, PageMixin):
    __tablename__ = "template"

    user_id = sa.Column(sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    data = sa.Column(Data, nullable=True)
    type = sa.Column(sa.Enum(TemplateType), nullable=False)

    user = orm.relationship("User", lazy="noload")
    journals = orm.relationship(
        "Journal",
        foreign_keys="Journal.default_template_id",
        back_populates="default_template",
        lazy="noload",
    )

    __mapper_args__ = {"polymorphic_on": type}


class TradeTemplate(Template):
    __mapper_args__ = {"polymorphic_identity": TemplateType.TRADE}


class ChapterTemplate(Template):
    __mapper_args__ = {"polymorphic_identity": TemplateType.CHAPTER}
