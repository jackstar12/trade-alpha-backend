from __future__ import annotations

import asyncio
import itertools
import logging
from collections import deque
from datetime import date
from decimal import Decimal
from operator import or_
from typing import Optional
from typing import TYPE_CHECKING

import aiohttp
from sqlalchemy import select, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

import core
from common.exchanges import EXCHANGES
from common.exchanges.channel import Channel
from common.exchanges.exchange import Exchange
from common.exchanges.exchangeticker import Subscription
from common.messenger import TableNames, Category, Messenger
from core.utils import combine_time_series, MINUTE, utc_now
from database.dbasync import db_unique, db_all, db_select, db_select_all, redis
from database.dbmodels.client import Client, ClientState

# from database.dbmodels.ohlc import OHLC
from database.dbmodels.execution import Execution
from database.dbmodels.pnldata import PnlData
from database.dbmodels.trade import Trade
from database.dbmodels.transfer import Transfer
from database.enums import ExecType, MarketType
from database.errors import ClientDeletedError
from database.models.market import Market
from database.models.observer import Observer
from database.models.ohlc import OHLC
from database.models.ticker import Ticker

if TYPE_CHECKING:
    from collector.services.dataservice import DataService
    from database.dbmodels.balance import Balance

logger = logging.getLogger(__name__)


class Worker(Observer):
    def __init__(
        self,
        client: Client,
        http_session: aiohttp.ClientSession,
        db_maker: sessionmaker,
        data_service: DataService,
        messenger: Optional[Messenger] = None,
        commit=True,
    ):
        exchange_cls = EXCHANGES.get(client.exchange)

        if exchange_cls and issubclass(exchange_cls, Exchange):
            self.exchange = exchange_cls(
                client,
                http_session=http_session,
                db_maker=db_maker,
                on_execution=self._on_execution,
            )
        else:
            raise ValueError(f"Invalid exchange: {client.exchange}")

        self.client_id = client.id if commit else None
        self.messenger = messenger
        self.client: Optional[Client] = client
        self.db_lock = asyncio.Lock()
        self.db_maker = db_maker
        self.db: AsyncSession = db_maker()  # type: ignore
        self.data_service = data_service

        self._subscribed_symbols: set[str] = set()

        self._pending_execs: deque[Execution] = deque()

        self._logger = logging.getLogger(
            __name__ + f" {self.exchange} id={self.client_id}"
        )

    async def cleanup(self):
        await self.exchange.clean_ws()
        for symbol in self._subscribed_symbols:
            await self.data_service.unsubscribe(
                self.client.exchange_info,
                Subscription.get(Channel.TICKER, symbol=symbol),
                observer=self,
            )
        await self.db.close()

    async def startup(self):
        await self.exchange.start_ws()
        await self.refresh()

    async def refresh(self):
        self.client = await db_unique(
            select(Client)
            .where(Client.id == self.client_id)
            .execution_options(populate_existing=True),
            Client.currently_realized,
            (Client.open_trades, [Trade.max_pnl, Trade.min_pnl, Trade.init_balance]),
            session=self.db,
        )

        new_symbols = {trade.symbol for trade in self.client.open_trades}
        for remove in self._subscribed_symbols.difference(new_symbols):
            await self.data_service.unsubscribe(
                self.client.exchange_info,
                Subscription.get(Channel.TICKER, symbol=remove),
                observer=self,
            )
        for add in new_symbols.difference(self._subscribed_symbols):
            await self.data_service.subscribe(
                self.client.exchange_info,
                Subscription.get(Channel.TICKER, symbol=add),
                observer=self,
            )
        self._subscribed_symbols = new_symbols
        return self.client

    async def update(self, ticker: Ticker):
        async with redis.pipeline(transaction=True) as pipe:
            for trade in self.client.open_trades:
                if trade.symbol == ticker.symbol:
                    trade.update_pnl(trade.calc_upnl(ticker.price))
                    await trade.set_live_pnl(pipe)

            client = self.client
            if client.currently_realized:
                balance = client.currently_realized.clone()
                balance.time = utc_now()

                for trade in client.open_trades:
                    if (
                        trade.initial.market_type == MarketType.DERIVATIVES
                        and trade.live_pnl
                    ):
                        balance.add_amount(
                            trade.settle, unrealized=trade.live_pnl.unrealized
                        )

                if balance.extra_currencies:
                    for amount in balance.extra_currencies:
                        market = Market(base=amount.currency, quote=client.currency)
                        if not self.exchange.is_equal(market):
                            ticker = await self.data_service.get_ticker(
                                self.exchange.get_symbol(market), client.exchange_info
                            )
                            amount.rate = ticker.price if ticker else 0
                        else:
                            amount.rate = 1
                    balance.evaluate()

                await client.as_redis(pipe).set_balance(balance)
                await self.messenger.pub_instance(balance, Category.LIVE)
            await pipe.execute()

    async def intelligent_get_balance(self) -> Optional["Balance"]:
        """
        Fetch the clients balance, only saving if it makes sense to do so.
        database session to ues
        :param date:
        :return:
        new balance object
        """
        async with self.db_maker() as db:
            client = await self._get_client(
                db, options=(selectinload(Client.recent_history),)
            )
            self.client = client
            result = await self.exchange.get_balance()

            if result:
                history = client.recent_history
                if len(history) > 2:
                    # If balance hasn't changed at all, why bother keeping it?
                    if result == history[-1] == history[-2]:
                        history[-1].time = date
                        return None
                if result.error:
                    logger.error(
                        f"Error while fetching {client.id=} balance: {result.error}"
                    )
                else:
                    await client.as_redis().set_balance(result)
            return result

    async def synchronize_positions(self):
        """
        Responsible for synchronizing the client with the exchange.
        Fetches executions, transfers and additional incomes (kickback fees, etc.)

        The flow can be summarized the following way:
        - Fetch all executions and transfers that happend since last sync and all executions
        - Check the fetched ones against executions that are already in the system
        - Delete trades if they are invalid (could happen due to websocket connection loss etc.)
        - Generate balances based on valid executions
        - Generate trades including pnl data
        - Set the unrealized field of each balance
          (can't be done in advance because it depends on pnl data but creating pnl data depends on balances)
        """
        async with self.db_maker() as db:
            client: Client = await db_select(
                Client,
                Client.id == self.client_id,
                eager=[
                    (
                        Client.trades,
                        [Trade.executions, Trade.init_balance, Trade.initial],
                    ),
                    (
                        Client.open_trades,
                        [Trade.executions, Trade.init_balance, Trade.initial],
                    ),
                    Client.currently_realized,
                ],
                session=db,
            )
            client.state = ClientState.SYNCHRONIZING
            await db.commit()
            self.client = client

            since = client.last_execution_sync

            transfers, all_executions, misc = await self.exchange.get_executions(since)

            check_executions = await db_all(
                select(Execution)
                .order_by(asc(Execution.time))
                .join(Execution.trade, isouter=True)
                .join(Execution.transfer, isouter=True)
                .where(
                    or_(
                        Trade.client_id == self.client_id,
                        Transfer.client_id == self.client_id,
                    ),
                    Execution.time > since if since else True,
                ),
                session=db,
            )

            valid_until = since
            abs_exec_sum = abs_check_sum = Decimal(0)
            exec_sum = check_sum = Decimal(0)
            for execution, check in itertools.zip_longest(
                all_executions, check_executions
            ):
                if execution:
                    if not execution.qty and not execution.realized_pnl:
                        pass
                    abs_exec_sum += abs(execution.qty or execution.realized_pnl)
                    exec_sum += execution.effective_qty or execution.realized_pnl
                if check:
                    abs_check_sum += abs(check.qty or check.realized_pnl)
                    check_sum += check.effective_qty or check.realized_pnl
                if abs_exec_sum == abs_check_sum and abs_exec_sum != 0:
                    valid_until = (execution or check).time

            all_executions = (
                [e for e in all_executions if e.time > valid_until]
                if valid_until
                else all_executions
            )

            executions_by_symbol = core.groupby(all_executions, lambda e: e.symbol)

            for trade in client.trades:
                if valid_until:
                    await trade.reverse_to(valid_until, db=db)
                else:
                    await db.delete(trade)

            # if client.currently_realized and valid_until and valid_until < client.currently_realized.time and all_executions:
            #    bl = db_balance.Balance
            #    await db.execute(
            #        delete(bl).where(
            #            bl.client_id == client.id,
            #            bl.time > valid_until
            #        )
            #    )

            await db.flush()

            prev_balance = await self._update_realized_balance(db)

            real_positions = await self.exchange.get_positions()
            positions = {trade.symbol: trade.qty for trade in client.open_trades}

            if executions_by_symbol:
                for symbol, executions in executions_by_symbol.items():
                    if not symbol:
                        continue

                    should_qty = sum(
                        position.effective_qty
                        for position in real_positions.get(symbol, [])
                    )
                    open_qty = positions.get(symbol, 0)

                    while executions:
                        is_qty = sum(
                            [e.effective_qty for e in executions], start=open_qty
                        )
                        if is_qty != should_qty:
                            executions.pop(0)
                        else:
                            break

                    exec_iter = iter(executions)
                    to_exec = next(exec_iter, None)

                    # In order to avoid unnecesary OHLC data between trades being fetched
                    # we preflight the executions in a way where the executions which
                    # form a trade can be extracted.

                    while to_exec:
                        current_executions = [to_exec]

                        while open_qty != 0 and to_exec:
                            to_exec = next(exec_iter, None)
                            if to_exec:
                                current_executions.append(to_exec)
                                if to_exec.type == ExecType.TRADE:
                                    open_qty += to_exec.effective_qty

                        if open_qty:
                            # If the open_qty is not 0 there is an active trade going on
                            # -> needs to be published (unlike others which are historical)
                            pass

                        all_trades = set()
                        for item in executions:
                            current_trade = await self._add_executions(
                                db, [item], realtime=False
                            )
                            all_trades.add(current_trade)

                        for trade in all_trades:
                            dummy = Trade.from_execution(
                                trade.initial, trade.client_id, trade.init_balance
                            )
                            ohlc_data = await self.exchange.get_ohlc(
                                symbol, since=trade.open_time, to=trade.close_time
                            )
                            for item in combine_time_series(
                                ohlc_data, trade.executions[1:-1]
                            ):
                                if isinstance(item, Execution):
                                    dummy.add_execution(item)
                                    all_trades.add(current_trade)
                                elif isinstance(item, OHLC):
                                    trade.update_pnl(
                                        dummy.calc_upnl(item.open),
                                        now=item.time,
                                        extra_currencies={client.currency: item.open},
                                    )
                        to_exec = next(exec_iter, None)

            # Start Balance:
            # 11.4. 100
            # Executions
            # 10.4. +10
            # 8.4.  -20
            # 7.4.  NONE <- required because otherwise the last ones won't be accurate
            # PnlData
            # 9.4. 5U
            # New Balances
            # 10.4. 100
            # 8.4. 90
            # 7.4. 110

            balances = []
            pnl_data = await db_select_all(
                PnlData,
                PnlData.trade_id.in_(
                    execution.trade_id for execution in all_executions
                ),
                eager=[PnlData.trade],
                apply=lambda s: s.order_by(desc(PnlData.time)),
                session=db,
            )

            pnl_iter = iter(pnl_data)
            cur_pnl = next(pnl_iter, None)

            # Note that we iterate through the executions reversed because we have to reconstruct
            # the history from the only known point (which is the present)
            for prev_exec, execution, next_exec in core.prev_now_next(
                reversed(all_executions)
            ):
                execution: Execution
                prev_exec: Execution
                next_exec: Execution

                current_balance = prev_balance.clone()
                current_balance.time = execution.time
                current_balance.__realtime__ = False

                if execution.type == ExecType.TRANSFER:
                    current_balance.add_amount(
                        execution.settle, realized=-execution.effective_qty
                    )

                pnl_by_trade = {}

                if next_exec:
                    # The closest pnl from each trade should be taken into account
                    while cur_pnl and cur_pnl.time > next_exec.time:
                        if (
                            cur_pnl.time < execution.time
                            and cur_pnl.trade_id not in pnl_by_trade
                        ):
                            pnl_by_trade[cur_pnl.trade_id] = cur_pnl
                        cur_pnl = next(pnl_iter, None)

                if execution.trade and execution.trade.open_time == execution.time:
                    execution.trade.init_balance = current_balance

                if execution.net_pnl:
                    current_balance.add_amount(
                        execution.settle, realized=-execution.net_pnl
                    )

                if current_balance.extra_currencies:
                    for amount in current_balance.extra_currencies:
                        amount.rate = await self.exchange.conversion_rate(
                            Market(base=amount.currency, quote=client.currency),
                            execution.time,
                            resolution_s=5 * MINUTE,
                        )
                    current_balance.evaluate()

                # base = sum(
                #    # Note that when upnl of the current execution is included the rpnl that was realized
                #    # can't be included anymore
                #    pnl_data.unrealized
                #    if tr_id != execution.trade_id
                #    else pnl_data.unrealized - execution.net_pnl
                #    for tr_id, pnl_data in pnl_by_trade.items()
                # )
                #
                # if execution.settle != client.currency:
                #    amt = new_balance.get_amount(execution.settle)
                #    prev = current_balance.get_amount(execution.settle)
                #    amt.unrealized = amt.realized + base
                #    new_balance.unrealized = new_balance.realized + sum(
                #        # Note that when upnl of the current execution is included the rpnl that was realized
                #        # can't be included anymore
                #        pnl_data.unrealized_ccy(client.currency)
                #        if tr_id != execution.trade_id
                #        else pnl_data.unrealized_ccy(client.currency) - execution.net_pnl * execution.price
                #        for tr_id, pnl_data in pnl_by_trade.items()
                #    )
                # else:
                #    new_balance.unrealized = new_balance.realized + base

                # Don't bother adding multiple balances for executions happening as a direct series of events

                if not prev_exec or prev_exec.time != execution.time:
                    balances.append(current_balance)
                prev_balance = current_balance

            db.add_all(balances)
            db.add_all(transfers)
            await db.flush()

            client.last_execution_sync = client.last_transfer_sync = core.utc_now()
            client.state = ClientState.OK

            await db.commit()

            redis_client = self.client.as_redis()
            await redis_client.set_balance(client.currently_realized)

            if all_executions:
                await redis_client.set_last_exec(all_executions[-1].time)

    async def _get_client(self, db: AsyncSession, options=None) -> Client:
        client = await db.get(Client, self.client_id, options=options)
        if client is None:
            await self.messenger.pub_channel(
                TableNames.CLIENT, Category.DELETE, obj={"id": self.client_id}
            )
            raise ClientDeletedError()
        return client

    async def _update_realized_balance(self, db: AsyncSession):
        balance = await self.exchange.get_balance(upnl=False)

        if balance:
            client = await self._get_client(db)
            client.currently_realized = balance
            spot_trades = await db_all(
                select(Trade)
                .where(
                    Trade.client_id == client.id,
                    Trade.is_open,
                    Execution.market_type == MarketType.SPOT,
                )
                .join(Trade.initial),
                session=db,
            )

            for trade in spot_trades:
                realized = balance.get_realized(
                    ccy=self.exchange.get_market(trade.symbol).base
                )
                trade.open_qty = realized
                if realized > trade.qty:
                    trade.qty = realized

            for amount in balance.extra_currencies:
                if amount.currency not in spot_trades:
                    pass

            db.add(balance)
            return balance

    async def _on_execution(self, execution: Execution | list[Execution]):
        if not isinstance(execution, list):
            execution = [execution]
        try:
            trade = await self._add_executions(self.db, execution, realtime=True)
            self._logger.debug(f"Added executions {execution} {trade}")
            await self.db.commit()
            await self.refresh()
            return
        except Exception:
            self._logger.exception("Error while adding executions")

    async def _add_executions(
        self, db: AsyncSession, executions: list[Execution], realtime=True
    ):
        client = await self._get_client(db)
        current_balance = client.currently_realized

        if executions:
            active_trade: Optional[Trade] = None

            async def get_trade(execution: Execution) -> Optional[Trade]:
                stmt = (
                    select(Trade)
                    .where(
                        # Trade.symbol.like(f'{symbol}%'),
                        Trade.client_id == self.client_id,
                        Execution.symbol == execution.symbol,
                        Execution.market_type == execution.market_type,
                    )
                    .join(Trade.initial)
                )

                # The distinguishing here is pretty important because Liquidation Execs can happen
                # after a trade has been closed on paper (e.g. insurance fund on binance). These still have to be
                # attributed to the corresponding trade.
                if execution.type in (
                    ExecType.TRADE,
                    ExecType.TRANSFER,
                    ExecType.FUNDING,
                ):
                    stmt = stmt.where(Trade.is_open)
                elif execution.type in (ExecType.FUNDING, ExecType.LIQUIDATION):
                    stmt = stmt.order_by(desc(Trade.open_time))
                trades = await db_all(
                    stmt,
                    Trade.executions,
                    Trade.init_balance,
                    Trade.initial,
                    Trade.max_pnl,
                    Trade.min_pnl,
                    session=db,
                )

                if len(trades) > 1:
                    for trade in trades:
                        if execution.reduce and execution.side != trade.side:
                            return trade
                    return trades[0]
                elif len(trades) == 1:
                    return trades[0]

            for execution in executions:
                if self.exchange.exclude_from_trade(execution):
                    continue

                active_trade = await get_trade(execution)

                execution.__realtime__ = realtime
                db.add(execution)

                if active_trade:
                    # Update existing trade
                    active_trade.__realtime__ = realtime
                    new_trade = active_trade.add_execution(execution, current_balance)
                    if new_trade:
                        db.add(new_trade)
                        active_trade = new_trade
                else:
                    active_trade = Trade.from_execution(
                        execution, self.client_id, current_balance
                    )
                    db.add(active_trade)

                active_trade.__realtime__ = realtime

                try:
                    await db.flush()
                except StaleDataError:
                    self._logger.exception(f"Error while adding execution {execution}")
                    pass
                if not realtime:
                    if execution.settle:
                        spot_trade = await db_unique(
                            select(Trade)
                            .where(
                                Trade.is_open,
                                Trade.symbol
                                == self.exchange.get_symbol(
                                    Market(base=execution.settle, quote=client.currency)
                                ),
                                Execution.market_type == MarketType.SPOT,
                            )
                            .join(Trade.initial),
                            session=db,
                        )
                        if spot_trade:
                            spot_trade.open_qty += execution.net_pnl
                            spot_trade.qty = max(spot_trade.qty, spot_trade.open_qty)
                    else:
                        pass

            if realtime:
                # Updating LAST_EXEC is siginificant for caching
                # asyncio.create_task(
                #     self.client.as_redis().set_last_exec(executions[-1].time)
                # )
                await self.client.as_redis().invalidate_cache()

                await self._update_realized_balance(db)

            return active_trade

    def __repr__(self):
        return f"<Worker exchange={self.exchange} client_id={self.client_id}>"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
