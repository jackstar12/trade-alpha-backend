from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, and_

from collector.services.baseservice import BaseService
from lib.db.dbasync import db_all, db_select
from lib.db.models.event import Event, EventState
from lib.db.models.evententry import EventEntry
from lib.messenger import Category, TableNames, EventSpace
from lib.models.balance import Balance


@dataclass
class FutureCallback:
    time: datetime
    callback: Callable


class EventService(BaseService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.event_sync = SyncedService(self._messenger,
        #                                 EventSpace,
        #                                 get_stmt=self._get_event,
        #                                 update=self._get_event,
        #                                 cleanup=self._on_event_delete)

    async def init(self):
        self._messenger.listen_class_all(Event)

        for event in await db_all(
            select(Event).where(Event.is_expr(EventState.ACTIVE))
        ):
            self._schedule(event)
            await self._save_event(event.id)

        await self._messenger.bulk_sub(
            Event,
            {
                Category.NEW: self._on_event,
                Category.UPDATE: self._on_event,
                Category.DELETE: self._on_event_delete,
            },
        )

        await self._messenger.bulk_sub(
            TableNames.BALANCE,
            {
                Category.NEW: self._on_balance,
                Category.LIVE: self._on_balance,
            },
        )

        await self._messenger.sub_channel(
            TableNames.TRANSFER, Category.NEW, self._on_transfer
        )

    async def _get_event(self, event_id: int) -> Event:
        return await db_select(
            Event,
            Event.id == event_id,
            eager=[(Event.entries, [EventEntry.client, EventEntry.init_balance])],
            session=self._db,
        )

    async def _on_event(self, data: dict):
        self._schedule(await self._db.get(Event, data["id"]))

    async def _on_transfer(self, data: dict):
        async with self._db_lock:
            await db_all(
                select(EventEntry)
                .where(
                    EventEntry.client_id == data["client_id"], ~Event.allow_transfers
                )
                .join(EventEntry.event),
                session=self._db,
            )

    async def _on_balance(self, data: dict):
        async with self._db_lock:
            await db_all(
                select(EventEntry)
                .where(EventEntry.client_id == data["client_id"])
                .join(
                    Event,
                    and_(
                        Event.id == EventEntry.event_id,
                        Event.is_expr(EventState.ACTIVE),
                    ),
                ),
                EventEntry.init_balance,
                EventEntry.client,
                session=self._db,
            )
            Balance(**data)

    def _on_event_delete(self, data: dict):
        self._unregister(data["id"])

    async def _save_event(self, event_id: int):
        event = await self._get_event(event_id)
        await event.save_leaderboard()
        await self._db.commit()

    def schedule_job(self, event_id: int, run_date: datetime, category: Category):
        job_id = self.job_id(event_id, category)

        if self._scheduler.get_job(job_id):
            self._scheduler.reschedule_job(
                job_id, trigger=DateTrigger(run_date=run_date)
            )
        else:

            async def fn():
                event = await self._get_event(event_id)
                if event:
                    if category in (EventSpace.END, EventSpace.START):
                        await event.save_leaderboard()

                        if category == EventSpace.END:
                            event.final_summary = await event.get_summary()

                        await self._db.commit()
                    return await self._messenger.pub_instance(event, category)

            self._scheduler.add_job(
                func=fn, trigger=DateTrigger(run_date=run_date), id=job_id
            )

    def _schedule(self, event: Event):
        self.schedule_job(event.id, event.start, EventSpace.START)
        self.schedule_job(event.id, event.end, EventSpace.END)
        self.schedule_job(
            event.id, event.registration_start, EventSpace.REGISTRATION_START
        )
        self.schedule_job(event.id, event.registration_end, EventSpace.REGISTRATION_END)

        if not self._scheduler.get_job(f"event:{event.id}"):
            self._scheduler.add_job(
                func=lambda: self._save_event(event.id),
                trigger=IntervalTrigger(days=1),
                id=f"event:{event.id}",
                jitter=60,
            )

    def _unregister(self, event_id: int):
        def remove_job(category: Category):
            self._scheduler.remove_job(self.job_id(event_id, category))

        remove_job(EventSpace.START)
        remove_job(EventSpace.END)
        remove_job(EventSpace.REGISTRATION_START)
        remove_job(EventSpace.REGISTRATION_END)

        self._scheduler.remove_job(f"event:{event_id}")

    @classmethod
    def job_id(cls, event_id: int, category: Category):
        return f"{event_id}:{category}"
