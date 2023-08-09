from __future__ import annotations

import abc
import asyncio
import logging
import time
import urllib.parse
from asyncio import Future, Task
from asyncio.queues import PriorityQueue
from collections import deque, OrderedDict
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Dict, Optional, Union, Set
from typing import NamedTuple
from typing import TYPE_CHECKING

import aiohttp.client
import pytz
from aiohttp import ClientResponse, ClientResponseError
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from core import json as customjson, json, utils
from core.utils import utc_now
from database.dbmodels.client import Client, ClientState
# from database.dbmodels.ohlc import OHLC
from database.dbmodels.execution import Execution
from database.dbmodels.transfer import Transfer, RawTransfer
from database.enums import Priority, ExecType, Side, MarketType
from database.errors import RateLimitExceeded, ExchangeUnavailable, ExchangeMaintenance, ResponseError, \
    InvalidClientError
from database.models.market import Market
from database.models.miscincome import MiscIncome
from database.models.observer import Observer
from database.models.ohlc import OHLC

if TYPE_CHECKING:
    from database.dbmodels.balance import Balance

logger = logging.getLogger(__name__)


class Cached(NamedTuple):
    url: str
    response: dict
    expires: float


class TaskCache(NamedTuple):
    url: str
    task: Future
    expires: float


class RequestItem(NamedTuple):
    priority: Priority
    future: Future
    cache: bool
    weight: Optional[int]
    request: Request
    client_id: int

    def __gt__(self, other):
        return self.priority.value > other.priority.value

    def __lt__(self, other):
        return self.priority.value < other.priority.value


class Position(NamedTuple):
    symbol: str
    side: Side
    qty: Decimal
    entry_price: Decimal

    @property
    def effective_qty(self):
        return self.qty if self.side == Side.BUY else -self.qty


class State(Enum):
    OK = 1
    RATE_LIMIT = 2
    MAINTANENANCE = 3
    OFFLINE = 4


class Request(NamedTuple):
    method: str
    url: str
    path: str
    headers: Optional[dict]
    params: Optional[dict]
    json: Optional[dict]

    def __hash__(self):
        return json.dumps(self._asdict()).__hash__()


def create_limit(interval_seconds: int, max_amount: int, default_weight: int):
    return Limit(
        interval_seconds,
        max_amount,
        max_amount,
        default_weight,
        interval_seconds / max_amount
    )


@dataclass
class Limit:
    interval_seconds: int
    max_amount: int
    amount: float
    default_weight: int
    refill_rate_seconds: float
    last_ts: float = 0

    def validate(self, weight: int = None):
        return self.amount < (weight or self.default_weight)

    def refill(self, ts: float):
        self.amount = min(
            self.amount + (ts - self.last_ts) * self.refill_rate_seconds,
            self.max_amount
        )
        self.last_ts = ts

    def sleep_for_weight(self, weight: int = None):
        return asyncio.sleep((weight or self.default_weight) / self.refill_rate_seconds)


class ExchangeWS(abc.ABC):
    async def connect(self):
        raise NotImplementedError

    async def disconnect(self):
        raise NotImplementedError


class Exchange(Observer):
    supports_extended_data = False
    state = State.OK
    exchange: str = ''
    required_extra_args: Set[str] = set()

    _ENDPOINT: str
    _SANDBOX_ENDPOINT: str
    _cache: Dict[Request, Cached] = {}

    # Networking
    _response_result = ''
    _request_queue: PriorityQueue[RequestItem] = PriorityQueue()
    _response_error = ''
    _request_task: Task = None
    _http: aiohttp.ClientSession = None

    # Rate Limiting
    _limits = [
        create_limit(interval_seconds=60, max_amount=60, default_weight=1)
    ]

    def __init__(self,
                 client: Client,
                 http_session: aiohttp.ClientSession,
                 db_maker: sessionmaker,
                 on_execution,
                 commit=True):
        self.client_id = client.id if commit else None
        self.exchange = client.exchange
        self.client: Optional[Client] = client
        self.db_maker = db_maker

        self._http = http_session
        self._last_fetch: Balance | None = None

        self._pending_execs: deque[Execution] = deque()

        self._on_execution = on_execution

        cls = self.__class__

        if cls._http is None or cls._http.closed:
            cls._http = http_session

        if cls._request_task is None:
            cls._request_task = asyncio.create_task(cls._request_handler())

        self._logger = logging.getLogger(__name__ + f' {self.exchange} id={self.client_id}')

    async def get_ohlc(self,
                       symbol: str,
                       since: datetime = None,
                       to: datetime = None,
                       resolution_s: int = None,
                       limit: int = None) -> List[OHLC]:
        raise NotImplementedError

    async def get_transfers(self, since: datetime = None) -> List[Transfer]:
        if not since:
            since = self.client.last_transfer_sync
        raw_transfers = await self._get_transfers(since)
        if raw_transfers:
            raw_transfers.sort(key=lambda transfer: transfer.time)

            result = []
            for raw_transfer in raw_transfers:

                if raw_transfer.amount:
                    market = Market(
                        base=raw_transfer.coin,
                        quote=self.client.currency
                    )
                    rate = await self.conversion_rate(market, raw_transfer.time)

                    transfer = Transfer(
                        client_id=self.client_id,
                        coin=raw_transfer.coin
                    )

                    transfer.execution = Execution(
                        symbol=self.get_symbol(market),
                        qty=abs(raw_transfer.amount),
                        price=rate,
                        side=Side.BUY if raw_transfer.amount > 0 else Side.SELL,
                        time=raw_transfer.time,
                        type=ExecType.TRANSFER,
                        market_type=raw_transfer.market_type or MarketType.SPOT,
                        commission=raw_transfer.fee,
                        settle=raw_transfer.coin,
                        transfer=transfer
                    )
                    result.append(transfer)
            return result
        else:
            return []

    async def get_executions(self,
                             since: datetime) -> tuple[List[Transfer], List[Execution], List[MiscIncome]]:
        transfers = await self.get_transfers(since)
        execs, misc = await self._get_executions(since, init=self.client.last_execution_sync is None)

        # for transfer in transfers:
        #     if transfer.coin:
        #         raw_amount = transfer.extra_currencies.get(transfer.coin, transfer.amount)
        #         transfer.execution = Execution(
        #             symbol=self._symbol(transfer.coin),
        #             qty=abs(raw_amount),
        #             price=transfer.amount / raw_amount,
        #             side=Side.BUY if transfer.amount > 0 else Side.SELL,
        #             time=transfer.time,
        #             type=ExecType.TRANSFER,
        #             commission=transfer.commission
        #         )
        #         execs.append(transfer.execution)

        for transfer in transfers:
            if transfer.execution:
                execs.append(transfer.execution)
        execs.sort(key=lambda e: e.time)
        return transfers, execs, misc

    async def _get_executions(self,
                              since: datetime,
                              init=False) -> tuple[List[Execution], List[MiscIncome]]:
        raise NotImplementedError

    @classmethod
    def _get_market_type(cls, symbol: str):
        raise NotImplementedError

    @classmethod
    def exclude_from_trade(cls, execution: Execution):
        return execution.market_type == MarketType.SPOT

    async def clean_ws(self):
        pass

    async def start_ws(self):
        pass

    async def _get_transfers(self,
                             since: datetime = None,
                             to: datetime = None) -> List[RawTransfer]:
        logger.warning(f'Exchange {self.exchange} does not implement get_transfers')
        return []

    async def get_balance(self, upnl=True) -> Balance:
        now = utc_now()
        balance = await self._get_balance(now, upnl=upnl)
        if not balance.time:
            balance.time = now
        self._last_fetch = balance
        balance.client_id = self.client_id
        return balance

    @abc.abstractmethod
    async def get_positions(self) -> dict[str, list[Position]]:
        return utils.groupby(await self._get_positions(), lambda p: p.symbol)

    async def get_position(self, symbol: str, side: Side) -> Optional[Position]:
        positions = await self._get_positions()
        return next((p for p in positions if p.side == side and p.symbol == symbol), None)

    @abc.abstractmethod
    async def _get_positions(self) -> list[Position]:
        pass

    @abc.abstractmethod
    async def _get_balance(self, time: datetime, upnl=True):
        logger.error(f'Exchange {self.exchange} does not implement _get_balance')
        raise NotImplementedError(f'Exchange {self.exchange} does not implement _get_balance')

    @abc.abstractmethod
    def _sign_request(self, method: str, path: str, headers=None, params=None, data=None, **kwargs):
        logger.error(f'Exchange {self.exchange} does not implement _sign_request')

    def _set_rate_limit_parameters(self, response: ClientResponse):
        pass

    @classmethod
    def _check_for_error(cls, response_json: dict, response: ClientResponse):
        if response.status == 401:
            raise InvalidClientError(human='Invalid Credentials', response=response)

    @classmethod
    async def _process_response(cls, response: ClientResponse) -> dict:
        response_json = await response.json(loads=customjson.loads)
        try:
            response.raise_for_status()
        except ClientResponseError as e:
            logger.error(f'{e}\n{response_json=}\n{response.reason=}')

            error = ''
            if response.status == 400:
                error = "400 Bad Request. This is probably an internal bug, please contact dev"
            elif response.status == 401:
                error = f"401 Unauthorized ({response.reason}). You might want to check your API access"
            elif response.status == 403:
                error = f"403 Access Denied ({response.reason}). You might want to check your API access"
            elif response.status == 404:
                error = f"404 Not Found. This is probably an internal bug, please contact dev"
            elif response.status == 429:
                raise RateLimitExceeded(
                    root_error=e,
                    human="429 Rate Limit Exceeded. Please try again later."
                )
            elif 500 <= response.status < 600:
                raise ExchangeUnavailable(
                    root_error=e,
                    human=f"{response.status} Problem or Maintenance on {cls.exchange} servers."
                )

            raise ResponseError(
                root_error=e,
                human=error
            )

        cls._check_for_error(response_json, response)

        # OK
        if response.status == 200:
            if cls._response_result and cls._response_result in response_json:
                return response_json[cls._response_result]
            return response_json

    @classmethod
    def get_market(cls, raw: str) -> Optional[Market]:
        raise NotImplementedError

    @classmethod
    def get_symbol(cls, market: Market) -> str:
        logger.warning(f'Exchange {cls.exchange} does not implement get_symbol')
        raise NotImplementedError

    @classmethod
    def is_equal(cls, market: Market) -> bool:
        return market.quote == market.base or (cls.usd_like(market.quote) and cls.usd_like(market.base))

    @classmethod
    def set_weights(cls, weight: int, response: ClientResponse):
        for limit in cls._limits:
            limit.amount -= weight or limit.default_weight

    @classmethod
    async def _request_handler(cls):
        """
        Task which is responsible for putting out the requests
        for this specific Exchange.

        All requests have to be put onto the :cls._request_queue:
        so that the handler can properly Knockkek ist geil execute them according to the current
        rate limit states. If there is enough weight available it will also
        decide to run requests in parallel.
        """
        while True:
            try:

                item = await cls._request_queue.get()
                request = item.request

                ts = time.monotonic()

                # Provide some basic limiting by default
                for limit in cls._limits:
                    limit.refill(ts)
                    if limit.validate(item.weight):
                        await limit.sleep_for_weight(item.weight)
                        ts = time.monotonic()
                        limit.refill(ts)

                try:
                    async with cls._http.request(request.method,
                                                 request.url,
                                                 params=request.params,
                                                 headers=request.headers,
                                                 json=request.json) as resp:
                        cls.set_weights(item.weight, resp)
                        resp = await cls._process_response(resp)

                        if item.cache:
                            cls._cache[item.request] = Cached(
                                url=item.request.url,
                                response=resp,
                                expires=time.time() + 3600
                            )

                        item.future.set_result(resp)
                except ResponseError as e:
                    if e.root_error.status == 401:
                        e = InvalidClientError(root_error=e.root_error, human=e.human)
                    logger.error(f'Error while executing request: {e.human} {e.root_error}')
                    item.future.set_exception(e)
                except RateLimitExceeded as e:
                    cls.state = State.RATE_LIMIT
                    if e.retry_ts:
                        await asyncio.sleep(time.monotonic() - e.retry_ts)
                except ExchangeUnavailable as e:
                    cls.state = State.OFFLINE
                except ExchangeMaintenance as e:
                    cls.state = State.MAINTANENANCE
                except Exception as e:
                    logger.exception(f'Exception while execution request {item}')
                    item.future.set_exception(e)
                finally:
                    cls._request_queue.task_done()
            except Exception as e:
                logger.exception('why')

    async def _request(self,
                       method: str, path: str,
                       headers=None, params=None, data=None,
                       sign=True, cache=False,
                       endpoint=None, dedupe=False, weight=None,
                       **kwargs):
        url = (endpoint or (self._SANDBOX_ENDPOINT if self.client.sandbox else self._ENDPOINT)) + path

        params = OrderedDict(params or {})
        headers = headers or {}

        if sign:
            self._sign_request(method, path, headers, params, data)

        request = Request(
            method,
            url,
            path,
            headers,
            params,
            data,
        )

        if cache:
            cached = self._cache.get(request)
            if cached and time.time() < cached.expires:
                return cached.response

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.__class__._request_queue.put(
            RequestItem(
                priority=Priority.MEDIUM,
                future=future,
                cache=cache,
                weight=None,
                request=request,
                client_id=self.client_id
            )
        )
        try:
            return await future
        except InvalidClientError:
            if self.client_id:
                async with self.db_maker() as db:
                    await db.execute(
                        update(Client).where(Client.id == self.client_id).values(state=ClientState.INVALID)
                    )
                    await db.commit()
            raise

    def get(self, path: str, **kwargs):
        return self._request('GET', path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._request('POST', path, **kwargs)

    def put(self, path: str, **kwargs):
        return self._request('PUT', path, **kwargs)

    async def conversion_rate(self, market: Market, date: datetime, resolution_s: int = None):
        if self.usd_like(market.base):
            return 1

        # conversion = await db_unique(
        #    Conversion.at_dt(dt=date,
        #                     market=market,
        #                     tolerance=timedelta(seconds=resolution_s),
        #                     exchange=self.exchange)
        # )
        #
        # if conversion:
        #    return conversion.rate

        ticker = await self.get_ohlc(
            self.get_symbol(market),
            since=date,
            resolution_s=None,
            limit=1
        )
        if ticker:
            return (ticker[0].open + ticker[0].close) / 2

    async def _convert_to_usd(self, amount: Decimal, coin: str, date: datetime):
        if self.usd_like(coin):
            return amount
        # return await self._convert()

    @classmethod
    def usd_like(cls, coin: str):
        return coin in ('USD', 'USDT', 'USDC', 'BUSD')

    @classmethod
    def _query_string(cls, params: Dict):
        query_string = urllib.parse.urlencode(params)
        return f"?{query_string}" if query_string else ""

    @classmethod
    def parse_ts(cls, ts: Union[int, float]):
        return datetime.fromtimestamp(ts, pytz.utc)

    @classmethod
    def date_as_ms(cls, datetime: datetime):
        return int(datetime.timestamp() * 1000)

    @classmethod
    def date_as_s(cls, datetime: datetime):
        return int(datetime.timestamp())

    @classmethod
    def parse_ms_dt(cls, ts_ms: int | str):
        return datetime.fromtimestamp(int(ts_ms) / 1000, pytz.utc)

    @classmethod
    def parse_ms_d(cls, ts_ms: int | str):
        return date.fromtimestamp(int(ts_ms) / 1000)

    def __repr__(self):
        return f'<Worker exchange={self.exchange} client_id={self.client_id}>'
