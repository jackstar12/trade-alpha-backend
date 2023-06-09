import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterator, Optional

import pytz

from common.exchanges.channel import Channel
from common.exchanges.exchangeticker import ExchangeTicker, Subscription
from core import utc_now
from database.dbmodels import Execution, Balance, Client
from database.dbmodels.client import ClientType, ExchangeInfo
from database.dbmodels.transfer import RawTransfer
from database.enums import Side, ExecType, MarketType
from common.exchanges.exchangeworker import ExchangeWorker
from database.models import BaseModel
from database.models.client import ClientCreate
from database.models.market import Market
from database.models.miscincome import MiscIncome
from database.models.ohlc import OHLC
from database.models.ticker import Ticker

queue = asyncio.Queue()


class RawExec(BaseModel):
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    reduce: Optional[bool] = True
    market_type: Optional[MarketType] = MarketType.DERIVATIVES

    def to_exec(self, client: Client):
        return Execution(**self.dict(),
                         settle=client.currency,
                         time=utc_now(),
                         commission=Decimal(random.randint(50, 100) * .01),
                         type=ExecType.TRADE)


class MockTicker(ExchangeTicker):
    NAME = 'mock'

    async def _unsubscribe(self, sub: Subscription):
        pass

    async def disconnect(self):
        pass

    async def generate_ticker(self, sub: Subscription):
        while True:
            await self._callbacks[sub].notify(
                Ticker(
                    symbol=sub.kwargs['symbol'],
                    src=ExchangeInfo(name='mock', sandbox=True),
                    price=Decimal(10000 + random.randint(-100, 100))
                )
            )
            await asyncio.sleep(0.1)

    async def _subscribe(self, sub: Subscription):
        if sub.channel == Channel.TICKER:
            asyncio.create_task(
                self.generate_ticker(sub)
            )

    async def connect(self):
        pass


class MockExchange(ExchangeWorker):
    supports_extended_data = True
    exchange = 'mock'
    exec_start = datetime(year=2022, month=1, day=1)

    _queue: asyncio.Queue = None

    @classmethod
    def create(cls):
        return ClientCreate(
            name='Mock Client',
            exchange=cls.exchange,
            api_key='super',
            api_secret='secret',
            sandbox=True,
            type=ClientType.FULL
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self._queue:
            self.__class__._queue = asyncio.Queue()
        self._execs = []

    @classmethod
    async def put_exec(cls, **kwargs):
        await cls._queue.put(RawExec(**kwargs))

    async def wait_queue(self):
        while True:
            self._logger.info('Mock listening for execs')
            new = await self.__class__._queue.get()
            execution = new.to_exec(self.client)
            await self._on_execution(execution)
            self._execs.append(execution)

    async def startup(self):
        self._queue_waiter = asyncio.create_task(self.wait_queue())

    async def cleanup(self):
        self._queue_waiter.cancel()

    def _sign_request(self, method: str, path: str, headers=None, params=None, data=None, **kwargs):
        pass

    async def _get_ohlc(self, symbol: str, since: datetime = None, to: datetime = None, resolution_s: int = None,
                        limit: int = None) -> list[OHLC]:
        data = [
            Decimal(10000),
            Decimal(12500),
            Decimal(15000),
            Decimal(17500),
            Decimal(20000),
            Decimal(22500),
            Decimal(25000),
            Decimal(22500)
        ]
        ohlc_data = [
            OHLC(
                open=val, high=val, low=val, close=val,
                volume=Decimal(0),
                time=self.exec_start + timedelta(hours=12 * index)
            )
            for index, val in enumerate(data)
        ]
        return [
            ohlc for ohlc in ohlc_data if since < ohlc.time < to
        ]

    async def _get_transfers(self, since: datetime = None, to: datetime = None) -> list[RawTransfer]:
        return []
        return [
            RawTransfer(
                amount=Decimal(1), time=self.exec_start - timedelta(days=1), coin='BTC', fee=Decimal(0)
            )
        ]

    async def _get_executions(self, since: datetime, init=False) -> tuple[Iterator[Execution], Iterator[MiscIncome]]:
        return self._execs, []
        data = [
            dict(qty=1, price=10000, side=Side.SELL),
            dict(qty=1, price=10000, side=Side.BUY),
            dict(qty=1, price=15000, side=Side.BUY),
            dict(qty=1, price=20000, side=Side.SELL),
            dict(qty=2, price=25000, side=Side.SELL),
            dict(qty=1, price=20000, side=Side.BUY),
        ]
        return [
                   Execution(**attrs, time=self.exec_start + timedelta(days=index), type=ExecType.TRADE,
                             symbol='BTCUSDT')
                   for index, attrs in enumerate(data)
               ], []

    # https://binance-docs.github.io/apidocs/futures/en/#account-information-v2-user_data
    async def _get_balance(self, time: datetime, upnl=True):
        return Balance(
            realized=100,
            unrealized=0,
            time=utc_now(),
            extra_currencies=[]
        )

    @classmethod
    def get_symbol(cls, market: Market) -> str:
        return market.base + market.quote
