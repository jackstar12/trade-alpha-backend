from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Any

from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from collector.services.baseservice import BaseService
from lib.messenger import Category
from lib.utils import utc_now
from lib.env import ENV
from lib.db.dbasync import db_all, db_select, db_unique, opt_eq
from lib.db.models.execution import Execution
from lib.db.models.editing import Chapter
from lib.db.models.action import Action, ActionTrigger
from lib.db.models.authgrant import ChapterGrant, TradeGrant
from lib.db.models.balance import Balance
from lib.db.models.discord.discorduser import DiscordUser
from lib.db.models.trade import Trade
from lib.models.discord.guild import MessageRequest
from lib.db.redis import rpc


@dataclass
class FutureCallback:
    time: datetime
    callback: Callable


class ActionService(BaseService):
    def get_action(self, data: dict):
        return db_select(Action, Action.id == data["id"])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.action_sync = SyncedService(self._messenger,
        #                                 EVENT,
        #                                 get_stmt=self._get_event,
        #                                 update=self._get_event,
        #                                 cleanup=self._on_event_delete)

    async def update(self, action: Action):
        await self.remove(action)
        await self.add(action)

    def add(self, action: Action):
        return self._messenger.sub_channel(
            action.type,
            action.topic,
            lambda data: self.execute(action, data),
            **action.all_ids,
        )

    def remove(self, action: Action):
        return self._messenger.unsub_channel(
            action.type, action.topic, **action.all_ids
        )

    async def on_action(self, action: Action, data: Any):
        if action.delay:
            self._scheduler.add_job(
                func=self.execute,
                trigger=DateTrigger(utc_now() + action.delay),
                args=(action, data),
            )
        else:
            await self.execute(action, data)

    async def execute(self, action: Action, data: Any):
        ns = self._messenger.get_namespace(action.type)
        self._logger.info(f"Executing action {action.id}")
        if action.platform.name == "webhook":
            action.platform.data["url"]
            # TODO
        elif action.platform.name == "discord":
            dc = rpc.Client("discord", self._redis)

            discord_user = await db_select(
                DiscordUser, DiscordUser.user_id == action.user_id, session=self._db
            )

            if ns.table.__model__:
                instance = ns.table.__model__(**data)
            else:
                instance = ns.table(**data)
            if ns.table == Balance:
                embed = discord_user.get_balance_embed(instance)
            elif ns.table == Trade:
                embed = discord_user.get_trade_embed(instance)
            elif ns.table == Execution:
                embed = discord_user.get_exec_embed(instance)
            elif ns.table == ChapterGrant:
                info = await db_unique(
                    select(Chapter.id, Chapter.journal_id, Chapter.title).where(
                        Chapter.id == data["chapter_id"],
                        opt_eq(
                            Chapter.journal_id, action.trigger_ids.get("journal_id")
                        ),
                    ),
                    session=self._db,
                )
                embed = discord_user.get_embed(
                    title=info.title,
                    description=action.message,
                    url=ENV.FRONTEND_URL
                    + f"/app/profile/journal/{info.journal_id}/chapter/{info.id}",
                )
            elif ns.table == TradeGrant:
                trade = await self._db.get(Trade, data["trade_id"])
                embed = discord_user.get_trade_embed(trade)
                embed.description = action.message
                embed.url = ENV.FRONTEND_URL + f"/app/profile/trade/{trade.id}"
            else:
                embed = discord_user.get_embed(title=ns.table.__name__, fields=data)

            try:
                await dc.call(
                    "send",
                    MessageRequest(
                        **action.platform.data,
                        embed={
                            "raw": embed.to_dict(),
                        },
                    ),
                )
            except Exception as e:
                self._logger.error(f"Error sending discord message: {e}")

        if action.trigger_type == ActionTrigger.ONCE:
            await self.remove(action)
            await self._db.delete(action)

    async def init(self):
        for action in await db_all(select(Action)):
            await self.add(action)

        wrap = self.table_decorator(Action)

        await self._messenger.bulk_sub(
            Action,
            {
                Category.NEW: wrap(self.add),
                Category.UPDATE: wrap(self.update),
                Category.DELETE: wrap(self.remove),
            },
        )
        # await self.action_sync.sub()
