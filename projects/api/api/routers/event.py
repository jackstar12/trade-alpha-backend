from datetime import datetime
from typing import Optional

import pydantic
import pytz
import sqlalchemy.exc
from fastapi import APIRouter, Depends
from fastapi.params import Query
from pydantic import validator
from sqlalchemy import select, delete, asc
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.users import CurrentUser, get_current_user, get_auth_grant_dependency, DefaultGrant, get_table_auth_dependency
from api.utils.responses import BadRequest, OK, ResponseModel, NotFound
from core.utils import groupby
from database import utils as dbutils
from database.dbasync import db_first, redis, db_unique, wrap_greenlet, db_all
from database.dbmodels import Client
from database.dbmodels.authgrant import AuthGrant, EventGrant, AssociationType
from database.dbmodels.client import add_client_checks
from database.dbmodels.event import Event as EventDB, EventState
from database.dbmodels.evententry import EventEntry as EventEntryDB, EventScore as EventScoreDB
from database.dbmodels.user import User
from database.models import BaseModel, InputID, OutputID, OrmBaseModel
from database.models.document import DocumentModel
from database.models.eventinfo import EventInfo, EventDetailed, EventCreate, Leaderboard, Summary, \
    EventEntry, EventEntryDetailed, EventScore

router = APIRouter(
    tags=["event"],
    responses={
        401: {'detail': 'Wrong Email or Password'},
        400: {'detail': "Email is already used"}
    },
    prefix='/event'
)


def event_dep(*eager, require_user=False):

    event_loads = [EventDB.clients, *eager]

    async def dependency(event_id: InputID,
                         assoc: EventGrant = Depends(
                             get_table_auth_dependency(
                                 (EventGrant.event, event_loads),
                                 association_table=EventGrant
                             )
                         ),
                         db: AsyncSession = Depends(get_db)) -> EventDB:
        if not assoc.event:
            if assoc.grant.is_root_for(AssociationType.EVENT):
                stmt = select(EventDB).filter(
                    EventDB.id == event_id,
                    EventDB.owner_id == assoc.grant.user_id
                )
                assoc.event = await db_first(stmt, *event_loads, session=db)
        if not assoc.event:
            raise BadRequest('Invalid event id')
        return assoc

    return dependency


@router.post('', response_model=ResponseModel[EventInfo])
async def create_event(body: EventCreate,
                       user: User = Depends(CurrentUser),
                       db: AsyncSession = Depends(get_db)):
    event = body.get(user)

    try:
        event.validate()
        await event.validate_location(redis)
    except ValueError as e:
        raise BadRequest(str(e))

    if event.location.name != 'web':
        active_event = await dbutils.get_event(location=body.location,
                                               throw_exceptions=False,
                                               db=db)

        if active_event:
            if body.start < active_event.end:
                raise BadRequest(f"Event can't start while other event ({active_event.name}) is still active")
            if body.registration_start < active_event.registration_end:
                raise BadRequest(
                    f"Event registration can't start while other event ({active_event.name}) is still open for registration")

        active_registration = await dbutils.get_event(location=body.location, state=EventState.REGISTRATION,
                                                      throw_exceptions=False,
                                                      db=db)

        if active_registration:
            if body.registration_start < active_registration.registration_end:
                raise BadRequest(
                    f"Event registration can't start while other event ({active_registration.name}) is open for registration")

    db.add(event)
    await db.commit()

    return OK(
        result=EventInfo.from_orm_with(event, extra={'grants': []})
    )


EventUserDep = get_current_user(
    (User.events, EventDB.actions)
)


@router.get('', response_model=ResponseModel[list[EventInfo]])
@wrap_greenlet
def get_events(grant: AuthGrant = Depends(DefaultGrant)):
    return OK(
        result=[
            EventInfo.from_orm_with(
                event,
                extra={'grants': event.grants if grant.is_root_for(AssociationType.EVENT) else [grant]}
            )
            for event in grant.events
        ]
    )


@router.get('/{event_id}', response_model=ResponseModel[EventDetailed])
@wrap_greenlet
def get_event(grant: EventGrant = Depends(event_dep(EventDB.owner,
                                                    (EventDB.entries, [
                                                        EventEntryDB.client,
                                                        EventEntryDB.user,
                                                        EventEntryDB.init_balance
                                                    ])))):
    return OK(
        result=EventDetailed.from_orm_with(grant.event, extra={'grant': grant})
    )


SummaryEvent = event_dep(
    (EventDB.entries,
     [
         EventEntryDB.client,
         EventEntryDB.init_balance
     ])
)


@router.get('/{event_id}/leaderboard', response_model=ResponseModel[Leaderboard])
async def get_event_leaderboard(date: datetime = None,
                                grant: EventGrant = Depends(SummaryEvent)):
    #leaderboard = await grant.event.get_leaderboard(date)
    leaderboard = await grant.event.save_leaderboard()

    return OK(result=leaderboard)


EventAuth = get_auth_grant_dependency(EventGrant)


@router.get('/{event_id}/summary', response_model=ResponseModel[Summary])
async def get_summary(grant: EventGrant = Depends(SummaryEvent),
                      date: datetime = None):
    summary = await grant.event.get_summary(date)
    return OK(result=summary)


class EntryHistoryResponse(OrmBaseModel):
    by_entry: dict[OutputID, list[EventScore]]


@router.get('/{event_id}/registrations/history',
            response_model=ResponseModel[EntryHistoryResponse],
            dependencies=[Depends(EventAuth)])
async def get_event_entry_history(event_id: InputID,
                                  entry_ids: list[InputID] = Query(alias='entry_id'),
                                  db: AsyncSession = Depends(get_db)):
    scores = await db_all(
        select(EventScoreDB).where(
            EventScoreDB.entry_id.in_(entry_ids),
            EventEntryDB.event_id == event_id
        ).join(
            EventScoreDB.entry
        ).order_by(
            asc(EventScoreDB.time)
        ),
        session=db
    )

    grouped = groupby(scores or [], 'entry_id')

    return OK(result=EntryHistoryResponse(by_entry=grouped))


@router.get('/{event_id}/registrations/{entry_id}',
            response_model=ResponseModel[EventEntryDetailed],
            dependencies=[Depends(EventAuth)])
async def get_event_entry(event_id: int,
                          entry_id: int,
                          db: AsyncSession = Depends(get_db)):
    score = await db_unique(
        select(EventEntryDB).filter_by(
            id=entry_id,
            event_id=event_id
        ),
        EventEntryDB.rank_history,
        session=db
    )

    if score:
        return OK(result=EventEntryDetailed.from_orm(score))
    else:
        raise BadRequest('Invalid event or entry id. You might miss authorization')


class EventUpdate(BaseModel):
    name: Optional[str]
    description: Optional[DocumentModel]
    start: Optional[datetime]
    end: Optional[datetime]
    registration_start: Optional[datetime]
    registration_end: Optional[datetime]
    public: Optional[bool]
    max_registrations: Optional[int]

    @validator("start", "registration_start", "end", "registration_end")
    def cant_update_past(cls, v):
        now = datetime.now(pytz.utc)
        if v and v < now:
            raise ValueError(f'Can not udpate date from the past')
        return v


@router.patch('/{event_id}', response_model=ResponseModel[EventInfo])
async def update_event(body: EventUpdate,
                       grant: EventGrant = Depends(event_dep(EventDB.grants, require_user=True)),
                       db: AsyncSession = Depends(get_db)):
    event = grant.event
    # dark magic
    for key, value in body.dict(exclude_none=True).items():
        setattr(event, key, value)
    try:
        event.validate()
    except ValueError as e:
        await db.rollback()
        raise BadRequest(str(e))

    await db.commit()

    return OK('Event Updated', result=EventInfo.from_orm(event))


@router.delete('/{event_id}')
async def delete_event(grant: EventGrant = Depends(event_dep(EventDB.actions, require_user=True)),
                       db: AsyncSession = Depends(get_db)):
    if grant.event:
        await db.delete(grant.event)
        await db.commit()
        return OK('Deleted')
    else:
        raise BadRequest('You can not delete this event')


class EventJoinBody(BaseModel):
    client_id: InputID


@router.post('/{event_id}/registrations', response_model=EventEntry)
async def join_event(body: EventJoinBody,
                     grant: EventGrant = Depends(event_dep(require_user=True)),
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(CurrentUser)):
    client_id = await db_first(
        add_client_checks(
            select(Client.id), user_id=user.id, client_ids=[body.client_id]
        ),
        session=db
    )
    if not grant.event.is_(EventState.REGISTRATION):
        raise BadRequest('Registration is already over')
    if client_id:
        try:
            entry = EventEntryDB(
                event_id=grant.event.id,
                client_id=client_id,
                user=user
            )
            db.add(entry)
            await db.commit()
            return EventEntry.from_orm(entry)
        except sqlalchemy.exc.IntegrityError:
            raise BadRequest('Already signed up')
    else:
        raise BadRequest('Invalid client ID')


@router.delete('/{event_id}/registrations/{entry_id}')
async def unregister_event(event_id: int,
                           entry_id: int = None,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(CurrentUser)):
    stmt = delete(EventEntryDB).filter_by(event_id=event_id, user_id=user.id)
    result = await db.execute(
        stmt.filter_by(id=entry_id) if entry_id else stmt
    )
    await db.commit()
    if result.rowcount == 1:
        return OK('Unregistered form the Event')
    else:
        raise NotFound('Invalid entry id')
