from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select, and_

from collector.services.baseservice import BaseService
from database.dbasync import db_all
from database.dbmodels.event import Event, EventState
from database.dbmodels.score import EventScore
from common.messenger import Category, TableNames, EVENT
from database.models.balance import Balance


@dataclass
class FutureCallback:
    time: datetime
    callback: Callable


class EventService(BaseService):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.event_sync = SyncedService(self._messenger,
        #                                 EVENT,
        #                                 get_stmt=self._get_event,
        #                                 update=self._get_event,
        #                                 cleanup=self._on_event_delete)

    async def init(self):
        for event in await db_all(
            select(Event).where(Event.is_expr(EventState.ACTIVE))
        ):
            self._schedule(event)

        await self._messenger.bulk_sub(
            TableNames.EVENT, {
                Category.NEW: self._on_event,
                Category.UPDATE: self._on_event,
                Category.DELETE: self._on_event_delete
            }
        )

        await self._messenger.bulk_sub(
            TableNames.BALANCE, {
                Category.NEW: self._on_balance,
                Category.LIVE: self._on_balance,
            }
        )

        await self._messenger.sub_channel(TableNames.TRANSFER, Category.NEW, self._on_transfer)
        await self._messenger.sub_channel(TableNames.EVENT, EVENT.START, self._on_event_start)

    async def _on_event(self, data: dict):
        self._schedule(
            await self._db.get(Event, data['id'])
        )

    async def _on_transfer(self, data: dict):
        async with self._db_lock:
            event_entries = await db_all(
                select(EventScore).where(
                    EventScore.client_id == data['client_id'],
                    ~Event.allow_transfers
                ).join(EventScore.event),
                session=self._db
            )

    async def _on_balance(self, data: dict):
        async with self._db_lock:
            scores: list[EventScore] = await db_all(
                select(EventScore).where(
                    EventScore.client_id == data['client_id']
                ).join(Event, and_(
                    Event.id == EventScore.event_id,
                    Event.is_expr(EventState.ACTIVE)
                )),
                EventScore.init_balance,
                EventScore.client,
                session=self._db
            )
            balance = Balance(**data)

            for score in scores:
                event: Event = await self._db.get(Event, score.event_id)

                if score.init_balance is None:
                    score.init_balance = await score.client.get_balance_at_time(
                        event.start,
                        db=self._db
                    )

                offset = await score.client.get_total_transfered(
                    db=self._db, since=event.start, to=event.end
                )
                score.update(balance, offset)
                if score.rel_value < event.rekt_threshold:
                    score.rekt_on = balance.time
                    await self._messenger.pub_instance(score, Category.REKT)
                await self._db.flush()
                await Event.save_leaderboard(event.id, self._db)
            await self._db.commit()

    async def _on_event_end(self, event_id: int):
        scores: list[EventScore] = await db_all(
            select(EventScore).filter(
                EventScore.event_id == event_id
            ),
            EventScore.init_balance,
            EventScore.client,
            session=self._db
        )

        event: Event = await self._db.get(Event, event_id)
        for score in scores:
            if score.init_balance is None:
                score.init_balance = await score.client.get_balance_at_time(
                    event.start,
                    db=self._db
                )
            balance = await score.client.get_balance_at_time(event.end, db=self._db)

            offset = await score.client.get_total_transfered(
                db=self._db, since=event.start, to=event.end
            )
            score.update(balance.get_currency(), offset)
        await Event.save_leaderboard(event.id, self._db)
        await self._db.commit()

    async def _on_event_start(self, event_id: int):
        await Event.save_leaderboard(event_id, self._db)
        await self._db.commit()

    def _on_event_delete(self, data: dict):
        self._unregister(data['id'])

    def _schedule(self, event: Event):

        def schedule_job(run_date: datetime, category: Category):
            job_id = self.job_id(event.id, category)

            if self._scheduler.get_job(job_id):
                self._scheduler.reschedule_job(
                    job_id,
                    trigger=DateTrigger(run_date=run_date)
                )
            else:
                async def fn():
                    if category == EVENT.END:
                        await self._on_event_end(event.id)
                    elif category == EVENT.START:
                        await self._on_event_start(event.id)
                    return await self._messenger.pub_instance(event, category)

                self._scheduler.add_job(
                    func=fn,
                    trigger=DateTrigger(run_date=run_date),
                    id=job_id
                )

        schedule_job(event.start, EVENT.START)
        schedule_job(event.end, EVENT.END)
        schedule_job(event.registration_start, EVENT.REGISTRATION_START)
        schedule_job(event.registration_end, EVENT.REGISTRATION_END)

    def _unregister(self, event_id: int):

        def remove_job(category: Category):
            self._scheduler.remove_job(
                self.job_id(event_id, category)
            )

        remove_job(EVENT.START)
        remove_job(EVENT.END)
        remove_job(EVENT.REGISTRATION_START)
        remove_job(EVENT.REGISTRATION_END)

    @classmethod
    def job_id(cls, event_id: int, category: Category):
        return f'{event_id}:{category}'
