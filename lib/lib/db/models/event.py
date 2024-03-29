from __future__ import annotations

import operator
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

import discord
import numpy
import pytz
import sqlalchemy.exc
from aioredis import Redis
from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    String,
    DateTime,
    Boolean,
    func,
    desc,
    select,
    and_,
    Numeric,
    BigInteger,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, backref
from sqlalchemy.orm.util import identity_key
from sqlalchemy_utils import get_mapper

from lib.utils import join_args, utc_now
from lib.utils import safe_cmp
from lib.db.dbasync import db_all, opt_op
from lib.db.models.evententry import EventEntry, EventScore
from lib.db.models.mixins.serializer import Serializer
from lib.db.models.types import Document
from lib.db.models.types import Platform
from lib.db.dbsync import Base, BaseMixin
from lib.models.discord.guild import GuildRequest
from lib.models.eventinfo import EventState, Summary, Leaderboard, EventScore, Stat
from lib.models.platform import PlatformModel
from lib.db.redis import rpc
from lib.db.utils import get_client_history

if TYPE_CHECKING:
    from lib.db.models.user import User
    from lib.db.models.client import Client

SummaryType = Summary.get_sa_type(validate=True)


class Event(Base, Serializer, BaseMixin):
    __tablename__ = "event"

    id = Column(Integer, primary_key=True)
    owner_id = Column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)

    registration_start = Column(DateTime(timezone=True), nullable=False)
    registration_end = Column(DateTime(timezone=True), nullable=False)
    start = Column(DateTime(timezone=True), nullable=False)
    end = Column(DateTime(timezone=True), nullable=False)

    max_registrations = Column(Integer, nullable=False, default=100)
    allow_transfers = Column(Boolean, default=False)

    name = Column(String, nullable=False)
    description = Column(Document, nullable=False)
    location = Column(Platform, nullable=False)
    currency = Column(String(10), nullable=False, server_default="USD")
    rekt_threshold = Column(Numeric, nullable=False, server_default="-99")
    final_summary = Column(SummaryType, nullable=True)

    owner: User = relationship("User", lazy="noload")

    clients: list[Client] = relationship(
        "Client",
        lazy="raise",
        secondary="evententry",
        backref=backref("events", lazy="raise"),
    )

    entries: list[EventEntry] = relationship(
        "EventEntry", lazy="raise", back_populates="event"
    )

    actions = relationship(
        "Action",
        lazy="raise",
        backref=backref("event", lazy="noload"),
        primaryjoin="foreign(Action.trigger_ids['event_id'].astext.cast(Integer)) == Event.id",
        cascade="all, delete",
    )

    grants = relationship("EventGrant")

    @hybrid_property
    def all_clients(self):
        try:
            return self.clients
        except sqlalchemy.exc.InvalidRequestError:
            return [score.client for score in self.entries]

    def validate_time_range(
        self, since: Optional[datetime] = None, to: Optional[datetime] = None
    ):
        since = max(self.start, since) if since else self.start
        to = min(self.end, to) if to else self.end
        return since, to

    @hybrid_property
    def state(self):
        now = datetime.now(pytz.utc)
        res = []
        if self.registration_start > now:
            res.append(EventState.UPCOMING)
        if self.end < now:
            res.append(EventState.ARCHIVED)
        if self.start <= now <= self.end:
            res.append(EventState.ACTIVE)
        if self.registration_start <= now <= self.registration_end:
            res.append(EventState.REGISTRATION)
        return res

    def is_(self, state: EventState):
        return state in self.state

    @property
    def save_interval(self):
        return (self.end - self.start).total_seconds() // 60

    @classmethod
    def is_expr(cls, state: EventState):
        now = func.now()
        if state == EventState.ARCHIVED:
            return cls.end < func.now()
        elif state == EventState.ACTIVE:
            return and_(cls.start <= now, now <= cls.end)
        elif state == EventState.REGISTRATION:
            return and_(cls.registration_start <= now, now <= cls.registration_end)
        else:
            return False

    def validate(self):
        if self.start >= self.end:
            raise ValueError("Start time can't be after end time.")
        if self.registration_start >= self.registration_end:
            raise ValueError("Registration start can't be after registration end")
        if self.registration_end < self.start:
            raise ValueError("Registration end should be after or at event start")
        if self.registration_end > self.end:
            raise ValueError("Registration end can't be after event end.")
        if self.registration_start > self.start:
            raise ValueError("Registration start should be before event start.")
        if self.max_registrations < len(self.all_clients):
            raise ValueError(
                "Max Registrations can not be less than current registration count"
            )

    async def get_saved_leaderboard(self, date: Optional[datetime] = None):
        sub = (
            select(
                func.row_number.over(
                    order_by=desc(EventScore.time), partition_by=EventScore.client_id
                ).label("rank")
            )
            .where(
                opt_op(EventScore.time, date, operator.lt),
                EventScore.event_id == self.id,
            )
            .subquery()
        )

        scores: list[EventScore] = await db_all(
            select(EventScore, sub).where(sub.c.rank == 1)
        )
        result = []
        for score in scores:
            entry = await score.get_entry()
            result.append(
                EventScore.construct(
                    entry_id=entry.id,
                    rank=score.rank,
                    gain=score.gain,
                    time=score.time,
                    rekt_on=safe_cmp(operator.lt, entry.rekt_on, score.time),
                )
            )
        return Leaderboard(valid=result, unknown=[])

    async def get_leaderboard(
        self, since: Optional[datetime] = None
    ) -> Leaderboard:
        unknown = []
        valid = []

        now = utc_now()

        since, now = self.validate_time_range(since, now)

        for entry in self.entries:
            score = None
            if self.is_(EventState.ACTIVE):
                if entry.init_balance is None:
                    entry.init_balance = await entry.client.get_balance_at_time(
                        entry.joined_at or self.start
                    )

                gain = await entry.client.calc_gain(
                    self,
                    since=entry.init_balance if since == self.start else since,
                    currency=self.currency,
                )
                if gain:
                    if gain.relative < self.rekt_threshold and not entry.rekt_on:
                        entry.rekt_on = now
                    score = EventScore.construct(
                        entry_id=entry.id,
                        rank=1,  # Ranks are evaluated lazy
                        time=now,
                        gain=gain,
                        rekt_on=entry.rekt_on,
                    )

            if score:
                valid.append(score)
            else:
                unknown.append(entry.id)

        await self.async_session.commit()
        valid.sort(reverse=True)

        prev = None
        rank = 1
        for index, score in enumerate(valid):
            if prev is not None and score < prev:
                rank = index + 1
            score.rank = rank
            prev = score

        return Leaderboard.construct(valid=valid, unknown=unknown)

    async def validate_location(self, redis: Redis):
        self.location: "PlatformModel"
        if self.location.name == "discord":
            # Validate
            discord_oauth = self.owner.get_oauth("discord")
            if not discord:
                raise ValueError("No discord account")
            client = rpc.Client("discord", redis)
            guild = await client(
                "guild",
                GuildRequest(
                    user_id=discord_oauth.account_id,
                    guild_id=self.location.data["guild_id"],
                ),
            )
            if all(
                ch["id"] != self.location.data["channel_id"]
                for ch in guild["text_channels"]
            ):
                raise ValueError("Invalid channel")

    @property
    def key(self):
        return join_args(self.__tablename__, self.id)

    @property
    def leaderboard_key(self):
        return join_args(self.key, "leaderboard")

    async def save_leaderboard(self):
        leaderboard = await self.get_leaderboard()

        await self.async_session.run_sync(
            lambda session: session.bulk_insert_mappings(
                get_mapper(EventScore),
                [
                    {
                        "entry_id": int(score.entry_id),
                        "time": score.time,
                        "rank": score.rank,
                        "abs_value": score.gain.absolute,
                        "rel_value": score.gain.relative,
                    }
                    for score in leaderboard.valid
                ],
            )
        )

        await self.async_session.commit()

        return leaderboard

    @hybrid_property
    def guild_id(self):
        return int(self.location.data["guild_id"])

    @guild_id.expression
    def guild_id(self):
        return self.location["data"]["guild_id"].astext.cast(BigInteger)

    @hybrid_property
    def channel_id(self):
        return int(self.location.data["channel_id"])

    @channel_id.expression
    def channel_id(self):
        return self.location["data"]["channel_id"].astext.cast(BigInteger)

    @hybrid_property
    def is_active(self):
        return self.is_(EventState.ACTIVE)

    def is_free_for_registration(self):
        return self.is_(EventState.REGISTRATION)

    @hybrid_property
    def is_full(self):
        return len(self.clients) < self.max_registrations

    def get_discord_embed(
        self, title: str, dc_client: discord.Client, registrations=False
    ):
        embed = discord.Embed(title=title)
        embed.add_field(name="Name", value=self.name)
        embed.add_field(name="Channel", value=f"<#{self.channel_id}>")
        embed.add_field(name="Start", value=self.start, inline=False)
        embed.add_field(name="End", value=self.end)
        embed.add_field(
            name="Registration Start", value=self.registration_start, inline=False
        )
        embed.add_field(name="Registration End", value=self.registration_end)

        if registrations:
            value = "\n".join(
                f"{client.user.discord.get_display_name(dc_client, self.guild_id)}"
                for client in self.all_clients
            )

            embed.add_field(
                name="Registrations",
                value=value if value else "Be the first!",
                inline=False,
            )

            # self._archive.registrations = value

        return embed

    async def finalize(self):
        pass

    async def get_summary(self, date: Optional[datetime] = None):
        if self.is_(EventState.ARCHIVED) and self.final_summary:
            return self.final_summary

        leaderboard = await self.get_leaderboard(date)

        if not leaderboard.valid:
            return None

        gain = Stat.from_sorted(leaderboard.valid)

        def init(score: EventScore):
            entry = self.sync_session.identity_map.get(
                identity_key(EventEntry, score.entry_id)
            )
            return entry.init_balance.realized if entry.init_balance else None

        leaderboard.valid.sort(key=init, reverse=True)
        stakes = Stat.from_sorted(leaderboard.valid)

        async def vola(client: Client):
            history = await get_client_history(client, self.start, self.start, self.end)
            return (
                numpy.array(
                    [
                        balance.unrealized
                        for balance in history.data
                        if balance.unrealized
                    ]
                ).std()
                / history.data[0].unrealized
            )

        client_vola = [(client, await vola(client)) for client in self.all_clients]
        client_vola.sort(key=lambda x: x[1], reverse=True)

        volatili = Stat(
            best=str(client_vola[0][0].id),
            worst=str(client_vola[-1][0].id),
        )

        cum_percent = Decimal(0)
        cum_dollar = Decimal(0)
        for entry in leaderboard.valid:
            cum_percent += entry.gain.relative
            cum_dollar += entry.gain.absolute

        avg_percent = cum_percent / len(self.entries) or 1  # Avoid division by zero

        return Summary(
            gain=gain,
            stakes=stakes,
            volatility=volatili,
            avg_percent=avg_percent,
            total=cum_dollar,
        )

    def __hash__(self):
        return self.id.__hash__()
