from __future__ import annotations

from datetime import datetime
from operator import and_
from typing import Optional, Type, TypeVar
from typing import TYPE_CHECKING
from uuid import UUID

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import delete, update
from sqlalchemy import select, Column, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

import lib.db.models.event as db_event
import lib.db.models as dbmodels
from lib.db.dbasync import db_all, db_first, time_range
from lib.db.dbasync import db_select, async_session
from lib.db.models.balance import Balance, Amount
from lib.db.models.client import ClientQueryParams
from lib.db.models.client import add_client_checks
from lib.db.models.discord.discorduser import DiscordUser
from lib.db.models.mixins.filtermixin import FilterParam
from lib.db.models.trade import Trade
from lib.db.models.transfer import Transfer
from lib.db.dbsync import BaseMixin
from lib.db.errors import UserInputError
from lib.models.history import History

if TYPE_CHECKING:
    from lib.db.models.event import EventState, PlatformModel


TTable = TypeVar("TTable", bound=BaseMixin)


def run_migrations():
    try:
        path = "../../"
        alembic_cfg = Config(path + "alembic.ini")
        alembic_cfg.set_main_option("script_location", path + "alembic")
        command.upgrade(alembic_cfg, "head")
    except CommandError:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("script_location", "alembic")
        command.upgrade(alembic_cfg, "head")


async def query_table(
    *eager,
    table: Type[TTable],
    user_id: UUID,
    query_params: ClientQueryParams,
    db: AsyncSession,
    stmt: Optional[Select] = None,
    ids: Optional[set[int]] = None,
    filters: Optional[list[FilterParam]] = None,
    time_col: Optional[Column] = None,
    client_col: Optional[Column] = None,
) -> list[TTable]:
    if not time_col:
        time_col = table.time
    if stmt is None:
        stmt = select(table)
    stmt = (
        stmt.where(
            table.id.in_(ids) if ids else True,
            time_range(time_col, query_params.since, query_params.to),
        )
        .join(client_col or table.client)
        .order_by(desc(time_col) if query_params.order == "desc" else asc(time_col))
        .limit(query_params.limit)
    )

    if filters:
        for f in filters:
            stmt = f.apply(stmt, table)

    return await db_all(
        add_client_checks(
            stmt,
            user_id=user_id,
            client_ids=query_params.client_ids,
        ),
        *eager,
        session=db,
    )


async def get_client_history(
    client: dbmodels.Client,
    init_time: Optional[datetime] = None,
    since: Optional[datetime] = None,
    to: Optional[datetime] = None,
    currency: Optional[str] = None,
) -> History:
    initial = None

    stmt = select(Balance).where(
        time_range(Balance.time, since, to), Balance.client_id == client.id
    )
    if currency and currency != client.currency:
        stmt = stmt.join(
            Amount, and_(Amount.balance_id == Balance.id, Amount.currency == currency)
        )
    history = await db_all(stmt, session=client.async_session)
    if init_time and init_time != since:
        initial = await client.get_balance_at_time(init_time)

    if not initial:
        try:
            initial = history[0]
        except (ValueError, IndexError):
            pass

    return History(data=history, initial=initial)


async def reset_client(client_id: int, db: AsyncSession, full=False):
    await db.execute(
        update(dbmodels.Client)
        .values(last_execution_sync=None, last_transfer_sync=None)
        .where(dbmodels.Client.id == client_id)
    )
    if full:
        await db.execute(delete(Trade).where(Trade.client_id == client_id))
        await db.execute(delete(Balance).where(Balance.client_id == client_id))
        await db.execute(delete(Transfer).where(Transfer.client_id == client_id))
    await db.commit()


async def get_discord_client(
    user_id: int,
    guild_id: Optional[int] = None,
    registration=False,
    throw_exceptions=True,
    client_eager=None,
    discord_user_eager=None,
) -> Optional[dbmodels.Client]:
    discord_user_eager = discord_user_eager or []
    client_eager = client_eager or []

    user = await get_discord_user(
        user_id,
        throw_exceptions=throw_exceptions,
        eager_loads=[DiscordUser.global_associations, *discord_user_eager],
    )
    if user:
        if guild_id:
            event = await get_discord_event(
                guild_id,
                state=db_event.EventState.REGISTRATION
                if registration
                else db_event.EventState.ACTIVE,
                throw_exceptions=False,
                eager_loads=[db_event.Event.clients],
            )
            if event:
                for client in event.clients:
                    if client.discord_user_id == user_id:
                        return client
                if throw_exceptions:
                    raise UserInputError(
                        "User {name} is not registered for this event", user_id
                    )

        client = await user.get_guild_client(guild_id, *client_eager, db=async_session)
        if client:
            return client
        elif throw_exceptions:
            raise UserInputError(
                "User {name} does not have a global registration", user_id
            )
    elif throw_exceptions:
        raise UserInputError("User {name} is not registered", user_id)


async def get_event(
    location: PlatformModel | dict,
    state: Optional[EventState] = None,
    throw_exceptions=True,
    eager_loads=None,
    db: Optional[AsyncSession] = None,
) -> Optional[db_event.Event]:
    state = state or db_event.EventState.ACTIVE
    eager_loads = eager_loads or []

    stmt = select(db_event.Event).filter(
        db_event.Event.location == location, db_event.Event.is_expr(state)
    )

    if state == db_event.EventState.ARCHIVED:
        stmt = stmt.order_by(desc(db_event.Event.end)).limit(1)

    event = await db_first(stmt, *eager_loads, session=db)

    if not event and throw_exceptions:
        raise UserInputError(
            f'There is no {"event you can register for" if state == "registration" else "active event"}'
        )
    return event


def get_discord_event(
    guild_id: int,
    channel_id: Optional[int] = None,
    state: Optional[EventState] = None,
    throw_exceptions=True,
    eager_loads=None,
    db: Optional[AsyncSession] = None,
):
    return get_event(
        {
            "platform": "discord",
            "data": {"guild_id": str(guild_id), "channel_id": str(channel_id)},
        },
        state=state,
        throw_exceptions=throw_exceptions,
        eager_loads=eager_loads,
        db=db,
    )


def get_all_events(guild_id: int, channel_id):
    pass


async def get_discord_user(
    user_id: int,
    throw_exceptions=True,
    require_clients=True,
    eager_loads=None,
    db: Optional[AsyncSession] = None,
) -> Optional[DiscordUser]:
    """
    Tries to find a matching entry for the user and guild id.
    :param user_id: id of user to get
    :param guild_id: guild id of user to get
    :param throw_exceptions: whether to throw exceptions if user isn't registered
    :param exact: whether the global entry should be used if the guild isn't registered
    :return:
    The found user. It will never return None if throw_exceptions is True, since an ValueError exception will be thrown instead.
    """
    eager = eager_loads or []
    eager.append(DiscordUser.clients)
    result = await db_select(
        DiscordUser, eager=eager, account_id=str(user_id), session=db
    )

    if not result:
        if throw_exceptions:
            raise UserInputError("User {name} is not registered", user_id)
    elif len(result.clients) == 0 and throw_exceptions and require_clients:
        raise UserInputError("User {name} does not have any registrations", user_id)
    return result
