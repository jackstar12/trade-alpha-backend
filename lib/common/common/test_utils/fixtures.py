import asyncio
import os
from asyncio import Future
from dataclasses import dataclass
from typing import Any, Callable

import aioredis
import pytest
from aioredis import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from common.test_utils.mockexchange import MockExchange, MockTicker
from core import json as customjson
from database.dbmodels import Event, Client, EventEntry, Balance
from database.dbmodels.trade import Trade
from database.dbsync import Base
from common.exchanges import EXCHANGES, EXCHANGE_TICKERS
from common.messenger import Messenger, NameSpaceInput
from database.env import ENV
from database.utils import run_migrations

pytestmark = pytest.mark.anyio

EXCHANGES['mock'] = MockExchange
EXCHANGE_TICKERS['mock'] = MockTicker


@pytest.fixture(scope='session')
def anyio_backend():
    return 'asyncio'


@pytest.fixture(scope='session')
def engine():
    return create_async_engine(
        f'postgresql+asyncpg://{ENV.PG_URL}',
        json_serializer=customjson.dumps_no_bytes,
        json_deserializer=customjson.loads,
    )


@pytest.fixture(scope='session')
async def tables(engine):
    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all)
        run_migrations()
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope='session')
def session_maker(tables, engine):
    return sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope='session')
async def redis() -> Redis:
    redis = aioredis.from_url(ENV.REDIS_URL)
    try:
        yield redis
    finally:
        await redis.close()


@pytest.fixture(scope='session')
async def messenger(redis) -> Messenger:
    messenger = Messenger(redis=redis)
    yield messenger


@pytest.fixture(scope='function')
async def db(tables, engine, session_maker) -> AsyncSession:
    async with session_maker() as db:
        yield db


@dataclass
class Channel:
    ns: NameSpaceInput
    topic: Any
    validate: Callable[[dict], bool] = None


@dataclass
class Messages:
    channels: list[Channel]
    results: dict[str, Future]
    messenger: Messenger

    async def wait(self, timeout: float = 1):
        waiter = asyncio.gather(*self.results.values())
        waiter.cancelled()
        try:
            result = await asyncio.wait_for(waiter, timeout)
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            pytest.fail(f'Missed following messages:\n' + '\n'.join(
                name for name, fut in self.results.items() if fut.cancelled()))

    @classmethod
    def create(cls, *channels: Channel, messenger: Messenger):
        loop = asyncio.get_running_loop()
        return cls(
            channels=list(channels),
            results={
                str(c.ns) + str(c.topic): loop.create_future()
                for c in channels
            },
            messenger=messenger
        )

    async def __aenter__(self):

        async def register_channel(channel: Channel):
            def callback(data):
                if not channel.validate or channel.validate(data):
                    fut = self.results[str(channel.ns) + str(channel.topic)]
                    if not fut.done():
                        fut.set_result(data)

            await self.messenger.sub_channel(
                channel.ns,
                channel.topic,
                callback
            )

        for channel in self.channels:
            await register_channel(channel)
        return self

    async def __aexit__(self, *args):
        for channel in self.channels:
            await self.messenger.unsub_channel(channel.ns, channel.topic)


@pytest.fixture(scope='function')
async def redis_messages(request, messenger):
    async with Messages.create(*request.param, messenger=messenger) as messages:
        yield messages
