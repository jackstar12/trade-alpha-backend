import asyncio
import itertools
from asyncio import Future
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from common.exchanges.exchangeworker import ExchangeWorker
from common.test_utils.mockexchange import MockExchange, RawExec
from core import utc_now
from database.dbmodels import Client, Execution
from database.dbasync import db_select_all, db_all, db_unique, db_select
from database.dbmodels.client import ClientState
from database.dbmodels.trade import Trade
from common.messenger import TableNames, Category
from common.test_utils.fixtures import Channel, Messages
from common.exchanges import SANDBOX_CLIENTS, EXCHANGES
from database.enums import Side
from database.models.client import ClientCreate

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='session')
def time():
    return datetime.now()


symbol = 'BTCUSDT'
size = Decimal('0.01')


@pytest.mark.parametrize(
    'db_client',
    [MockExchange.create()],
    indirect=True
)
async def test_realtime(db, pnl_service, time, db_client, session_maker, messenger, redis):
    db_client: Client

    first_balance = await db_client.get_latest_balance(redis)

    async def get_trades() -> list[Trade]:
        async with session_maker() as db:
            return await db_all(
                select(Trade).where(
                    Trade.client_id == db_client.id,
                    Trade.symbol == symbol,
                ).order_by(Trade.open_time),
                Trade.min_pnl, Trade.max_pnl, Trade.pnl_data,
                session=db
            )

    async def get_trade():
        trades = await get_trades()
        assert len(trades) == 1
        return trades[0]

    async with Messages.create(
            Channel(TableNames.TRADE, Category.NEW),
            messenger=messenger
    ) as listener:
        await MockExchange.put_exec(symbol=symbol, side=Side.BUY, qty=size / 2, price=7500)
        await listener.wait(2)

    await asyncio.sleep(.2)
    trade = await get_trade()
    assert trade.qty == size / 2

    async with Messages.create(
            Channel(TableNames.TRADE, Category.UPDATE),
            messenger=messenger
    ) as listener:
        await MockExchange.put_exec(symbol=symbol, side=Side.BUY, qty=size / 2, price=12500)
        await listener.wait(3)

    await asyncio.sleep(.2)
    trade = await get_trade()
    assert trade.entry == 10000
    assert trade.qty == size

    async with Messages.create(
            Channel(TableNames.BALANCE, Category.LIVE),
            Channel(TableNames.TRADE, Category.UPDATE),
            messenger=messenger
    ) as listener:
        await MockExchange.put_exec(symbol=symbol, side=Side.SELL, qty=size / 2, price=17500)
        await listener.wait(3)

    await asyncio.sleep(.2)
    trade = await get_trade()
    assert trade.open_qty == size / 2
    assert trade.qty == size
    assert trade.max_pnl.total != trade.min_pnl.total
    assert trade.exit == 17500

    second_balance = await db_client.get_latest_balance(redis)
    assert first_balance.unrealized != second_balance.unrealized

    async with Messages.create(
            Channel(TableNames.TRADE, Category.FINISHED),
            Channel(TableNames.TRADE, Category.NEW),
            messenger=messenger
    ) as listener:
        await MockExchange.put_exec(symbol=symbol, side=Side.SELL, qty=size, price=22500)
        await listener.wait(1)

    await asyncio.sleep(.2)

    trades = await get_trades()
    assert len(trades) == 2
    finished = trades[0]
    new = trades[1]
    assert not finished.is_open
    assert finished.close_time != finished.open_time
    assert new.is_open
    assert new.qty == size / 2


@pytest.mark.parametrize(
    'db_client',
    SANDBOX_CLIENTS,
    indirect=True
)
async def test_exchange(db_client, pnl_service, db, session_maker, http_session, ccxt_client, messenger, redis):
    db_client: Client

    now = utc_now()

    async with Messages.create(Channel(TableNames.TRADE, Category.NEW), messenger=messenger) as listener:
        ccxt_client.create_market_buy_order(symbol, float(size))
        ccxt_client.create_market_sell_order(symbol, float(size))
        await listener.wait(5)

    worker = pnl_service.get_worker(db_client.id)

    _, execs, _ = await worker.get_executions(now)

    assert len(execs) == 2


@pytest.fixture
def mock_execs():
    raw = [
        RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
        RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
        RawExec(symbol=symbol, side=Side.SELL, qty=size, price=20000),
    ]
    MockExchange.queue = asyncio.Queue()
    for exec in raw:
        MockExchange.queue.put_nowait(exec)
    return raw


@pytest.mark.parametrize(
    'db_client',
    [MockExchange.create()],
    indirect=True
)
async def test_imports(mock_execs, pnl_service, db, time, db_client):
    trades = await db_select_all(
        Trade,
        eager=[Trade.executions, Trade.max_pnl, Trade.min_pnl],
        client_id=db_client.id
    )
    execs = list(
        itertools.chain.from_iterable(trade.executions for trade in trades)
    )

    assert len(execs) == len(mock_execs)
    assert sum(e.qty for e in execs) == sum(e.qty for e in mock_execs)
    assert sum(e.effective_qty for e in execs).is_zero()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.open_qty.is_zero()
    assert trade.qty == size
    assert trade.max_pnl.total != trades[0].min_pnl.total
