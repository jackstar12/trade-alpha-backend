import abc
import asyncio
from asyncio import Queue
from collections import deque
from typing import Dict, Optional, Deque, NamedTuple

from apscheduler.job import Job
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, and_, or_

from lib.exchanges import EXCHANGES
from collector.services.baseservice import BaseService
from collector.services.dataservice import DataService
from lib.db.dbasync import db_all, db_unique
from lib.db.models import Balance
from lib.db.models.client import Client, ClientState, ClientType
from lib.db.models.trade import Trade
from lib.db.errors import InvalidClientError
from lib.exchanges.worker import Worker
from lib.messenger import TableNames, Category


class ExchangeJob(NamedTuple):
    exchange: str
    job: Job
    deque: Deque[Worker]


class Lock:
    async def __aenter__(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class _BalanceServiceBase(BaseService):
    client_type: ClientType

    def __init__(self, *args, data_service: DataService, **kwargs):
        super().__init__(*args, **kwargs)

        # Public parameters

        self.data_service = data_service
        self._exchanges = EXCHANGES
        self._workers_by_id: Dict[int, Worker] = {}
        self._updates = set()
        # self._worker_lock = Lock()

        self._all_client_stmt = select(Client).where(
            ~Client.state.in_([ClientState.ARCHIVED, ClientState.INVALID])
        )

        self._active_client_stmt = self._all_client_stmt.join(
            Trade, and_(Client.id == Trade.client_id, Trade.open_qty > 0)
        )

    @classmethod
    def is_valid(cls, worker: Worker, category: ClientType):
        if category == ClientType.FULL and worker.exchange.supports_extended_data:
            return True
        elif category == ClientType.BASIC:
            return True

    async def _on_client_delete(self, data: dict):
        self._logger.debug(f"Client deleted: {data}")
        worker = self.get_worker(data["id"])
        if worker:
            await self.remove_worker(worker)

    async def _on_client_update(self, data: dict):
        worker = self.get_worker(data["id"])
        state = data["state"]
        if worker:
            if (
                state in ("archived", "invalid")
                or data["type"] != self.client_type.value
            ):
                await self.remove_worker(worker)
        elif state != "synchronizing" and data["type"] == self.client_type.value:
            await self.add_client_by_id(data["id"])

    def _on_client_add(self, data: dict):
        if data["type"] == self.client_type.value:
            return self.add_client_by_id(data["id"])

    async def _sub_client(self):
        await self._messenger.bulk_sub(
            TableNames.CLIENT,
            {
                Category.NEW: self._on_client_add,
                Category.UPDATE: self._on_client_update,
                Category.DELETE: self._on_client_delete,
            },
        )

    def get_worker(self, client_id: int):
        return self._workers_by_id.get(client_id)

    async def add_client_by_id(self, client_id: int):
        async with self._db_lock:
            client = await db_unique(
                self._all_client_stmt.filter_by(id=client_id), session=self._db
            )
        if client:
            return await self.add_client(client)
        else:
            raise ValueError(f"Invalid {client_id=} passed in")

    async def add_client(self, client: Client) -> Optional[Worker]:
        if client:
            worker = Worker(
                client,
                http_session=self._http_session,
                db_maker=self._db_maker,
                data_service=self.data_service,
                messenger=self._messenger,
            )

            if self.is_valid(worker, self.client_type):
                self._workers_by_id[worker.client.id] = worker
                worker = await self._init_worker(worker)
                if worker:
                    await self._messenger.pub_instance(client, Category.ADDED)
                return worker

    async def remove_worker(self, worker: Worker):
        if worker:
            await self._remove_worker(worker)
            self._workers_by_id.pop(worker.client_id, None)
            await worker.cleanup()
            await self._messenger.pub_instance(worker.client, Category.REMOVED)

    async def teardown(self):
        for worker in self._workers_by_id.values():
            await worker.cleanup()
        self._workers_by_id = {}

    async def _remove_worker(self, worker: Worker):
        pass

    @abc.abstractmethod
    async def _init_worker(self, worker: Worker):
        pass


class BasicBalanceService(_BalanceServiceBase):
    client_type = ClientType.BASIC

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exchange_jobs: Dict[str, ExchangeJob] = {}
        self._balance_queue = Queue()

    @staticmethod
    def reschedule(exchange_job: ExchangeJob):
        trigger = IntervalTrigger(
            seconds=15 // (len(exchange_job.deque) or 1), jitter=2
        )
        exchange_job.job.reschedule(trigger)

    async def _init_worker(self, worker: Worker):
        exchange_job = self._exchange_jobs[worker.exchange]
        exchange_job.deque.append(worker)
        self.reschedule(exchange_job)

    async def _remove_worker(self, worker: Worker):
        exchange_job = self._exchange_jobs[worker.exchange]
        exchange_job.deque.remove(worker)
        self.reschedule(exchange_job)

    async def update_worker_queue(self, worker_queue: Deque[Worker]):
        if worker_queue:
            worker = worker_queue[0]
            try:
                balance = await worker.intelligent_get_balance()
                if balance:
                    await self._balance_queue.put(balance)
                worker_queue.rotate()
            except InvalidClientError:
                await self.remove_worker(worker)

    async def init(self):
        await self._sub_client()

        for exchange in self._exchanges:
            exchange_queue = deque()
            job = self._scheduler.add_job(
                self.update_worker_queue,
                IntervalTrigger(seconds=3600),
                args=(exchange_queue,),
            )
            self._exchange_jobs[exchange] = ExchangeJob(exchange, job, exchange_queue)
        clients = await db_all(
            self._all_client_stmt.filter(
                or_(
                    Client.type == ClientType.BASIC,
                    Client.exchange.in_(
                        [
                            ExchangeCls.exchange
                            for ExchangeCls in self._exchanges.values()
                            if not ExchangeCls.supports_extended_data
                        ]
                    ),
                )
            ),
            session=self._db,
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
                Client.exchange.in_(
                    [
                        ExchangeCls.exchange
                        for ExchangeCls in self._exchanges.values()
                        if ExchangeCls.supports_extended_data
                    ]
                ),
            ),
            session=self._db,
        )
        for client in clients:
            try:
                await self.add_client(client)
            except Exception:
                self._logger.error("Could not add client")
        #
        return
        await asyncio.gather(
            *[self.add_client(client) for client in clients], return_exceptions=True
        )
        pass

    async def _init_worker(self, worker: Worker):
        try:
            await worker.synchronize_positions()
            await worker.startup()
            return worker
        except InvalidClientError:
            self._logger.exception(f"Error while adding {worker.client_id=}")
            return None
        except Exception:
            self._logger.exception(f"Error while adding {worker.client_id=}")
            raise
