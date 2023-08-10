import asyncio
import itertools
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from common.exchanges import SANDBOX_CLIENTS, EXCHANGES, BybitDerivatives
from common.exchanges.exchange import Position
from common.messenger import TableNames, Category
from common.test_utils.fixtures import Channel, Messages
from common.test_utils.mockexchange import MockExchange, RawExec, MockCreate
from core import utc_now
from database.dbasync import db_select_all, db_all
from database.dbmodels.trade import Trade
from database.enums import Side

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def time():
    return datetime.now()


symbol = "BTCUSDT"
size = Decimal("0.01")


@pytest.mark.parametrize("client", [MockCreate()], indirect=True)
async def test_realtime(
    db, pnl_service, time, client, registered_client, session_maker, messenger, redis
):
    first_balance = await client.get_latest_balance(redis)

    async def get_trades() -> list[Trade]:
        async with session_maker() as db:
            return await db_all(
                select(Trade)
                .where(
                    Trade.client_id == client.id,
                    Trade.symbol == symbol,
                )
                .order_by(Trade.open_time),
                Trade.min_pnl,
                Trade.max_pnl,
                Trade.pnl_data,
                session=db,
            )

    async def get_trade():
        trades = await get_trades()
        assert len(trades) == 1
        return trades[0]

    async with Messages.create(
        Channel(TableNames.TRADE, Category.NEW), messenger=messenger
    ) as listener:
        await MockExchange.put_exec(
            symbol=symbol, side=Side.BUY, qty=size / 2, price=7500
        )
        await listener.wait(2)

    await asyncio.sleep(0.2)
    trade = await get_trade()
    assert trade.qty == size / 2

    async with Messages.create(
        Channel(TableNames.TRADE, Category.UPDATE), messenger=messenger
    ) as listener:
        await MockExchange.put_exec(
            symbol=symbol, side=Side.BUY, qty=size / 2, price=12500
        )
        await listener.wait(3)

    await asyncio.sleep(0.2)
    trade = await get_trade()
    assert trade.entry == 10000
    assert trade.qty == size

    async with Messages.create(
        Channel(TableNames.BALANCE, Category.LIVE),
        Channel(TableNames.TRADE, Category.UPDATE),
        messenger=messenger,
    ) as listener:
        await MockExchange.put_exec(
            symbol=symbol, side=Side.SELL, qty=size / 2, price=17500
        )
        await listener.wait(3)

    await asyncio.sleep(0.2)
    trade = await get_trade()
    assert trade.open_qty == size / 2
    assert trade.qty == size
    assert trade.max_pnl.total != trade.min_pnl.total
    assert trade.exit == 17500

    second_balance = await client.get_latest_balance(redis)
    assert first_balance.unrealized != second_balance.unrealized

    async with Messages.create(
        Channel(TableNames.TRADE, Category.FINISHED),
        Channel(TableNames.TRADE, Category.NEW),
        messenger=messenger,
    ) as listener:
        await MockExchange.put_exec(
            symbol=symbol, side=Side.SELL, qty=size, price=22500
        )
        await listener.wait(1)

    await asyncio.sleep(0.2)

    trades = await get_trades()
    assert len(trades) == 2
    finished = trades[0]
    new = trades[1]
    assert not finished.is_open
    assert finished.close_time != finished.open_time
    assert new.is_open
    assert new.qty == size / 2


@pytest.mark.parametrize("client", SANDBOX_CLIENTS, indirect=True)
async def test_exchange(client, session_maker, http_session, ccxt_client):
    now = utc_now()
    fut = asyncio.get_event_loop().create_future()
    execs = []

    async def on_exec(new):
        execs.append(new)
        if len(execs) == 2:
            fut.set_result(execs)

    exchange = EXCHANGES[client.exchange](client, http_session, session_maker, on_exec)

    balance = await exchange.get_balance()

    await exchange.start_ws()

    async def get_total_position_size():
        positions = await exchange.get_positions()
        return sum(position.effective_qty for position in positions.get(symbol))

    previous = await get_total_position_size()

    ccxt_client.create_market_buy_order(symbol, float(size))

    current = await get_total_position_size()
    assert current - previous == size

    ccxt_client.create_market_sell_order(symbol, float(size))

    current = await get_total_position_size()
    assert current == previous

    await asyncio.wait_for(fut, 5)
    if client.exchange == BybitDerivatives.exchange:
        await asyncio.sleep(20)

    _, fetched_execs, _ = await exchange.get_executions(now)
    assert len(execs) == len(fetched_execs)

    new_balance = await exchange.get_balance()
    assert balance != new_balance

    await exchange.clean_ws()


async def all_trades(client):
    trades = await db_select_all(
        Trade,
        eager=[Trade.executions, Trade.max_pnl, Trade.min_pnl],
        client_id=client.id,
    )
    execs = list(itertools.chain.from_iterable(trade.executions for trade in trades))
    return trades, execs


@pytest.fixture
def execs(request):
    return MockExchange.set_execs(*request.param)


@pytest.fixture
def position(request):
    MockExchange.positions = request.param
    return request.param


@pytest.mark.parametrize("client", [MockExchange.create()], indirect=True)
@pytest.mark.parametrize(
    "execs,position",
    [
        [
            [
                RawExec(symbol=symbol, side=Side.SELL, qty=size, price=10000),
                RawExec(symbol=symbol, side=Side.BUY, qty=size, price=10000),
            ],
            [],
        ],
        [
            [
                RawExec(symbol=symbol, side=Side.SELL, qty=size, price=10000),
                RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
                RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
            ],
            [Position(symbol=symbol, qty=size, side=Side.BUY, entry_price=10000)],
        ],
        [
            [
                RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
                RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
                RawExec(symbol=symbol, side=Side.BUY, qty=size / 2, price=10000),
                RawExec(symbol=symbol, side=Side.SELL, qty=size, price=20000),
            ],
            [],
        ],
        [
            [
                RawExec(
                    symbol=symbol, side=Side.BUY, qty=size, price=10000, reduce=False
                ),
                RawExec(
                    symbol=symbol, side=Side.SELL, qty=size, price=10000, reduce=False
                ),
                RawExec(
                    symbol=symbol, side=Side.BUY, qty=size / 2, price=10000, reduce=True
                ),
                RawExec(
                    symbol=symbol,
                    side=Side.SELL,
                    qty=size / 2,
                    price=10000,
                    reduce=True,
                ),
            ],
            [
                Position(symbol=symbol, qty=size / 2, side=Side.BUY, entry_price=10000),
                Position(
                    symbol=symbol, qty=size / 2, side=Side.SELL, entry_price=10000
                ),
            ],
        ],
        pytest.param(
            # no sign of position start - no trade can be created, should fail
            [RawExec(symbol=symbol, side=Side.SELL, qty=size, price=20000)],
            [Position(symbol=symbol, qty=size, side=Side.BUY, entry_price=20000)],
            marks=pytest.mark.xfail,
        ),
    ],
    indirect=True,
)
async def test_imports(
    execs, position, pnl_service, db, time, client, registered_client
):
    trades, execs = await all_trades(client)
    worker = pnl_service.get_worker(client.id)

    for trade in trades:
        if trade.is_open:
            position = await worker.exchange.get_position(trade.symbol, trade.side)
            assert position
            assert trade.open_qty == position.qty
        assert trade.max_pnl.total != trade.min_pnl.total
