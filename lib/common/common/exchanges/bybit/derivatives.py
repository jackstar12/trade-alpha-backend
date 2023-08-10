import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Tuple, Optional

from aiohttp import ClientResponse

from common.exchanges.bybit._base import (
    Category,
    _BybitBaseClient,
    all_intervals,
    interval_map,
    Account,
)
from common.exchanges.exchange import create_limit, Position
from core import get_multiple
from core import utc_now
from database.dbmodels.balance import Balance, Amount
from database.dbmodels.execution import Execution
from database.dbmodels.transfer import RawTransfer
from database.enums import Side
from database.models.market import Market
from database.models.ohlc import OHLC

logger = logging.getLogger()


def get_contract_type(contract: str):
    if contract.endswith("USDT"):
        return Category.LINEAR
    else:
        return Category.INVERSE


class BybitDerivatives(_BybitBaseClient):
    # https://bybit-exchange.github.io/docs/derivativesV3/contract/#t-websocket
    _WS_ENDPOINT = "wss://stream.bybit.com/contract/private/v3"
    _WS_SANDBOX_ENDPOINT = "wss://stream-testnet.bybit.com/contract/private/v3"

    exchange = "bybit-derivatives"

    _limits = [
        create_limit(interval_seconds=5, max_amount=5 * 70, default_weight=1),
        create_limit(interval_seconds=5, max_amount=5 * 50, default_weight=1),
        create_limit(interval_seconds=120, max_amount=120 * 50, default_weight=1),
        create_limit(interval_seconds=120, max_amount=120 * 20, default_weight=1),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._internal_transfers: Optional[Tuple[datetime, List[RawTransfer]]] = None
        self._latest_balance = None
        # TODO: Fetch symbols https://bybit-exchange.github.io/docs/inverse/#t-querysymbol

    @classmethod
    def get_market(cls, raw: str) -> Optional[Market]:
        if raw.endswith("USDT"):
            return Market(base=raw[:-4], quote="USDT")
        elif raw.endswith("USD"):
            return Market(base=raw[:-3], quote="USD")

    @classmethod
    def set_weights(cls, weight: int, response: ClientResponse):
        used = response.headers.get("X-Bapi-Limit-Status")
        logger.info(f"Remaining: {used}")

    async def get_tickers(self, contract: Category) -> list[dict]:
        resp = await self.get(
            "/derivatives/v3/public/tickers",
            params={"contract": contract.value},
            cache=True,
            sign=False,
        )
        return resp["list"]

    async def _get_internal_executions_v3(
        self, category: Category, since: datetime
    ) -> list[Execution]:
        since_ts = self._parse_date(since) if since else 0

        params = {"limit": 50, "category": category.value, "orderStatus": "Filled"}
        raw_orders = await self._get_paginated_v3(
            path="/v5/order/history",
            valid=lambda r: int(r["createdTime"]) > since_ts,
            params=params,
        )

        symbols = set(raw["symbol"] for raw in raw_orders)

        executions = [self._parse_order_v3(raw) for raw in raw_orders]
        params = {
            "limit": 100,
            "category": category.value,
            "execType": "Funding",
            "start_time": since_ts,
        }
        for symbol in symbols:
            params["symbol"] = symbol
            funding = await self._get_paginated_v3(
                path="/v5/execution/list",
                valid=lambda r: int(r["execTime"]) > since_ts,
                params=params,
            )

            executions.extend(self._parse_exec_v3(raw) for raw in funding)
        return executions

    async def _internal_get_balance_v3(self, category: Optional[Category] = None):
        params = {}
        if category:
            params["category"] = category.value

        balances, tickers = await asyncio.gather(
            self.get("/contract/v3/private/account/wallet/balance"),
            self.get(
                "/derivatives/v3/public/tickers", params=params, sign=False, cache=True
            ),
        )

        total_realized = total_unrealized = Decimal(0)
        extra_currencies: list[Amount] = []

        ticker_prices = {
            ticker["symbol"]: Decimal(ticker["lastPrice"]) for ticker in tickers["list"]
        }
        err_msg = None
        for balance in balances["list"]:
            realized = Decimal(balance["walletBalance"])
            unrealized = Decimal(balance["equity"]) - realized
            coin = balance["coin"]
            price = 0

            if coin == "USDT":
                price = Decimal(1)
            elif realized and (category == Category.INVERSE or not category):
                price = get_multiple(ticker_prices, f"{coin}USD", f"{coin}USDT")
                if not price:
                    logging.error(
                        f"Bybit Bug: ticker prices do not contain info about {coin}:\n{ticker_prices}"
                    )
                    continue

            if realized:
                extra_currencies.append(
                    Amount(
                        currency=coin,
                        realized=realized,
                        unrealized=unrealized,
                        rate=price,
                    )
                )
            total_realized += realized * price
            total_unrealized += unrealized * price

        return Balance(
            realized=total_realized,
            unrealized=total_unrealized,
            extra_currencies=extra_currencies,
            error=err_msg,
        )

    async def get_ohlc(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        to: Optional[datetime] = None,
        resolution_s: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[OHLC]:
        limit = limit or 200

        if not resolution_s:
            limit, resolution_s = self._calc_resolution(limit, all_intervals, since, to)
        ts = int(since.timestamp())
        params = {
            "symbol": symbol,
            "interval": interval_map[resolution_s],
            "from": ts - (ts % resolution_s),
        }
        if limit:
            params["limit"] = limit

        contract_type = get_contract_type(symbol)
        # Different endpoints, but they both use the exact same repsonse
        if contract_type == Category.INVERSE:
            # https://bybit-exchange.github.io/docs/futuresV2/inverse/#t-markpricekline
            url = "/v2/public/mark-price-kline"
        elif contract_type == Category.LINEAR:
            # https://bybit-exchange.github.io/docs/futuresV2/linear/#t-markpricekline
            url = "/public/linear/mark-price-kline"
        else:
            raise

        data = await self.get(url, params=params, sign=False, cache=True)

        # "result": [{
        #     "id": 3866948,
        #     "symbol": "BTCUSDT",
        #     "period": "1",
        #     "start_at": 1577836800,
        #     "open": 7700,
        #     "high": 999999,
        #     "low": 0.5,
        #     "close": 6000
        # }

        if data:
            return [
                OHLC(
                    open=ohlc["open"],
                    high=ohlc["high"],
                    low=ohlc["low"],
                    close=ohlc["close"],
                    time=self.parse_ts(ohlc["start_at"]),
                )
                for ohlc in data
            ]
        else:
            return []

    async def _get_internal_positions(self, category: Category) -> list[Position]:
        data = await self.get(
            "/v5/position/list",
            params={"limit": 50, "category": category.value, "settleCoin": "USDT"},
        )

        return [
            Position(
                symbol=raw["symbol"],
                side=Side.BUY if raw["side"] == "Buy" else Side.SELL,
                qty=Decimal(raw["size"]),
                entry_price=Decimal(raw["avgPrice"]),
            )
            for raw in data["list"]
        ]

    async def _get_positions(self) -> list[Position]:
        return await self._get_internal_positions(Category.LINEAR)

    async def _get_transfers(
        self, since: Optional[datetime] = None, to: Optional[datetime] = None
    ) -> List[RawTransfer]:
        self._internal_transfers = (
            since,
            await self._get_internal_transfers_v3(since, Account.DERIVATIVE),
        )
        return self._internal_transfers[1]

    async def _get_executions(self, since: datetime, init=False):
        since = since or utc_now() - timedelta(days=365)
        results = []
        results.extend(await self._get_internal_executions_v3(Category.LINEAR, since))
        results.extend(await self._get_internal_executions_v3(Category.INVERSE, since))
        return results, []

    # https://bybit-exchange.github.io/docs/inverse/?console#t-balance
    async def _get_balance(self, time: datetime, upnl=True):
        # return await self._internal_get_balance_v5(Account.CONTRACT)
        return await self._internal_get_balance_v3()


class BybitSpotClient(_BybitBaseClient):
    exchange = "bybit-spot"

    _limits = [
        # TODO: Tweak Rate Limiter (method based + continious limits)
        create_limit(
            interval_seconds=2 * 60, max_amount=2 * 50, default_weight=1
        )  # Some kind of type=Type.CONTINIOUS
    ]
