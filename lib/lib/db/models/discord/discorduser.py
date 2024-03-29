from __future__ import annotations

from typing import List, TYPE_CHECKING, Optional

import discord
from aioredis import Redis
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, backref

import lib.db.models.client as db_client
from lib.utils import join_args
from lib.db.dbasync import db_select
from lib.db.models.trade import InternalTradeModel
from lib.db.models.user import OAuthAccount
from lib.db.enums import Side
from lib.db.env import ENV
from lib.models.discord.guild import UserRequest
from lib.models.user import ProfileData
from lib.db.redis import rpc

if TYPE_CHECKING:
    from lib.db.models import Client, GuildAssociation, Execution, Balance


class DiscordUser(OAuthAccount):
    __serializer_forbidden__ = ["global_client", "global_associations"]

    global_associations: list[GuildAssociation] = relationship(
        "GuildAssociation",
        lazy="noload",
        cascade="all, delete",
        back_populates="discord_user",
    )

    alerts = relationship(
        "Alert",
        backref=backref("discord_user", lazy="noload"),
        lazy="noload",
        cascade="all, delete",
    )

    clients = relationship(
        "Client", secondary="guild_association", lazy="noload", cascade="all, delete"
    )

    __mapper_args__ = {"polymorphic_identity": "discord"}

    @hybrid_property
    def discord_id(self):
        return int(self.account_id)

    @discord_id.expression
    def discord_id(self):
        return self.account_id.cast(BigInteger)

    def get_display_name(self, dc: discord.Client, guild_id: int):
        try:
            return dc.get_guild(guild_id).get_member(self.discord_id).display_name
        except AttributeError:
            return None

    async def get_guild_client(self, guild_id, *eager, db: AsyncSession):
        association = self.get_guild_association(guild_id)

        if association:
            return await db_select(
                db_client.Client, eager=eager, session=db, id=association.client_id
            )

    def get_guild_association(self, guild_id=None, client_id=None):
        if not guild_id and not client_id:
            if len(self.global_associations) == 1:
                return self.global_associations[0]
        else:
            for association in self.global_associations:
                if association.guild_id == guild_id or (
                    association.client_id == client_id and client_id
                ):
                    return association

    async def get_client_embed(self, dc: discord.Client, client: Client):
        embed = discord.Embed(title="User Information")

        def embed_add_value_safe(name, value, **kwargs):
            if value:
                embed.add_field(name=name, value=value, **kwargs)

        embed_add_value_safe("Events", client.get_event_string())
        embed_add_value_safe(
            "Servers", self.get_guilds_string(dc, client), inline=False
        )
        embed.add_field(name="Exchange", value=client.exchange)
        embed.add_field(name="Api Key", value=client.api_key)

        if client.subaccount:
            embed.add_field(name="Subaccount", value=client.subaccount)
        if client.extra:
            for extra in client.extra:
                embed.add_field(name=extra, value=client.extra[extra])

        initial = await client.initial()
        if initial:
            embed.add_field(name="Initial Balance", value=initial.to_string())

        return embed

    def get_embed(self, fields: Optional[dict] = None, **embed_kwargs):
        embed = discord.Embed(**embed_kwargs)
        if fields:
            for k, v in fields.items():
                embed.add_field(name=k, value=v)
        embed.set_author(
            name=self.data["name"],
            url=ENV.FRONTEND_URL + f"/app/profile?public_id={self.user_id}",
            icon_url=self.data["avatar_url"],
        )
        return embed

    def get_trade_embed(self, trade: InternalTradeModel):
        return self.get_embed(
            title="Trade",
            fields={
                "Symbol": trade.symbol,
                "Net PNL": str(trade.net_pnl) + trade.settle,
                "Entry": trade.entry,
                "Exit": trade.exit,
                "Side": "Long" if trade.side == Side.BUY else "Short",
            },
            color=discord.Color.green() if trade.net_pnl >= 0 else discord.Color.red(),
        )

    def get_exec_embed(self, execution: Execution):
        return self.get_embed(
            title="Execution",
            fields={
                "Symbol": execution.symbol,
                "Realized PNL": execution.realized_pnl,
                "Type": execution.type,
                "Price": execution.price,
                "Size": execution.qty * execution.price,
            },
        )

    def get_balance_embed(self, balance: Balance):
        return self.get_embed(
            title="Balance",
            fields={
                "Total": balance.total,
                "Unrealized": balance.unrealized,
            },
            color=(
                discord.Color.green()
                if balance.unrealized > 0
                else discord.Color.red()
                if balance.unrealized < 0
                else None
            ),
        )

    async def get_discord_embed(self, dc: discord.Client) -> List[discord.Embed]:
        return [await self.get_client_embed(dc, client) for client in self.clients]

    def get_events_and_guilds_string(self, dc: discord.Client, client: Client):
        return join_args(
            self.get_guilds_string(dc, client.id),
            client.get_event_string(),
            denominator=", ",
        )

    def get_guilds_string(self, dc: discord.Client, client_id: int):
        return ", ".join(
            f"_{dc.get_guild(association.guild_id).name}_"
            for association in self.global_associations
            if association.client_id == client_id
        )

    async def populate_oauth_data(self, redis: Redis) -> Optional[ProfileData]:
        client = rpc.Client("discord", redis)
        try:
            self.data = await client("user_info", UserRequest(user_id=self.account_id))
        except rpc.Error:
            pass

        return self.data
