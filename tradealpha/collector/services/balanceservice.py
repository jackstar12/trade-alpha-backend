import abc
import asyncio
import logging
from asyncio import Queue
from collections import deque
from typing import Dict, Optional, Deque, NamedTuple, Type

from aioredis.client import Pipeline
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect

from tradealpha.collector.services.baseservice import BaseService
from tradealpha.collector.services.dataservice import DataService, Channel
from tradealpha.common import utils, customjson
from tradealpha.common.dbasync import db_all, db_unique, db_eager, async_maker
from tradealpha.common.dbmodels.chapter import Chapter
from tradealpha.common.dbmodels.client import Client
from tradealpha.common.dbmodels.journal import Journal
from tradealpha.common.dbmodels.serializer import Serializer
from tradealpha.common.dbmodels.trade import Trade
from tradealpha.common.errors import InvalidClientError, ResponseError
from tradealpha.common.exchanges.exchangeworker import ExchangeWorker
from tradealpha.common.messenger import ClientUpdate
from tradealpha.common.messenger import NameSpace, Category


class ExchangeJob(NamedTuple):
    exchange: str
    job: Job
    deque: Deque[ExchangeWorker]


class _BalanceServiceBase(BaseService):
    def __init__(self,
                 *args,
                 data_service: DataService,
                 exchanges: dict[str, Type[ExchangeWorker]] = None,
                 rekt_threshold: float = 2.5,
                 data_path: str = '',
                 **kwargs):
        super().__init__(*args, **kwargs)

        # Public parameters
        self.rekt_threshold = rekt_threshold
        self.data_path = data_path
        self.backup_path = self.data_path + 'backup/'

        self.data_service = data_service
        self._exchanges = exchanges
        self._workers_by_id: Dict[int, ExchangeWorker] = {}

        self._all_client_stmt = db_eager(
            select(Client).filter(
                Client.archived == False,
                Client.invalid == False
            ),
            Client.discord_user,
            Client.currently_realized,
            Client.recent_history,
            Client.trades,
            (Client.open_trades, [Trade.max_pnl, Trade.min_pnl]),
            (Client.journals, (Journal.current_chapter, Chapter.balances))
        )
        self._active_client_stmt = self._all_client_stmt.join(
            Trade,
            and_(
                Client.id == Trade.client_id,
                Trade.open_qty > 0
            )
        )

        self._db: Optional[AsyncSession] = None

    def __exit__(self):
        self._db.sync_session.close()

    async def _initialize_positions(self):
        await self._messenger.sub_channel(NameSpace.CLIENT, sub=Category.NEW, callback=self._on_client_add,
                                          pattern=True)

        await self._messenger.sub_channel(NameSpace.CLIENT, sub=Category.DELETE, callback=self._on_client_delete,
                                          pattern=True)

        await self._messenger.sub_channel(NameSpace.CLIENT, sub=Category.UPDATE, callback=self._on_client_update,
                                          pattern=True)

    def _on_client_delete(self, data: Dict):
        self._remove_worker_by_id(data['id'])

    async def _on_client_add(self, data: Dict):
        await self._add_client_by_id(data['id'])

    async def _on_client_update(self, data: Dict):
        edit = ClientUpdate(**data)
        worker = self._get_existing_worker(edit.id)
        if worker:
            await self._db.refresh(worker.client)
        if edit.archived or edit.invalid:
            self._remove_worker(worker)
        elif edit.archived is False or edit.invalid is False:
            await self._add_client_by_id(edit.id)
        if edit.premium is not None:
            self._remove_worker(worker)
            await self._add_client_by_id(edit.id)

    def _remove_worker_by_id(self, client_id: int):
        self._remove_worker(self._get_existing_worker(client_id))

    def _get_existing_worker(self, client_id: int):
        return (
            self._workers_by_id.get(client_id)
        )

    async def _add_client_by_id(self, client_id: int, extended=True):
        await self.add_client(
            await db_unique(self._all_client_stmt.filter_by(id=client_id), session=self._db)
        )

    async def add_client(self, client: Client) -> Optional[ExchangeWorker]:
        exchange_cls = self._exchanges.get(client.exchange)
        if exchange_cls and issubclass(exchange_cls, ExchangeWorker):
            worker = exchange_cls(client,
                                  self._http_session,
                                  self._db,
                                  self._messenger,
                                  self.rekt_threshold)
            await self._add_worker(worker)
            return worker
        else:
            self._logger.error(
                f'CRITICAL: Exchange class {exchange_cls} for exchange {client.exchange} does NOT subclass ClientWorker')

    @abc.abstractmethod
    def _remove_worker(self, worker: ExchangeWorker):
        pass

    @abc.abstractmethod
    async def _add_worker(self, worker: ExchangeWorker):
        pass


class BasicBalanceService(_BalanceServiceBase):

    def __init__(self,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self._exchange_jobs: Dict[str, ExchangeJob] = {}
        self._balance_queue = Queue()

    @staticmethod
    def reschedule(exchange_job: ExchangeJob):
        trigger = IntervalTrigger(seconds=15 // (len(exchange_job.deque) or 1))
        exchange_job.job.reschedule(trigger)

    async def _add_worker(self, worker: ExchangeWorker):
        if worker.client.id not in self._workers_by_id:
            self._workers_by_id[worker.client.id] = worker

            exchange_job = self._exchange_jobs[worker.exchange]
            exchange_job.deque.append(worker)
            self.reschedule(exchange_job)

    def _remove_worker(self, worker: ExchangeWorker):
        self._workers_by_id.pop(worker.client.id,
                                None)
        exchange_job = self._exchange_jobs[worker.exchange]
        exchange_job.deque.remove(worker)
        self.reschedule(exchange_job)

    async def update_worker_queue(self, worker_queue: Deque[ExchangeWorker]):
        if worker_queue:
            worker = worker_queue[0]
            balance = await worker.intelligent_get_balance(commit=False)
            if balance:
                await self._balance_queue.put(balance)
            worker_queue.rotate()

    async def init(self):

        for exchange in self._exchanges:
            exchange_queue = deque()
            job = self._scheduler.add_job(
                self.update_worker_queue,
                IntervalTrigger(seconds=3600),
                args=(exchange_queue,)
            )
            self._exchange_jobs[exchange] = ExchangeJob(exchange, job, exchange_queue)

        for client in await db_all(
                self._all_client_stmt.filter(
                    or_(
                        Client.is_premium == False,
                        Client.exchange.in_([
                            ExchangeCls.exchange for ExchangeCls in self._exchanges.values()
                            if not ExchangeCls.supports_extended_data
                        ])
                    )
                ),
                session=self._db
        ):
            await self.add_client(client)

    async def run_forever(self):
        while True:
            # For efficiency reasons, balances are always grouped in 2-second intervals
            # (less commits to database)
            balances = [await self._balance_queue.get()]

            await asyncio.sleep(2)
            while not self._balance_queue.empty():
                balances.append(self._balance_queue.get_nowait())

            self._db.add_all(balances)
            await self._db.commit()


class ExtendedBalanceService(_BalanceServiceBase):

    async def init(self):

        await self._messenger.sub_channel(NameSpace.TRADE, sub=Category.UPDATE, callback=self._on_trade_update,
                                          pattern=True)
        await self._messenger.sub_channel(NameSpace.TRADE, sub=Category.NEW, callback=self._on_trade_delete,
                                          pattern=True)

        await self._messenger.sub_channel(NameSpace.TRADE, sub=Category.FINISHED, callback=self._on_trade_delete,
                                          pattern=True)

        for client in await db_all(
                self._all_client_stmt.filter(
                    Client.is_premium == True,
                    Client.exchange.in_([
                        ExchangeCls.exchange for ExchangeCls in self._exchanges.values()
                        if ExchangeCls.supports_extended_data
                    ])
                ),
                session=self._db
        ):
            await self.add_client(client)

    async def _on_client_update(self, data: Dict):
        edit = ClientUpdate(**data)
        worker = self._get_existing_worker(edit.id)
        if worker:
            await self._db.refresh(worker.client)
        if edit.archived or edit.invalid:
            self._remove_worker(worker)
        elif edit.archived is False or edit.invalid is False:
            await self._add_client_by_id(edit.id)
        if edit.premium is not None:
            self._remove_worker(worker)
            await self._add_client_by_id(edit.id)

    async def _on_trade_new(self, data: Dict):
        worker = await self.get_worker(data['client_id'], create_if_missing=True)
        if worker:
            await self._db.refresh(worker.client)
            # Trade is already contained in session because of ^^, no SQL will be emitted
            new = await self._db.get(Trade, data['trade_id'])
            await self.data_service.subscribe(worker.client.exchange, Channel.TICKER, symbol=new.symbol)

    async def _on_trade_update(self, data: Dict):
        client_id = data['client_id']
        trade_id = data['id']

        worker = await self.get_worker(client_id, create_if_missing=True)
        if worker:
            client = await self._db.get(Client, worker.client_id)
            for trade in client.open_trades:
                if trade.id == trade_id:
                    await self._db.refresh(trade)

    async def _on_trade_delete(self, data: Dict):
        worker = await self.get_worker(data['client_id'], create_if_missing=False)
        if worker:
            await self._db.refresh(worker.client)
            if len(worker.client.open_trades) == 0:
                symbol = data['symbol']
                # If there are no trades on the same exchange matching the deleted symbol, there is no need to keep it subscribed
                unsubscribe_symbol = all(
                    trade.symbol != symbol
                    for cur_worker in self._workers_by_id.values() if
                    cur_worker.client.exchange == worker.client.exchange
                    for trade in cur_worker.client.open_trades
                )
                if unsubscribe_symbol:
                    await self.data_service.unsubscribe(worker.client.exchange, Channel.TICKER, symbol=symbol)
                self._remove_worker(worker)

    async def _add_worker(self, worker: ExchangeWorker):
        workers = self._workers_by_id
        if worker.client.id not in workers:

            try:
                await worker.synchronize_positions()
                await worker.connect()
            except InvalidClientError:
                return None
            except ResponseError:
                self._logger.exception(f'Error while adding {worker.client_id=}')
                return None

            workers[worker.client.id] = worker

            def worker_callback(category, sub_category):
                async def callback(worker: ExchangeWorker, obj: Serializer):
                    self._messenger.pub_channel(category, sub=sub_category,
                                                channel_id=worker.client_id,
                                                obj=await obj.serialize(full=False))

                return callback

            worker.set_trade_update_callback(
                worker_callback(NameSpace.TRADE, Category.UPDATE)
            )
            worker.set_trade_callback(
                worker_callback(NameSpace.TRADE, Category.NEW)
            )
            worker.set_balance_callback(
                worker_callback(NameSpace.BALANCE, Category.NEW)
            )

    def _remove_worker(self, worker: ExchangeWorker):
        asyncio.create_task(worker.disconnect())
        self._workers_by_id.pop(worker.client.id, None)

    async def get_worker(self, client_id: int, create_if_missing=True) -> ExchangeWorker:
        if client_id:
            worker = self._workers_by_id.get(client_id)
            if not worker and create_if_missing:
                await self._add_client_by_id(client_id)
            return worker

    async def run_forever(self):
        while True:
            balances = []
            new_balances = []

            for worker in self._workers_by_id.values():
                new = False
                client = worker.client
                for trade in client.open_trades:
                    ticker = self.data_service.get_ticker(trade.symbol, client.exchange)
                    if ticker:
                        trade.update_pnl(ticker.price, realtime=True, extra_currencies={'USD': ticker.price})
                        if inspect(trade.max_pnl).transient or inspect(trade.min_pnl).transient:
                            new = True
                balance = client.evaluate_balance(self._redis)
                if balance:
                    balances.append(balance)
                    # TODO: Introduce new mechanisms determining when to save
                    if new:
                        new_balances.append(balance)
            if new_balances:
                self._db.add_all(new_balances)
                await self._db.commit()
            if balances:
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe: Pipeline = pipe
                    s = await balance.serialize(data=False, full=False)
                    for balance in balances:
                        await pipe.hset(
                            name=utils.join_args(NameSpace.CLIENT, balance.client_id),
                            key=NameSpace.BALANCE.value,
                            value=customjson.dumps(await balance.serialize(data=False, full=False))
                        )
                    await pipe.execute()
            await asyncio.sleep(0.2)