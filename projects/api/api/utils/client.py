import asyncio
import dataclasses
import time
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from typing import (
    Optional,
    Dict,
    List,
    Mapping,
    Any,
    Type,
    TypeVar,
    Generic,
    Awaitable,
    Iterable,
)
from uuid import UUID

import pytz
from fastapi import Depends
from fastapi_users.types import DependencyCallable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.client import get_query_params
from lib.db.models.mixins.filtermixin import FilterParam
from api.models.websocket import ClientConfig
from api.users import DefaultGrant
from lib import json as customjson
from lib.db.calc import calc_daily
from lib.db.dbasync import redis, db_all, redis_bulk, RedisKey, db_first
from lib.db.models import TradeDB
from lib.db.models.authgrant import AuthGrant
from lib.db.models.balance import Balance
from lib.db.models.client import Client, add_client_checks, ClientRedis
from lib.db.models.client import ClientQueryParams
from lib.db.models.user import User
from lib.db.dbsync import BaseMixin
from lib.models import BaseModel
from lib.models.document import Operator
from lib.db.redis.client import ClientSpace, ClientCacheKeys
from lib.db.utils import query_table


def ratio(a: float, b: float):
    return round(a / (a + b), ndigits=3) if a + b > 0 else 0.5


def update_dicts(*dicts: Dict, **kwargs):
    for arg in dicts:
        arg.update(kwargs)


def get_dec(mapping: Mapping, key: Any, default: Any):
    return Decimal(mapping.get(key, default))


def update_client_data_trades(
    cache: Dict, trades: List[Dict], config: ClientConfig, save_cache=True
):
    result = {}
    new_trades = {}
    existing_trades = cache.get("trades", None)
    now = datetime.now(tz=pytz.utc)

    winners, losers = get_dec(cache, "winners", 0), get_dec(cache, "losers", 0)
    total_win, total_loss = (
        get_dec(cache, "avg_win", 0) * winners,
        get_dec(cache, "avg_loss", 0) * losers,
    )

    for trade in trades:
        # Get existing entry
        existing_trades.get(trade["id"])
        if trade["status"] == "win":
            winners += 1
            total_win += trade["realized_pnl"]
        elif trade["status"] == "loss":
            losers += 1
            total_loss += trade["realized_pnl"]
        update_dicts(result, cache)
        tr_id = str(trade["id"])
        new_trades[tr_id] = existing_trades[tr_id] = trade

    update_dicts(result, trades=new_trades)

    update_dicts(
        result,
        cache,
        winners=winners,
        losers=losers,
        avg_win=total_win / winners if winners else 1,
        avg_loss=total_loss / losers if losers else 1,
        win_ratio=ratio(winners, losers),
        ts=now.timestamp(),
    )

    if save_cache:
        asyncio.create_task(set_cached_data(cache, config))

    return result


async def update_client_data_balance(
    cache: Dict, client: Client, config: ClientConfig, save_cache=True
) -> Dict:
    cached_date = datetime.fromtimestamp(int(cache.get("ts", 0)), tz=pytz.UTC)
    now = datetime.now(tz=pytz.UTC)

    if config.since:
        since_date = max(config.since, cached_date)
    else:
        since_date = cached_date

    result = {}

    new_history = []

    async def append(balance: Balance):
        new_history.append(
            balance.serialize(full=True, data=True, currency=config.currency)
        )

    daily = await calc_daily(
        client=client,
        throw_exceptions=False,
        since=since_date,
        to=config.to,
        now=now,
        forEach=append,
    )
    result["history"] = new_history
    cache["history"] += new_history

    winning_days, losing_days = cache.get("winning_days", 0), cache.get(
        "losing_days", 0
    )
    for day in daily:
        if day.gain.absolute > 0:
            winning_days += 1
        elif day.gain.absolute < 0:
            losing_days += 1
    result["daily"] = daily

    # When updating daily cache it's important to set the last day to the current day
    daily_cache = cache.get("daily", [])
    if daily:
        if daily_cache and cached_date.weekday() == now.weekday():
            daily_cache[-1] = daily[0]
            daily_cache += daily[1:]
        else:
            daily_cache += daily
    cache["daily"] = daily_cache

    update_dicts(
        result,
        cache,
        daily_win_ratio=ratio(winning_days, losing_days),
        winning_days=winning_days,
        losing_days=losing_days,
        ts=now.timestamp(),
    )

    if save_cache:
        asyncio.create_task(set_cached_data(cache, config))

    return result


async def get_cached_data(config: ClientConfig):
    redis_key = f"client:data:{config.id}:{config.since.timestamp() if config.since else None}:{config.to.timestamp() if config.to else None}:{config.currency}"
    cached = await redis.get(redis_key)
    if cached:
        return customjson.loads(cached)


async def set_cached_data(data: Dict, config: ClientConfig):
    redis_key = f"client:data:{config.id}:{config.since.timestamp() if config.since else None}:{config.to.timestamp() if config.to else None}:{config.currency}"
    await redis.set(redis_key, customjson.dumps(data))


async def create_client_data_serialized(client: Client, config: ClientConfig):
    cached = await get_cached_data(config)
    cached = None
    if cached:
        s = cached
        s["source"] = "cache"
    else:
        s = client.serialize(full=False, data=False)
        s["trades"] = {}
        s["source"] = "database"

    cached_date = datetime.fromtimestamp(int(s.get("ts", 0)), tz=pytz.UTC)

    now = datetime.now(tz=pytz.UTC)

    if config.since:
        since_date = max(config.since, cached_date)
    else:
        since_date = cached_date

    to_date = config.to or now
    since_date = since_date.replace(tzinfo=pytz.UTC)
    to_date = to_date.replace(tzinfo=pytz.UTC)

    if to_date > cached_date:
        since_date = max(since_date, cached_date)

        await update_client_data_balance(s, client, config, save_cache=False)

        trades = [
            trade.serialize(data=True)
            for trade in client.trades
            if since_date <= trade.open_time <= to_date
        ]
        update_client_data_trades(s, trades, config, save_cache=False)

        s["ts"] = now.timestamp()
        asyncio.create_task(set_cached_data(s, config))

    return s


T = TypeVar("T", bound=BaseModel)


def _parse_date(val: bytes) -> datetime:
    ts = float(val)
    return datetime.fromtimestamp(ts, pytz.utc)


@dataclasses.dataclass
class ClientCache(Generic[T]):
    cache_data_key: ClientCacheKeys
    data_model: Type[T]
    query_params: ClientQueryParams
    user_id: UUID
    client_last_exec: dict[int, datetime] = dataclasses.field(
        default_factory=lambda: {}
    )

    async def read(self, db: AsyncSession) -> tuple[list[T], list[int]]:
        pairs = OrderedDict()

        if not self.query_params.client_ids:
            self.query_params.client_ids = await db_all(
                select(Client.id).filter(Client.user_id == self.user_id), session=db
            )

        clients = [
            ClientRedis(self.user_id, client_id)
            for client_id in self.query_params.client_ids
        ]

        for client_id in self.query_params.client_ids:
            client = ClientRedis(self.user_id, client_id)
            # pairs[client.normal_hash] = [
            #    RedisKey(ClientSpace.LAST_EXEC, parse=_parse_date)
            # ]
            pairs[client.cache_hash] = [
                RedisKey(
                    self.cache_data_key,
                    ClientSpace.QUERY_PARAMS,
                    model=self.query_params.__class__,
                )
            ]

        key = RedisKey(
            self.cache_data_key,
            ClientSpace.QUERY_PARAMS,
            model=self.query_params.__class__,
        )
        data = await redis_bulk({client.cache_hash: [key] for client in clients})

        hits = []
        misses = []

        for client in clients:
            cached_query_params: ClientQueryParams = data[client.cache_hash][0]

            if cached_query_params and self.query_params.within(cached_query_params):
                ts1 = time.perf_counter()
                (raw_overview,) = await client.read_cache(
                    RedisKey(self.cache_data_key, model=self.data_model),
                )
                ts2 = time.perf_counter()
                print("Reading Cache", ts2 - ts1)
                if raw_overview:
                    hits.append(raw_overview)
                    continue

            misses.append(client.id)

        return hits, misses

    async def write(self, client_id: int, data: T):
        return await ClientRedis(self.user_id, client_id).redis_set(
            keys={
                RedisKey(self.cache_data_key): data,
                RedisKey(
                    self.cache_data_key, ClientSpace.QUERY_PARAMS
                ): self.query_params,
            },
            space="cache",
        )


def ClientCacheDependency(
    cache_data_key: ClientCacheKeys,
    data_model: Type[BaseModel],
    grant_dependency: Optional[DependencyCallable[AuthGrant]] = None,
    query_params_dep: DependencyCallable[ClientQueryParams] = get_query_params,
):
    def __call__(
        query_params: ClientQueryParams = Depends(query_params_dep),
        grant: AuthGrant = Depends(grant_dependency or DefaultGrant),
    ):
        return ClientCache(
            cache_data_key=cache_data_key,
            data_model=data_model,
            query_params=query_params,
            user_id=grant.user_id,
        )

    return __call__


TTable = TypeVar("TTable", bound=BaseMixin)


def query_trades(
    *eager,
    user_id: UUID,
    query_params: ClientQueryParams,
    trade_ids: Optional[Iterable[int]] = None,
    db: AsyncSession,
    filters: Optional[list[FilterParam]] = None,
):
    if not filters:
        filters = []

    if query_params.currency:
        filters.append(
            FilterParam.construct(
                field="settle", values=[query_params.currency], op=Operator.EQ
            )
        )

    return query_table(
        *eager,
        table=TradeDB,
        time_col=TradeDB.open_time,
        user_id=user_id,
        ids=trade_ids,
        query_params=query_params,
        filters=filters,
        db=db,
    )

    # return db_all(
    #    add_client_filters(
    #        select(TradeDB).filter(
    #            TradeDB.id.in_(trade_id) if trade_id else True,
    #            TradeDB.open_time >= query_params.since if query_params.since else True,
    #            TradeDB.open_time <= query_params.to if query_params.to else True
    #        ).join(
    #            TradeDB.client
    #        ).limit(
    #            query_params.limit
    #        ),
    #        user=user,
    #        client_ids=query_params.client_ids
    #    ),
    #    *eager,
    #    session=db
    # )


def query_balance(
    *eager,
    user: User,
    balance_id: List[int],
    query_params: ClientQueryParams,
    db: AsyncSession,
):
    return query_table(
        *eager,
        table=Balance,
        time_col=Balance.time,
        user_id=user.id,
        ids=balance_id,
        query_params=query_params,
        db=db,
    )
    # return db_all(
    #     add_client_filters(
    #         select(Balance).filter(
    #             Balance.id.in_(balance_id) if balance_id else True,
    #             Balance.time >= query_params.since if query_params.since else True,
    #             Balance.time <= query_params.to if query_params.to else True
    #         ).join(
    #             Balance.client
    #         ).limit(
    #             query_params.limit
    #         ),
    #         user=user,
    #         client_ids=query_params.client_ids
    #     ),
    #     *eager,
    #     session=db
    # )


def get_user_client(
    user: User, client_id: int, *eager, db: Optional[AsyncSession] = None
) -> Awaitable[Optional[Client]]:
    return db_first(
        add_client_checks(select(Client), user.id, {client_id}), *eager, session=db
    )


def get_user_clients(
    user: User,
    ids: Optional[List[int]] = None,
    *eager,
    db: Optional[AsyncSession] = None,
) -> Awaitable[list[Client]]:
    return db_all(add_client_checks(select(Client), user.id, ids), *eager, session=db)
