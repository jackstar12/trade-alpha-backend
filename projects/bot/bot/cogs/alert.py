from decimal import Decimal
from typing import Optional, Dict

from discord_slash import cog_ext, SlashContext, SlashCommandOptionType
from discord_slash.utils.manage_commands import create_option, create_choice

from bot import utils
from bot.cogs.cogbase import CogBase
from lib.db import utils as dbutils
from lib.db.dbasync import async_session
from lib.db.models.alert import Alert
from lib.db.models.discord.discorduser import DiscordUser
from lib.exchanges import EXCHANGES
from lib.models.selectionoption import SelectionOption


class AlertCog(CogBase):
    async def on_alert_trigger(self, data: Dict):
        user_id = data.get("discord_user_id")
        if user_id:
            message = f'Your alert for {data.get("symbol")}@{data.get("price")} just triggered!'
            note = data.get("note")
            if note:
                message += f"\nNote: _{note}_"
            await self.send_dm(user_id, message)

    @cog_ext.cog_subcommand(
        base="alert",
        name="new",
        description="Create New Alert",
        options=[
            create_option(
                name="symbol",
                description="Symbol to create Ticker for.",
                required=True,
                option_type=SlashCommandOptionType.STRING,
            ),
            create_option(
                name="exchange",
                description="Exchange you want to look at",
                required=True,
                option_type=SlashCommandOptionType.STRING,
                choices=[
                    create_choice(name=key, value=key) for key in EXCHANGES.keys()
                ],
            ),
            create_option(
                name="price",
                description="Price to trigger at.",
                required=True,
                option_type=SlashCommandOptionType.FLOAT,
            ),
            create_option(
                name="note",
                description="Additional Note to pass when the alert triggers",
                required=False,
                option_type=SlashCommandOptionType.STRING,
            ),
        ],
    )
    @utils.log_and_catch_errors()
    async def new_alert(
        self,
        ctx: SlashContext,
        symbol: str,
        price: float,
        exchange: str,
        note: Optional[str] = None,
    ):
        symbol = symbol.upper()

        discord_user = await dbutils.get_discord_user(
            ctx.author_id,
            require_clients=False,
            eager_loads=[
                DiscordUser.alerts,
            ],
        )

        alert = Alert(
            symbol=symbol,
            price=Decimal(price),
            note=note,
            exchange=exchange,
            discord_user_id=discord_user.id,
        )

        async_session.add(alert)
        await async_session.commit()

        await ctx.send("Alert created", embed=alert.get_discord_embed())

    @cog_ext.cog_subcommand(
        base="alert",
        name="delete",
        description="Delete an Alarm",
        options=[
            create_option(
                name="symbol",
                description="Symbol to delete",
                option_type=SlashCommandOptionType.STRING,
                required=False,
            )
        ],
    )
    @utils.log_and_catch_errors()
    async def delete_alert(self, ctx: SlashContext, symbol: Optional[str] = None):
        user = await dbutils.get_discord_user(
            ctx.author_id, require_clients=False, eager_loads=[DiscordUser.alerts]
        )

        if user.alerts:
            ctx, selections = await utils.new_create_selection(
                ctx,
                self.slash_cmd_handler,
                options=[
                    SelectionOption(
                        name=f"{alert.symbol}@{alert.price}",
                        value=str(alert.channel_id),
                        description=alert.note,
                        object=alert,
                    )
                    for alert in user.alerts
                    if alert.symbol == symbol or not symbol
                ],
                msg_content="Select the alert you want to delete",
                max_values=len(user.alerts),
            )
            for selection in selections:
                await async_session.delete(selection)
            await async_session.commit()
            await ctx.send("Success")
        else:
            await ctx.send("You do not have any active alerts")

    @cog_ext.cog_subcommand(
        base="alert",
        name="show",
        description="Show active Alerts",
        options=[
            create_option(
                name="symbol",
                description="Symbol to show alerts for",
                required=False,
                option_type=SlashCommandOptionType.STRING,
            )
        ],
    )
    @utils.log_and_catch_errors()
    async def show_alerts(self, ctx: SlashContext, symbol: Optional[str] = None):
        if symbol:
            symbol = symbol.upper()

        user = await dbutils.get_discord_user(
            ctx.author_id, require_clients=False, eager_loads=[DiscordUser.alerts]
        )

        embeds = [
            alert.get_discord_embed()
            for alert in user.alerts
            if alert.symbol == symbol or not symbol
        ]

        if len(embeds) > 0:
            await ctx.send(embeds=embeds)
        else:
            await ctx.send(
                f'You do not have any alerts active{f" for symbol {symbol}" if symbol else ""}'
            )
