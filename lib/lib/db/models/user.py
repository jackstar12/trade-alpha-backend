from __future__ import annotations

import enum
from typing import Optional, TYPE_CHECKING

import sqlalchemy as sa
from aioredis import Redis
from fastapi_users_db_sqlalchemy import (
    SQLAlchemyBaseUserTableUUID,
    SQLAlchemyBaseOAuthAccountTableUUID,
)
from sqlalchemy import Column, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from lib.utils import join_args
from lib.db.dbasync import redis, db_all
import lib.db.models as dbmodels
from lib.db.models.mixins.editsmixin import EditsMixin
from lib.db.models.mixins.serializer import Serializer
from lib.db.models.types import Document
from lib.db.dbsync import Base, BaseMixin
from lib.models.user import ProfileData, UserProfile

if TYPE_CHECKING:
    from lib.db.models.discord.discorduser import DiscordUser


class Subscription(enum.Enum):
    FREE = 1
    BASIC = 2
    PREMIUM = 3


class OAuthAccount(Base, SQLAlchemyBaseOAuthAccountTableUUID):
    account_id: str = Column(
        sa.String(length=320), index=True, nullable=False, unique=True
    )
    data: Optional[ProfileData] = Column(JSONB, nullable=True)

    __mapper_args__ = {
        "polymorphic_on": "oauth_name",
    }

    async def populate_oauth_data(self, redis: Redis) -> Optional[ProfileData]:
        return self.data


class User(Base, Serializer, BaseMixin, SQLAlchemyBaseUserTableUUID, EditsMixin):
    __tablename__ = "user"
    __serializer_forbidden__ = ["hashed_password", "salt"]

    oauth_accounts: list[OAuthAccount] = relationship("OAuthAccount", lazy="joined")

    # Identity
    # discord_user_id = Column(BigInteger, ForeignKey('discorduser.id', ondelete='SET NULL'), nullable=True)
    # discord_user = relationship('DiscordUser',
    #                             lazy='noload',
    #                             backref=backref('user', lazy='noload', uselist=False),
    #                             uselist=False, foreign_keys=discord_user_id)

    subscription = Column(
        sa.Enum(Subscription), default=Subscription.BASIC, nullable=False
    )

    info: str | ProfileData | None = Column(JSONB, nullable=True)
    about_me = Column(Document, nullable=True)
    events = relationship("Event", back_populates="owner")

    all_clients = relationship(
        "Client",
        lazy="raise",
        primaryjoin="or_(" "Client.user_id == User.id,"
        # 'and_(Client.oauth_account_id == OAuthAccount.account_id, OAuthAccount.user_id == User.id)'
        ")",
        viewonly=True,
    )

    # Data
    clients = relationship(
        "Client",
        back_populates="user",
        lazy="noload",
        cascade="all, delete",
        foreign_keys="[Client.user_id]",
    )

    label_groups = relationship(
        "LabelGroup", backref="user", lazy="raise", cascade="all, delete"
    )
    alerts = relationship("Alert", backref="user", lazy="noload", cascade="all, delete")

    journals = relationship("Journal", back_populates="user", cascade="all, delete")

    templates = relationship("Template", back_populates="user", cascade="all, delete")

    @property
    def redis_key(self):
        return f"user:{self.id}"

    async def get_client_ids(self) -> list[int]:
        key = join_args(self.redis_key, "clients")
        client_ids = await redis.smembers(key)
        if not client_ids:
            client_ids = await db_all(
                select(dbmodels.Client.id).where(dbmodels.Client.user_id == self.id),
                session=self.async_session,
            )
            await redis.sadd(key, *client_ids)
        else:
            client_ids = [int(client_id) for client_id in client_ids]
        return client_ids

    def get_oauth(self, name: str) -> Optional[OAuthAccount]:
        for account in self.oauth_accounts:
            if account.oauth_name == name:
                return account

    @property
    def profile(self) -> UserProfile:
        src = None
        if isinstance(self.info, str):
            account = self.get_oauth(self.info)
            if account:
                data = account.data
                src = self.info
            else:
                data = None
        else:
            data = self.info
        if not data:
            data = ProfileData(
                name=self.email,
                avatar_url="",
            )
        return UserProfile(**data, src=src)

    @property
    def discord(self) -> Optional[DiscordUser]:
        return self.get_oauth("discord")

    @classmethod
    def mock(cls):
        return cls(email="mock@gmail.com", hashed_password="SUPER_SECURE")
