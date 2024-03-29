import asyncio
import logging
import operator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from operator import and_
from typing import List, Tuple, Union, Any, TypeVar, Type, Optional, Callable

import aioredis
from pydantic import ValidationError
from sqlalchemy import delete, select, Column
from sqlalchemy.ext.asyncio import (
    async_scoped_session,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import (
    sessionmaker,
    joinedload,
    selectinload,
    InstrumentedAttribute,
    RelationshipProperty,
)
from sqlalchemy.sql import Select
from sqlalchemy.util import symbol, greenlet_spawn

from lib import json as customjson
from lib.db.dbsync import Base
from lib.db.env import ENV
from lib.models import BaseModel

engine = create_async_engine(
    f"postgresql+asyncpg://{ENV.PG_URL}",
    json_serializer=customjson.dumps_no_bytes,
    json_deserializer=customjson.loads,
    pool_size=20,
    future=True,
)
async_maker = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, future=True,
)
async_maker = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, future=True
)
async_session: AsyncSession = async_scoped_session(
    async_maker, scopefunc=asyncio.current_task
)

redis = aioredis.from_url(ENV.REDIS_URL)


def wrap_greenlet(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        return await greenlet_spawn(fn, *args, **kwargs)

    return wrapper


async def db_exec(stmt: Any, session: Optional[AsyncSession] = None) -> Any:
    return await (session or async_session).execute(stmt)


Table = TypeVar("Table", bound=Base)
StmtCallable = Callable[[Select], Any]


async def db_select(
    cls: Type[Table],
    *where,
    eager=None,
    session: Optional[AsyncSession] = None,
    apply: Optional[StmtCallable] = None,
    **filters,
) -> Optional[Table]:
    stmt = select(cls).where(*where).filter_by(**filters)
    if eager:
        stmt = db_eager(stmt, *eager)
    return await db_first(apply(stmt) if apply else stmt, session=session)


async def db_select_all(
    cls: Type[Table],
    *where,
    eager=None,
    session: Optional[AsyncSession] = None,
    apply: Optional[StmtCallable] = None,
    **filters,
) -> list[Table]:
    stmt = (
        db_eager(select(cls).where(*where).filter_by(**filters), *eager)
        if eager
        else select(cls)
    )
    return await db_all(apply(stmt) if apply else stmt, session=session)


async def db_all(stmt: Select, *eager, session=None) -> list[Any]:
    if eager:
        stmt = db_eager(stmt, *eager)
    return (await (session or async_session).scalars(stmt)).unique().all()


async def db_unique(
    stmt: Select, *eager: object, session: Optional[AsyncSession] = None
) -> Any:
    if eager:
        stmt = db_eager(stmt, *eager)
    return (await (session or async_session).scalars(stmt.limit(1))).unique().first()


async def db_first(stmt: Select, *eager, session: Optional[AsyncSession] = None):
    if eager:
        stmt = db_eager(stmt, *eager)
    return (await (session or async_session).scalars(stmt.limit(1))).first()


async def db_del_filter(cls, session=None, **kwargs):
    return await db_exec(delete(cls).filter_by(**kwargs), session)


def opt_op(col: Any, value: Any, op=operator.eq):
    return op(col, value) if value is not None else True


def opt_eq(col: Any, value: Any):
    return opt_op(col, value, operator.eq)


def time_range(col, since: Optional[datetime] = None, to: Optional[datetime] = None):
    return and_(
        opt_op(col, since, operator.gt),
        opt_op(col, to, operator.lt),
    )


def apply_option(stmt: Select, col: Union[Column, str], root=None, joined=False):
    if isinstance(col.prop, RelationshipProperty):
        joined = col.prop.direction in (symbol("ONETOONE"), symbol("MANYTOONE"))
    if root:
        if joined:
            stmt = stmt.options(root.joinedload(col))
        else:
            stmt = stmt.options(root.selectinload(col))
    else:
        if joined:
            stmt = stmt.options(joinedload(col))
        else:
            stmt = stmt.options(selectinload(col))
    return stmt


TEager = Union[
    Column, Tuple[Column, Union[Tuple, InstrumentedAttribute, List, str]], None
]


def db_eager(stmt: Select, *eager: TEager, root=None, joined=False):
    for col in eager:
        if col:
            if isinstance(col, Tuple):
                if root is None:
                    path = selectinload(col[0])
                else:
                    path = root.selectinload(col[0])
                if isinstance(col[1], list):
                    stmt = db_eager(stmt, *col[1], root=path, joined=joined)
                elif isinstance(col[1], InstrumentedAttribute) or isinstance(
                    col[1], Tuple
                ):
                    stmt = db_eager(stmt, col[1], root=path, joined=joined)
                elif col[1] == "*":
                    stmt = apply_option(stmt, "*", root=path, joined=joined)
            else:
                stmt = apply_option(stmt, col, root=root, joined=joined)
    return stmt


@dataclass
class RedisKey:
    key: str
    model: Optional[Type[BaseModel]]
    parse: Optional[Callable[[bytes], Any]]

    def __init__(
        self,
        *keys,
        model: Optional[Type[BaseModel]] = None,
        parse: Optional[Callable] = None,
        denominator=":",
    ):
        self.key = denominator.join(
            [str(key.value if isinstance(key, Enum) else key) for key in keys if key]
        )
        self.parse = parse
        self.model = model

    def __hash__(self):
        return self.key.__hash__()


async def redis_bulk_keys(h: str, *keys: list[RedisKey], redis_instance=None):
    # if len(keys):
    #    return await (redis_instance or redis).hget(h, keys[0])
    result = await redis_bulk({h: keys}, redis_instance=redis_instance)
    return result[h]


async def redis_bulk_hashes(key: RedisKey, *hashes, redis_instance=None):
    # if len(hashes):
    #     return await (redis_instance or redis).hget(hashes[0], key)
    result = await redis_bulk({h: [key] for h in hashes}, redis_instance=redis_instance)
    return result.values()


async def redis_bulk(hash_keys: dict[str, list[RedisKey]], redis_instance=None):
    async with redis.pipeline(transaction=True) as pipe:
        for h, keys in hash_keys.items():
            for key in keys:
                pipe.hget(h, key.key)
        results = await pipe.execute()
        result = {}
        for h, keys in hash_keys.items():
            result[h] = []
            for key in keys:
                value = results.pop(0)
                if key.model and value:
                    try:
                        res = customjson.loads_bytes(value)
                        value = key.model(**res)
                    except ValidationError as e:
                        logging.error(e)
                        value = None
                if key.parse and value:
                    try:
                        value = key.parse(value)
                    except Exception as e:
                        logging.error(e)
                        value = None
                result[h].append(value)
        return result


if __name__ == "__main__":
    print(asyncio.run(redis.get("test")))
