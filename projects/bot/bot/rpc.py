import logging

import discord
import discord.errors
from fastapi.encoders import jsonable_encoder

from lib.env import ENV
from lib.db.dbasync import redis, db_select
from lib.db.models.discord.discorduser import DiscordUser
from lib.models.discord.guild import (
    UserRequest,
    GuildRequest,
    GuildData,
    MessageRequest,
)
from lib.models.user import ProfileData
from lib.db.redis.rpc import Server
from typing import Optional


def create_rpc_server(bot):
    server = Server("discord", redis)

    @server.method(input_model=UserRequest)
    def user_info(request: UserRequest):
        test = bot.get_user(request.user_id)

        return jsonable_encoder(
            ProfileData(avatar_url=str(test.avatar_url), name=test.name)
        )

    @server.method(input_model=UserRequest)
    def guilds(request: UserRequest):
        res = [
            GuildData.from_member(data, request.user_id)
            for data in bot.get_user(request.user_id).mutual_guilds
        ]
        return jsonable_encoder(res)

    async def send_dm(
        self, user_id: int, message: str, embed: Optional[discord.Embed] = None
    ):
        user: discord.User = self.bot.get_user(user_id)
        if user:
            try:
                await user.send(content=message, embed=embed)
            except discord.Forbidden:
                logging.exception(f"Not allowed to send messages to {user}")

    @server.method(input_model=GuildRequest)
    def guild(request: GuildRequest):
        data: discord.Guild = bot.get_guild(request.guild_id)
        return jsonable_encoder(GuildData.from_member(data, request.user_id))

    def _get_channel(guild_id: int, channel_id: int) -> discord.TextChannel:
        guild = bot.get_guild(guild_id)
        if guild:
            return guild.get_channel(channel_id)

    @server.method(input_model=MessageRequest)
    async def send(request: MessageRequest):
        channel = _get_channel(request.guild_id, request.channel_id)
        embed = None
        if request.embed:
            if request.embed.type:
                pass
                # TODO: keep?
            else:
                embed = discord.Embed.from_dict(request.embed.raw)
            if request.embed.author_id:
                discord_user = await db_select(
                    DiscordUser, DiscordUser.user_id == request.embed.author_id
                )
                account_id = int(discord_user.account_id)
                if request.guild_id:
                    member = bot.get_guild(request.guild_id).get_member(account_id)
                    name = member.nick or member.display_name
                else:
                    name = discord_user.data["name"]
                embed.set_author(
                    name=name,
                    url=ENV.FRONTEND_URL + f"/app/profile?public_id={account_id}",
                    icon_url=discord_user.data["avatar_url"],
                )

        await channel.send(content=request.message, embed=embed)

        return True

    return server
