import asyncio
import hmac
import itertools
import logging
import time
import urllib.parse
from abc import ABC
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, List, Type, Callable, Any

from aiohttp import ClientResponse

from lib.exchanges.bybit.websocket import BybitWebsocketClient

from lib.exchanges.exchange import Exchange
from lib import utils
from lib.db.models.balance import Amount, Balance
from lib.db.models.execution import Execution
from lib.db.models.transfer import RawTransfer
from lib.db.enums import ExecType, Side, MarketType
from lib.db.errors import (
    ResponseError,
    InvalidClientError,
    RateLimitExceeded,
    WebsocketError,
)
from lib.models.async_websocket_manager import WebsocketManager
from lib.models.market import Market


class Transfer(Enum):
    SUCCESS = "SUCCESS"
    PENDING = "PENDING"
    FAILED = "FAILED"


class Account(Enum):
    SPOT = "SPOT"
    DERIVATIVE = "DERIVATIVE"
    UNIFIED = "UNIFIED"
    INVESTMENT = "INVESTMENT"
    OPTION = "OPTION"
    CONTRACT = "CONTRACT"


class Category(Enum):
    INVERSE = "inverse"
    LINEAR = "linear"
    SPOT = "spot"
    OPTION = "option"


class Direction(Enum):
    PREV = "Prev"
    NEXT = "Next"


def tf_helper(tf: str, factor_seconds: int, ns: list[int]):
    return {
        factor_seconds * n: f"{int(n * factor_seconds / 60) if n < utils.DAY else tf}"
        for n in ns
    }


interval_map = {
    **tf_helper("m", utils.MINUTE, [1, 3, 5, 15, 30]),
    **tf_helper("h", utils.HOUR, [1, 2, 4, 6, 8, 12]),
    **tf_helper("D", utils.DAY, [1]),
    **tf_helper("W", utils.WEEK, [1]),
    **tf_helper("M", utils.WEEK * 4, [1]),
}

all_intervals = list(interval_map.keys())


class _BybitBaseClient(Exchange, ABC):
    supports_extended_data = True

    _ENDPOINT = "https://api.bybit.com"
    _SANDBOX_ENDPOINT = "https://api-testnet.bybit.com"

    _WS_ENDPOINT: str
    _WS_SANDBOX_ENDPOINT: str

    _response_error = "ret_msg"
    _response_result = "result"

    @classmethod
    def get_symbol(cls, market: Market) -> str:
        return market.base + market.quote

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ws = BybitWebsocketClient(
            self._http, get_url=self._get_ws_url, on_message=self._on_message
        )
        # TODO: Fetch symbols https://bybit-exchange.github.io/docs/inverse/#t-querysymbol

    async def start_ws(self):
        self._logger.info("Connecting")
        await self._ws.connect()
        self._logger.info("Connected")

        resp = await self._ws.authenticate(self.client.api_key, self.client.api_secret)

        if resp["success"]:
            await self._ws.subscribe("user.order.contractAccount")
            # await self._ws.subscribe("user.execution.contractAccount")
            self._logger.info("Authed")
        else:
            return WebsocketError(reason="Could not authenticate")

    async def clean_ws(self):
        await self._ws.close()

    def _get_ws_url(self) -> str:
        # https://bybit-exchange.github.io/docs/futuresV2/linear/#t-websocketauthentication
        return self._WS_SANDBOX_ENDPOINT if self.client.sandbox else self._WS_ENDPOINT

    @classmethod
    def _parse_exec_v3(cls, raw: Dict):
        # {
        #     "symbol": "BITUSDT",
        #     "execFee": "0.02022",
        #     "execId": "beba036f-9fb4-59a7-84b7-2620e5d13e1c",
        #     "execPrice": "0.674",
        #     "execQty": "50",
        #     "execType": "Trade",
        #     "execValue": "33.7",
        #     "feeRate": "0.0006",
        #     "lastLiquidityInd": "RemovedLiquidity",
        #     "leavesQty": "0",
        #     "orderId": "ddbea432-2bd7-45dd-ab42-52d920b8136d",
        #     "orderLinkId": "b001",
        #     "orderPrice": "0.707",
        #     "orderQty": "50",
        #     "orderType": "Market",
        #     "stopOrderType": "UNKNOWN",
        #     "side": "Buy",
        #     "execTime": "1659057535081",
        #     "closedSize": "0"
        # }
        if raw["execType"] == "Trade":
            exec_type = ExecType.TRADE
        elif raw["execType"] == "Funding":
            exec_type = ExecType.FUNDING
        else:
            return
        symbol = raw["symbol"]
        commission = Decimal(raw["execFee"])
        price = Decimal(utils.get_multiple(raw, "execPrice"))
        qty = Decimal(raw["execQty"])

        # Unify Inverse and Linear
        contract_type = get_contract_type(symbol)
        return Execution(
            symbol=symbol,
            price=price,
            qty=qty,
            commission=commission,
            time=cls.parse_ms_dt(int(raw["execTime"])),
            side=Side.BUY if raw["side"] == "Buy" else Side.SELL,
            type=exec_type,
            inverse=contract_type == Category.INVERSE,
            settle="USDT" if contract_type == Category.LINEAR else symbol[:-3],
            market_type=MarketType.DERIVATIVES,
        )

    @classmethod
    def _parse_order_v3(cls, raw: Dict):
        # {
        #     "symbol": "BTCUSD",
        #     "orderId": "ee013d82-fafc-4504-97b1-d92aca21eedd",
        #     "side": "Buy",
        #     "orderType": "Market",
        #     "stopOrderType": "UNKNOWN",
        #     "price": "21920.00",
        #     "qty": "200",
        #     "timeInForce": "ImmediateOrCancel",
        #     "orderStatus": "Filled",
        #     "triggerPrice": "0.00",
        #     "orderLinkId": "inv001",
        #     "createdTime": "1661338622771",
        #     "updatedTime": "1661338622775",
        #     "takeProfit": "0.00",
        #     "stopLoss": "0.00",
        #     "tpTriggerBy": "UNKNOWN",
        #     "slTriggerBy": "UNKNOWN",
        #     "triggerBy": "UNKNOWN",
        #     "reduceOnly": false,
        #     "closeOnTrigger": false,
        #     "triggerDirection": 0,
        #     "leavesQty": "0",
        #     "lastExecQty": "200",
        #     "lastExecPrice": "21282.00",
        #     "cumExecQty": "200",
        #     "cumExecValue": "0.00939761"
        # }

        if raw["orderStatus"] == "Filled":
            symbol = raw["symbol"]
            contract_type = get_contract_type(symbol)

            commission = Decimal(raw["cumExecFee"])
            qty = Decimal(raw["cumExecQty"])
            value = Decimal(raw["cumExecValue"])
            price = value / qty if contract_type == Category.LINEAR else qty / value

            # Unify Inverse and Linear
            return Execution(
                symbol=symbol,
                price=price,
                qty=qty,
                reduce=raw["reduceOnly"],
                commission=commission,
                time=cls.parse_ms_dt(int(raw["createdTime"])),
                side=Side.BUY if raw["side"] == "Buy" else Side.SELL,
                type=ExecType.TRADE,
                inverse=contract_type == Category.INVERSE,
                settle="USDT" if contract_type == Category.LINEAR else symbol[:-3],
                market_type=MarketType.DERIVATIVES,
            )

    async def _on_message(self, ws: WebsocketManager, message: Dict):
        # https://bybit-exchange.github.io/docs/inverse/#t-websocketexecution
        print(message)
        topic = message.get("topic")
        if topic == "user.order.contractAccount":
            await self._on_execution(
                [self._parse_order_v3(raw) for raw in message["data"]]
            )
        elif topic == "user.execution.contractAccount":
            await self._on_execution(
                [self._parse_exec_v3(raw) for raw in message["data"]]
            )

    # https://bybit-exchange.github.io/docs/inverse/?console#t-authentication
    def _sign_request(
        self,
        method: str,
        path: str,
        headers=None,
        params: Optional[OrderedDict] = None,
        data=None,
        **kwargs,
    ):
        ts = str(int(time.time() * 1000))
        headers["X-BAPI-API-KEY"] = self.client.api_key
        headers["X-BAPI-TIMESTAMP"] = ts
        query_string = urllib.parse.urlencode(params)
        sign_str = ts + self.client.api_key + query_string
        sign = hmac.new(
            self.client.api_secret.encode("utf-8"), sign_str.encode("utf-8"), "sha256"
        ).hexdigest()
        headers["X-BAPI-SIGN"] = sign

    @classmethod
    def _check_for_error(cls, response_json: dict, response: ClientResponse):
        # https://bybit-exchange.github.io/docs/inverse/?console#t-errors
        code = utils.get_multiple(response_json, "ret_code", "retCode")
        if code != 0:
            error: Type[ResponseError] = ResponseError
            if code in (
                10003,
                10005,
                33004,
            ):  # Invalid api key, Permission denied, Api Key Expired
                error = InvalidClientError

            if code == 10006:
                error = RateLimitExceeded

            raise error(
                response=response,
                human=f'{utils.get_multiple(response_json, "ret_msg", "retMsg")}, Code: {code}',
            )

    async def _get_paginated_v3(
        self, params: Optional[dict] = None, valid: Optional[Callable] = None, **kwargs
    ) -> list[dict]:
        page = True
        results = []

        while page:
            if page is not True:
                params["cursor"] = page
            elif "cursor" in params:
                del params["cursor"]
            response = await self.get(params=params, **kwargs)

            for result in response["list"]:
                if not valid or valid(result):
                    results.append(result)
                else:
                    page = None
                    break
            else:
                page = response.get("nextPageCursor")

        return results

    async def _internal_get_balance_v5(
        self, account_type: Account, category: Optional[Category] = None
    ):
        if not category:
            category = Category.LINEAR

        wallet, tickers = await asyncio.gather(
            self.get(
                "/v5/account/wallet-balance", params={"accountType": account_type.value}
            ),
            self.get(
                "/v5/market/tickers",
                params={"category": category.value},
                sign=False,
                cache=True,
            ),
        )

        total_realized = total_unrealized = Decimal(0)
        extra_currencies: list[Amount] = []

        ticker_prices = {
            ticker["symbol"]: Decimal(ticker["lastPrice"]) for ticker in tickers["list"]
        }
        err_msg = None
        for account in wallet["list"]:
            for balance in account["coin"]:
                realized = Decimal(balance["walletBalance"])
                unrealized = Decimal(balance["equity"]) - realized
                coin = balance["coin"]
                price = 0
                if coin == "USDT":
                    if category == Category.LINEAR or not category:
                        price = Decimal(1)
                elif realized and (category == Category.INVERSE or not category):
                    price = utils.get_multiple(ticker_prices, f"{coin}USD", f"{coin}USDT")
                    if not price:
                        logging.error(
                            f"Bybit Bug: ticker prices do not contain info about {coin}:\n{ticker_prices}"
                        )
                        continue

                if category != Category.LINEAR and realized:
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

    async def _get_internal_transfers_v3(
        self, since: datetime, account: Account
    ) -> List[RawTransfer]:
        results = []

        params: dict[str, Any] = {"status": Transfer.SUCCESS.value}
        if since:
            params["startTime"] = self._parse_date(since)

        transfers = await self._get_paginated_v3(
            path="/asset/v3/private/transfer/inter-transfer/list/query", params=params
        )

        params["walletFundType"] = "ExchangeOrderWithdraw"
        withdrawals = await self._get_paginated_v3(
            path="/contract/v3/private/account/wallet/fund-records", params=params
        )

        params["walletFundType"] = "ExchangeOrderDeposit"
        deposits = await self._get_paginated_v3(
            path="/contract/v3/private/account/wallet/fund-records", params=params
        )

        for record in itertools.chain(withdrawals, deposits):
            results.append(
                RawTransfer(
                    amount=Decimal(record["amount"])
                    * (-1 if record["type"] == "ExchangeOrderWithdraw" else 1),
                    time=self.parse_ms_dt(record["execTime"]),
                    coin=record["coin"],
                    fee=None,
                    market_type=MarketType.SPOT,
                )
            )

        # {
        #     "transferId": "selfTransfer_cafc74cc-e28a-4ff6-b0e6-9e711376fc90",
        #     "coin": "USDT",
        #     "amount": "1000",
        #     "fromAccountType": "UNIFIED",
        #     "toAccountType": "SPOT",
        #     "timestamp": "1658986298000",
        #     "status": "SUCCESS"
        # }
        for transfer in transfers:
            if transfer["fromAccountType"] == account.value:
                # Withdrawal
                amount = transfer["amount"] * -1
            elif transfer["toAccountType"] == account.value:
                # Deposit
                amount = transfer["amount"]
            else:
                continue

            results.append(
                RawTransfer(
                    amount=amount,
                    time=self.parse_ts(transfer["timestamp"]),
                    coin=transfer["coin"],
                    fee=None,
                )
            )

        return results

    def _parse_date(self, date: datetime) -> int:
        return int(date.timestamp() * 1000) if date else 0


def get_contract_type(contract: str):
    if contract.endswith("USDT"):
        return Category.LINEAR
    else:
        return Category.INVERSE
