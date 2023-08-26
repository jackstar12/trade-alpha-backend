import asyncio
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import pytz

from lib.exchanges.channel import Channel
from lib.exchanges.exchange import Exchange, Position
from lib.exchanges.exchangeticker import ExchangeTicker, Subscription
from lib.utils import utc_now
from lib.db.models import Execution, Balance, Client, User, Amount
from lib.db.models.client import ClientType, ExchangeInfo
from lib.db.models.transfer import RawTransfer
from lib.db.enums import Side, ExecType, MarketType
from lib.models import BaseModel
from lib.models.client import ClientCreate
from lib.models.market import Market
from lib.models.ohlc import OHLC
from lib.models.ticker import Ticker

queue = asyncio.Queue()

_next_exec_time = utc_now() - timedelta(days=30)


class RawExec(BaseModel):
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    reduce: Optional[bool] = True
    market_type: Optional[MarketType] = MarketType.DERIVATIVES

    def to_exec(self, client: Client):
        global _next_exec_time
        _next_exec_time += timedelta(hours=1)
        return Execution(
            **self.dict(),
            settle=client.currency,
            time=_next_exec_time,
            commission=Decimal(random.randint(50, 100) * 0.01),
            type=ExecType.TRADE
        )


class MockTicker(ExchangeTicker):
    NAME = "mock"

    async def _unsubscribe(self, sub: Subscription):
        pass

    async def disconnect(self):
        pass

    async def generate_ticker(self, sub: Subscription):
        while True:
            await self._callbacks[sub].notify(
                Ticker(
                    symbol=sub.kwargs["symbol"],
                    src=ExchangeInfo(name="mock", sandbox=True),
                    price=Decimal(10000 + random.randint(-100, 100)),
                )
            )
            await asyncio.sleep(0.1)

    async def _subscribe(self, sub: Subscription):
        if sub.channel == Channel.TICKER:
            asyncio.create_task(self.generate_ticker(sub))

    async def connect(self):
        pass


class MockCreate(ClientCreate):
    name = "Mock Client"
    exchange = "mock"
    api_key = "super"
    api_secret = "secret"
    sandbox = True
    type = ClientType.FULL
    # execs: list[RawExec] = []
    # transfers: list[RawTransfer] = []
    # positions: list[Position] = []

    def get(self, user: Optional[User] = None) -> Client:
        client = super().get(user)

        # MockExchange.execs = self.execs
        # MockExchange.transfers = self.transfers
        # MockExchange.positions = self.positions

        return client


class MockExchange(Exchange):
    supports_extended_data = True
    exchange = "mock"
    exec_start = datetime(year=2022, month=1, day=1, tzinfo=pytz.UTC)

    queue = asyncio.Queue()
    execs: list[RawExec] = []
    transfers: list[RawTransfer] = []
    positions: list[Position] = []

    @classmethod
    def create(cls):
        return ClientCreate(
            name="Mock Client",
            exchange=cls.exchange,
            api_key="super",
            api_secret="secret",
            sandbox=True,
            type=ClientType.FULL,
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execs = []
        while not self.queue.empty():
            self.execs.append(self.queue.get_nowait())

    @classmethod
    async def put_exec(cls, **kwargs):
        await cls.queue.put(RawExec(**kwargs))

    @classmethod
    def set_execs(cls, *execs: RawExec):
        MockExchange.queue = asyncio.Queue()
        for exec in execs:
            MockExchange.queue.put_nowait(exec)
        return list(execs)

    async def wait_queue(self):
        while True:
            self._logger.info("Mock listening for execs")
            new = await self.__class__.queue.get()
            execution = new.to_exec(self.client)
            await self._on_execution(execution)
            self.execs.append(new)

    async def _get_positions(self) -> list[Position]:
        return self.positions

    async def start_ws(self):
        self._queue_waiter = asyncio.create_task(self.wait_queue())

    async def clean_ws(self):
        self._queue_waiter.cancel()

    def _sign_request(
        self, method: str, path: str, headers=None, params=None, data=None, **kwargs
    ):
        pass

    async def get_ohlc(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        to: Optional[datetime] = None,
        resolution_s: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[OHLC]:
        ohlc_data = []
        since = since or self.exec_start
        to = to or utc_now()

        step = timedelta(seconds=resolution_s) if resolution_s else (to - since) / 200

        while since < to:
            since += step
            val = Decimal(random.randint(15000, 25000))
            ohlc_data.append(
                OHLC(
                    open=val,
                    high=val,
                    low=val,
                    close=val,
                    volume=Decimal(0),
                    time=since,
                )
            )
        return ohlc_data

    async def _get_transfers(
        self, since: Optional[datetime] = None, to: Optional[datetime] = None
    ) -> list[RawTransfer]:
        return self.transfers

    async def _get_executions(
        self, since: datetime, init=False
    ) -> list[Execution]:
        return [raw.to_exec(self.client) for raw in self.execs]
        data = [
            dict(qty=1, price=10000, side=Side.SELL),
            dict(qty=1, price=10000, side=Side.BUY),
            dict(qty=1, price=15000, side=Side.BUY),
            dict(qty=1, price=20000, side=Side.SELL),
            dict(qty=2, price=25000, side=Side.SELL),
            dict(qty=1, price=20000, side=Side.BUY),
        ]
        return [
            Execution(
                **attrs,
                time=self.exec_start + timedelta(days=index),
                type=ExecType.TRADE,
                symbol="BTCUSDT"
            )
            for index, attrs in enumerate(data)
        ], []

    # https://binance-docs.github.io/apidocs/futures/en/#account-information-v2-user_data
    async def _get_balance(self, time: datetime, upnl=True):
        return [
            Amount(currency="USD", realized=100, unrealized=0),
        ]

    @classmethod
    def get_symbol(cls, market: Market) -> str:
        return market.base + market.quote
