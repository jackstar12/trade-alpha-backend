import abc
import asyncio
from asyncio import Queue
from collections import deque
from typing import Dict, Optional, Deque, NamedTuple

from apscheduler.job import Job
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, and_, or_

from common.exchanges import EXCHANGES
from collector.services.baseservice import BaseService
from collector.services.dataservice import DataService, Channel, ExchangeInfo
from common.exchanges.exchangeticker import Subscription
from core import utc_now
from database.dbasync import db_all, db_unique, db_eager
from database.dbmodels import Balance
from database.dbmodels.client import Client, ClientState, ClientType
from database.dbmodels.trade import Trade
from database.enums import MarketType
from database.errors import InvalidClientError
from common.exchanges.exchangeworker import ExchangeWorker
from common.messenger import TradeSpace
from common.messenger import TableNames, Category
from database.models.market import Market


class ExchangeJob(NamedTuple):
    exchange: str
    job: Job
    deque: Deque[ExchangeWorker]


class Lock:
    async def __aenter__(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class _BalanceServiceBase(BaseService):
    client_type: ClientType

    def __init__(self,
                 *args,
                 data_service: DataService,
                 **kwargs):
        super().__init__(*args, **kwargs)

        # Public parameters

        self.data_service = data_service
        self._exchanges = EXCHANGES
        self._workers_by_id: Dict[int, ExchangeWorker] = {}
        self._updates = set()
        # self._worker_lock = Lock()

        self._all_client_stmt = select(Client).where(
            ~Client.state.in_([ClientState.ARCHIVED, ClientState.INVALID])
        )

        self._active_client_stmt = self._all_client_stmt.join(
            Trade,
            and_(
                Client.id == Trade.client_id,
                Trade.open_qty > 0
            )
        )

    @classmethod
    def is_valid(cls, worker: ExchangeWorker, category: ClientType):
        if category == ClientType.FULL and worker.supports_extended_data:
            return True
        elif category == ClientType.BASIC:
            return True

    async def _on_client_delete(self, data: dict):
        worker = self._get_existing_worker(data['id'])
        if worker:
            await self._remove_worker(worker)
        await self._messenger.pub_channel(Client,
                                          Category.REMOVED,
                                          data,
                                          client_id=data['id'])

    async def _on_client_update(self, data: dict):
        worker = self._get_existing_worker(data['id'])
        state = data['state']
        if worker:
            if state in ('archived', 'invalid') or data['type'] != self.client_type.value:
                await self._remove_worker(worker)
            #else:
            #    await worker.refresh()
        elif state != 'synchronizing' and data['type'] == self.client_type.value:
            await self.add_client_by_id(data['id'])

    def _on_client_add(self, data: dict):
        if data['type'] == self.client_type.value:
            return self.add_client_by_id(data['id'])

    async def _sub_client(self):
        await self._messenger.bulk_sub(
            TableNames.CLIENT,
            {
                Category.NEW: self._on_client_add,
                Category.UPDATE: self._on_client_update,
                Category.DELETE: self._on_client_delete,
            }
        )

    def _get_existing_worker(self, client_id: int):
        return self._workers_by_id.get(client_id)

    async def add_client_by_id(self, client_id: int):
        async with self._db_lock:
            client = await db_unique(
                self._all_client_stmt.filter_by(id=client_id),
                session=self._db
            )
        if client:
            return await self.add_client(client)
        else:
            raise ValueError(f'Invalid {client_id=} passed in')

    async def add_client(self, client: Client) -> Optional[ExchangeWorker]:
        if client:
            exchange_cls = self._exchanges.get(client.exchange)
            if exchange_cls and issubclass(exchange_cls, ExchangeWorker):
                worker = exchange_cls(client,
                                      http_session=self._http_session,
                                      data_service=self.data_service,
                                      db_maker=self._db_maker,
                                      messenger=self._messenger)
                worker = await self._add_worker(worker)
                if worker:
                    await self._messenger.pub_instance(client, Category.ADDED)
                return worker
            else:
                self._logger.error(f'Exchange class {exchange_cls} does NOT subclass ExchangeWorker')

    async def teardown(self):
        for worker in self._workers_by_id.values():
            await worker.cleanup()
        self._workers_by_id = {}

    @abc.abstractmethod
    async def _remove_worker(self, worker: ExchangeWorker):
        pass

    @abc.abstractmethod
    async def _add_worker(self, worker: ExchangeWorker):
        pass


class BasicBalanceService(_BalanceServiceBase):
    client_type = ClientType.BASIC

    def __init__(self,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self._exchange_jobs: Dict[str, ExchangeJob] = {}
        self._balance_queue = Queue()

    @staticmethod
    def reschedule(exchange_job: ExchangeJob):
        trigger = IntervalTrigger(seconds=15 // (len(exchange_job.deque) or 1), jitter=2)
        exchange_job.job.reschedule(trigger)

    async def _add_worker(self, worker: ExchangeWorker):
        if worker.client.id not in self._workers_by_id and self.is_valid(worker, ClientType.BASIC):
            self._workers_by_id[worker.client.id] = worker

            exchange_job = self._exchange_jobs[worker.exchange]
            exchange_job.deque.append(worker)
            self.reschedule(exchange_job)

    async def _remove_worker(self, worker: ExchangeWorker):
        self._workers_by_id.pop(worker.client.id, None)
        exchange_job = self._exchange_jobs[worker.exchange]
        exchange_job.deque.remove(worker)
        self.reschedule(exchange_job)
        await worker.cleanup()

    async def update_worker_queue(self, worker_queue: Deque[ExchangeWorker]):
        if worker_queue:
            worker = worker_queue[0]
            try:
                balance = await worker.intelligent_get_balance()
                if balance:
                    await self._balance_queue.put(balance)
                worker_queue.rotate()
            except InvalidClientError:
                await self._remove_worker(worker)

    async def init(self):

        await self._sub_client()

        for exchange in self._exchanges:
            exchange_queue = deque()
            job = self._scheduler.add_job(
                self.update_worker_queue,
                IntervalTrigger(seconds=3600),
                args=(exchange_queue,)
            )
            self._exchange_jobs[exchange] = ExchangeJob(exchange, job, exchange_queue)
        clients = await db_all(
            self._all_client_stmt.filter(
                or_(
                    Client.type == ClientType.BASIC,
                    Client.exchange.in_([
                        ExchangeCls.exchange for ExchangeCls in self._exchanges.values()
                        if not ExchangeCls.supports_extended_data
                    ])
                )
            ),
            session=self._db
        )
        for client in clients:
            try:
                await self.add_client(client)
            except Exception:
                # TODO: Schedule retry?
                continue

    async def run_forever(self):
        while True:
            # For efficiency reasons, balances are always grouped in 2-second intervals
            # (fewer round trips to database)
            balances = [await self._balance_queue.get()]

            await asyncio.sleep(2)
            while not self._balance_queue.empty():
                balances.append(self._balance_queue.get_nowait())

            self._db.add_all(balances)
            await self._db.commit()


class ExtendedBalanceService(_BalanceServiceBase):
    client_type = ClientType.FULL

    async def init(self):
        await self._sub_client()

        self._messenger.listen_class_all(Client)
        self._messenger.listen_class_all(Balance)
        self._messenger.listen_class_all(Trade)

        clients = await db_all(
            self._all_client_stmt.where(
                Client.type == ClientType.FULL,
                Client.exchange.in_([
                    ExchangeCls.exchange for ExchangeCls in self._exchanges.values()
                    if ExchangeCls.supports_extended_data
                ])
            ),
            session=self._db
        )
        for client in clients:
            try:
                await self.add_client(client)
            except Exception as e:
                self._logger.error('Could not add client')
        #
        return
        await asyncio.gather(
            *[
                self.add_client(client)
                for client in clients
            ],
            return_exceptions=True
        )
        pass

    async def _add_worker(self, worker: ExchangeWorker):
        self._workers_by_id[worker.client.id] = worker

        if self.is_valid(worker, ClientType.FULL):
            try:
                await worker.synchronize_positions()
                await worker.startup()
                return worker
            except InvalidClientError:
                self._logger.exception(f'Error while adding {worker.client_id=}')
                return None
            except Exception:
                self._logger.exception(f'Error while adding {worker.client_id=}')
                raise

    async def _remove_worker(self, worker: ExchangeWorker):
        self._workers_by_id.pop(worker.client_id, None)
        await worker.cleanup()
