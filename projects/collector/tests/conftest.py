import asyncio
from functools import wraps

import aiohttp
import pytest
import requests
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete

from collector.services.balanceservice import (
    ExtendedBalanceService,
    BasicBalanceService,
)
from collector.services.dataservice import DataService
from collector.services.eventservice import EventService
from common.test_utils.fixtures import *
from database.dbasync import db_select
from database.dbmodels.client import Client
from database.dbmodels.user import User
from common.exchanges import CCXT_CLIENTS
from common.messenger import TableNames, Category
from core.utils import setup_logger


pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def session():
    with requests.Session() as session:
        yield session


def run_service(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with func(*args, **kwargs) as service:
            await service.init()
            task = asyncio.create_task(service.run_forever())
            yield service
            task.cancel()

    return wrapper


@pytest.fixture(scope="session")
async def http_session():
    setup_logger(debug=True)

    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture(scope="session")
async def service_args(redis, session_maker, http_session):
    scheduler = AsyncIOScheduler(executors={"default": AsyncIOExecutor()})
    scheduler.start()
    return http_session, redis, scheduler, session_maker


@pytest.fixture(scope="session")
@run_service
def data_service(service_args):
    return DataService(*service_args)


@pytest.fixture(scope="session")
@run_service
def pnl_service(data_service, service_args):
    return ExtendedBalanceService(*service_args, data_service=data_service)


@pytest.fixture(scope="session")
@run_service
def balance_service(data_service, service_args):
    return BasicBalanceService(*service_args, data_service=data_service)


@pytest.fixture(scope="session")
@run_service
def event_service(data_service, service_args):
    return EventService(*service_args)


@pytest.fixture
async def test_user(db):
    user = await db_select(User, eager=[], session=db, email=User.mock().email)
    if not user:
        user = User.mock()
        db.add(user)
        await db.commit()

    yield user
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()


@pytest.fixture
async def client(request, test_user, time) -> Client:
    request.param.import_since = time
    client: Client = request.param.get(test_user)
    yield client


@pytest.fixture
async def registered_client(
    pnl_service, client, time, db, test_user, messenger
) -> Client:
    async with Messages.create(
        Channel(
            TableNames.CLIENT,
            Category.UPDATE,
            validate=lambda data: data["state"] == "synchronizing",
        ),
        Channel(
            TableNames.CLIENT,
            Category.UPDATE,
            validate=lambda data: data["state"] == "ok",
        ),
        Channel(TableNames.CLIENT, Category.ADDED),
        messenger=messenger,
    ) as listener:
        await db.execute(
            delete(Client).where(
                Client.api_key == client.api_key, Client.exchange == client.exchange
            )
        )
        db.add(client)
        await db.commit()
        await listener.wait(10)

    try:
        yield client
    finally:
        async with Messages.create(
            Channel(TableNames.CLIENT, Category.REMOVED), messenger=messenger
        ) as listener:
            await db.delete(client)
            await db.commit()
            await listener.wait(1)


@pytest.fixture
def ccxt_client(client, session):
    ccxt_class = CCXT_CLIENTS[client.exchange]
    ccxt = ccxt_class(
        {
            "api_key": client.api_key,
            "secret": client.api_secret,
            "session": session,
            **(client.extra or {}),
        }
    )

    ccxt.set_sandbox_mode(client.sandbox)

    return ccxt
