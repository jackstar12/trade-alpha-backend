import asyncio
from typing import Optional, Dict

from collector.errors import InvalidExchangeError
from collector.services.baseservice import BaseService
from lib import utils
from lib.exchanges import EXCHANGE_TICKERS
from lib.exchanges.exchangeticker import ExchangeTicker, Channel, Subscription
from lib.messenger import TableNames
from lib.db.models.client import ExchangeInfo
from lib.models.observer import Observer
from lib.models.ticker import Ticker


class DataService(BaseService, Observer):
    """
    Provides market data.

    It depends on the exchange having a ``ExchangeTicker``  implementation
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exchanges: Dict[ExchangeInfo, ExchangeTicker] = {}
        self._tickers: Dict[tuple[ExchangeInfo, str], Ticker] = {}

    # async def run_forever(self):
    #    await self._update_redis()

    async def teardown(self):
        for exchange in self._exchanges.values():
            await exchange.disconnect()

    async def subscribe(
        self,
        exchange: ExchangeInfo,
        sub: Subscription,
        observer: Optional[Observer] = None,
    ):
        """
        Subscribes to the given ecxhange channel.

            # Will subscribe to BTCUSDT Ticker Messages from binance-futures
            >>> self.subscribe(ExchangeInfo(name='binance-futures', sandbox=False), Channel.TICKER, symbol='BTCUSDT')

        :param sub:
        :param exchange: which exchange?
        :param observer: [Optional] will be notified whenever updates arrive
        """
        ticker = self._exchanges.get(exchange)
        if not ticker:
            self._logger.info(f"Creating ticker for {exchange}")
            ticker_cls = EXCHANGE_TICKERS.get(exchange.name)
            if ticker_cls and issubclass(ticker_cls, ExchangeTicker):
                ticker = ticker_cls(self._http_session, exchange.sandbox)
                self._exchanges[exchange] = ticker
                await ticker.connect()
            else:
                raise InvalidExchangeError()

        observer = observer or self
        try:
            await ticker.subscribe(sub, observer)
        except Exception:
            self._logger.exception("Could not subscribe")

    async def unsubscribe(
        self,
        exchange: ExchangeInfo,
        channel: Channel,
        observer: Optional[Observer] = None,
        **kwargs,
    ):
        self._logger.info(f"Unsubscribe: {exchange=} {channel=} {kwargs=}")

        ticker = self._exchanges.get(exchange)
        if ticker:
            observer = observer or self
            await ticker.unsubscribe(
                Subscription(channel=channel, kwargs=kwargs), observer
            )

    async def update(self, ticker: Ticker):
        # ticker: Ticker = new_state[0]
        self._logger.debug(ticker)
        self._tickers[(ticker.src, ticker.symbol)] = ticker

    async def get_ticker(self, symbol: str, exchange: ExchangeInfo):
        ticker = self._tickers.get((exchange, symbol))

        if not ticker:
            try:
                await self.subscribe(
                    exchange, Subscription.get(Channel.TICKER, symbol=symbol), self
                )
            except asyncio.exceptions.TimeoutError:
                pass
        return self._tickers.get((exchange, symbol))

    async def _update_redis(self):
        return None
        while True:
            for ticker in self._tickers.values():
                await self._redis.set(
                    utils.join_args(TableNames.TICKER, ticker.src, ticker.symbol),
                    str(ticker.price),
                )
            await asyncio.sleep(1)
