import logging
from datetime import datetime

import discord
from discord_slash import cog_ext, SlashContext, SlashCommandOptionType
from discord_slash.utils.manage_commands import create_option
from prettytable import PrettyTable

from bot import config
from bot import utils
from bot.cogs.cogbase import CogBase
from core.utils import date_string, round_ccy
from database import utils as dbutils
from database.calc import calc_daily
from database.dbmodels.client import Client
from database.dbmodels.discord.discorduser import DiscordUser
from database.dbmodels.event import EventState
from typing import Optional


class BalanceCog(CogBase):
    @cog_ext.cog_slash(
        name="balance",
        description="Gives balance of user",
        options=[
            create_option(
                name="user",
                description="User to get balance for",
                required=False,
                option_type=6,
            ),
            create_option(
                name="currency",
                description="Currency to show. Not supported for all exchanges",
                required=False,
                option_type=3,
            ),
        ],
    )
    @utils.log_and_catch_errors()
    @utils.set_author_default(name="user")
    async def balance(
        self,
        ctx: SlashContext,
        user: Optional[discord.Member] = None,
        currency: Optional[str] = None,
    ):
        if currency:
            currency = currency.upper()

        if ctx.guild:
            registered_client = await dbutils.get_discord_client(user.id, ctx.guild.id)

            await ctx.defer()

            usr_balance = await registered_client.get_latest_balance(
                self.redis, currency
            )
            if not usr_balance:
                await ctx.send(
                    f"There are no records about {user.display_name}'s balance"
                )
            else:
                await ctx.send(
                    f"{user.display_name}'s balance: {usr_balance.to_string()}"
                )
        else:
            user: DiscordUser = await dbutils.get_discord_user(
                ctx.author_id,
                eager_loads=[
                    (DiscordUser.clients, Client.events),
                    DiscordUser.global_associations,
                ],
            )

            await ctx.defer()

            for user_client in user.clients:
                usr_balance = await user_client.get_latest_balance(self.redis, currency)
                balance_message = f"Your balance ({user.get_events_and_guilds_string(self.bot, user_client)}): "
                if not usr_balance:
                    await ctx.send("There are no records about your balance")
                else:
                    await ctx.send(
                        embed=discord.Embed(
                            title=balance_message, description=usr_balance.to_string()
                        )
                    )
                    # await ctx.send(f'{balance_message}{usr_balance.to_string()}')

    @cog_ext.cog_slash(
        name="gain",
        description="Calculate gain",
        options=[
            create_option(
                name="user",
                description="User to calculate gain for",
                required=False,
                option_type=6,
            ),
            create_option(
                name="time",
                description="Time frame for gain. Default is start",
                required=False,
                option_type=3,
            ),
            create_option(
                name="currency",
                description="Currency to calculate gain for",
                required=False,
                option_type=3,
            ),
        ],
    )
    @utils.log_and_catch_errors()
    @utils.time_args(("time", None))
    @utils.set_author_default(name="user")
    async def gain(
        self,
        ctx: SlashContext,
        user: discord.Member,
        time: Optional[datetime] = None,
        currency: Optional[str] = None,
    ):
        if currency is None:
            currency = "USD"
        currency = currency.upper()

        since_start = time is None

        await ctx.defer()
        if ctx.guild:
            registered_client = await dbutils.get_discord_client(
                user.id, ctx.guild_id, client_eager=[Client.events]
            )
            clients = [registered_client]

            event = await dbutils.get_discord_event(
                ctx.guild_id, ctx.channel_id, EventState.ACTIVE, throw_exceptions=False
            )

            if event:
                time, to = event.validate_time_range(time)

        else:
            discord_user = await dbutils.get_discord_user(
                ctx.author_id,
                eager_loads=[
                    (DiscordUser.clients, Client.events),
                    DiscordUser.global_associations,
                ],
            )
            clients = discord_user.clients
            event = None

        time_str = utils.readable_time(time)

        for client in clients:
            gain = await client.calc_gain(event, time, currency)
            guild = self.bot.get_guild(ctx.guild_id)
            if ctx.guild:
                gain_message = (
                    f'{user.display_name}\'s gain {"" if since_start else time_str}: '
                )
            else:
                events_n_guild = discord_user.get_events_and_guilds_string(
                    self.bot, client
                )
                gain_message = (
                    f"Your gain ({events_n_guild}): "
                    if not guild
                    else f"Your gain on {guild}: "
                )
            if gain is None:
                logging.info(
                    f"Not enough data for calculating {utils.de_emojify(user.display_name)}'s {time_str} gain on guild {guild}"
                )
                if ctx.guild:
                    await ctx.send(
                        f"Not enough data for calculating {user.display_name}'s {time_str} gain"
                    )
                else:
                    s = discord_user.get_events_and_guilds_string(self.bot, client)
                    await ctx.send(f"Not enough data for calculating your gain ({s})")
            else:
                rounded = round(
                    gain.absolute, ndigits=config.CURRENCY_PRECISION.get(currency, 3)
                )
                await ctx.send(
                    embed=discord.Embed(
                        title=gain_message,
                        description=f"{round(gain.relative, ndigits=3)}% ({rounded}{currency})",
                        colour=discord.Color.green()
                        if gain.relative > 0
                        else discord.Color.red(),
                    )
                )
                return
                await ctx.send(
                    f"{gain_message}{round(gain.relative, ndigits=3)}% ({rounded}{currency})"
                )

    @cog_ext.cog_slash(
        name="daily",
        description="Shows your daily gains.",
        options=[
            create_option(
                name="user",
                description="User to display daily gains for (Author default)",
                option_type=SlashCommandOptionType.USER,
                required=False,
            ),
            create_option(
                name="amount",
                description="Amount of days",
                option_type=SlashCommandOptionType.INTEGER,
                required=False,
            ),
            create_option(
                name="currency",
                description="Currency to use",
                option_type=SlashCommandOptionType.STRING,
                required=False,
            ),
        ],
    )
    @utils.log_and_catch_errors()
    @utils.set_author_default(name="user")
    async def daily(
        self,
        ctx: SlashContext,
        user: discord.Member,
        amount: Optional[int] = None,
        currency: Optional[str] = None,
    ):
        client = await dbutils.get_discord_client(
            user.id, ctx.guild_id, client_eager=(Client.transfers,), registration=True
        )

        currency = currency.upper() if currency else client.currency

        await ctx.defer()

        daily_gains = await calc_daily(client, amount, ctx.guild_id, currency=currency)

        results = PrettyTable(
            field_names=["Date", f"Amount [{currency}]", f"Gain [{currency}]", "Gain %"]
        )

        for interval in daily_gains:
            results.add_row(
                [
                    date_string(interval.start),
                    round_ccy(interval.end_balance.realized, ccy=currency),
                    round_ccy(interval.gain.absolute, ccy=currency),
                    round_ccy(interval.gain.relative, ccy=currency),
                ]
            )

        await ctx.send(
            embed=discord.Embed(
                title=f"Daily gains for {ctx.author.display_name} ({currency})",
                description=f"```\n{results}```",
            )
        )
