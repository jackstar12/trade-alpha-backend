from lib.db.models.mixins.serializer import Serializer

from lib.db.dbsync import Base, BaseMixin
from sqlalchemy.orm import relationship, backref
from sqlalchemy import Column, String, BigInteger, Enum

from lib.db.enums import Tier


class Guild(Base, Serializer, BaseMixin):
    __tablename__ = "guild"

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String, nullable=True)
    tier = Column(Enum(Tier), default=Tier.BASE, nullable=False)
    avatar = Column(String, nullable=True)

    events = relationship(
        "Event",
        lazy="raise",
        backref=backref("guild", lazy="raise"),
        viewonly=True,
        primaryjoin="Guild.id == foreign(Event.guild_id)",
    )

    users = relationship(
        "DiscordUser",
        secondary="guild_association",
        lazy="raise",
        backref=backref("guilds", lazy="raise"),
        viewonly=True,
    )

    associations = relationship(
        "GuildAssociation", lazy="raise", viewonly=True, back_populates="guild"
    )
