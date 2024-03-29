from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Callable, Optional, Type, TypeVar, Generic, Any

import sqlalchemy.orm
from aioredis import Redis
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import object_session
from sqlalchemy.orm.util import identity_key

from lib import utils
from lib import json as customjson
from lib.db.models import Client, Balance, Chapter, Event, EventEntry, Execution
from lib.db.models.action import Action
from lib.db.models.alert import Alert
from lib.db.models.authgrant import AuthGrant, ChapterGrant, TradeGrant
from lib.db.models.editing import Journal
from lib.db.models.mixins.serializer import Serializer
from lib.db.models.pnldata import PnlData
from lib.db.models.trade import Trade
from lib.db.models.transfer import Transfer
from lib.db.models.user import User
from lib.db.dbsync import BaseMixin
from lib.models import OrmBaseModel
from lib.db.redis import TableNames

TTable = TypeVar("TTable", bound=BaseMixin)

by_names = {}


@dataclass
class NameSpace(Generic[TTable]):
    parent: "Optional[NameSpace]"
    name: "str"
    table: "Type[TTable]"
    model: Type[OrmBaseModel]
    id: Optional[str]

    @classmethod
    def from_table(
        cls,
        table: Type[TTable],
        parent: Optional["Optional[NameSpace]"] = None,
        model: Optional[Type[OrmBaseModel]] = None,
        children: Optional[list[NameSpace]] = None,
    ):
        new = cls(
            parent=parent,
            name=table.__tablename__,
            table=table,
            id=f"{table.__tablename__}_id",
            model=model or table.__model__,
        )
        by_names[new.name] = new
        if children:
            for child in children:
                child.parent = new
        return new

    def get_ids(self, instance: TTable):
        if not instance:
            return {}
            # return self.parent.get_ids(instance)
        result = {self.id: instance.id}
        if self.parent:
            parent_id = getattr(instance, self.parent.id, None)
            if parent_id:
                result[self.parent.id] = getattr(instance, self.parent.id)
                try:
                    parent_instance = getattr(instance, self.parent.name, None)
                except InvalidRequestError:
                    parent_instance = None
                if not parent_instance:
                    session: sqlalchemy.orm.Session = object_session(instance)
                    if session:
                        parent_instance = session.identity_map.get(
                            identity_key(self.parent.table, parent_id)
                        )
                result |= self.parent.get_ids(parent_instance)
        return result

    def format(self, *add, **ids):
        pattern = False
        for id in self.all_ids:
            if id not in ids:
                ids[id] = "*"
                pattern = True
        return self.fill(*add).format(**ids), pattern

    @property
    def all_ids(self):
        if self.parent:
            return [self.id, *self.parent.all_ids]
        return [self.id]

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.fill()

    def fill(self, *add):
        return utils.join_args(self.parent, self.name, *add, "{" + self.id + "}")


class TradeSpace(NameSpace):
    FINISHED = "finished"


class EventSpace(NameSpace[Event]):
    def get_ids(self, instance: Event):
        return {"user_id": instance.owner_id, "event_id": instance.id}

    START = "start"
    REGISTRATION_START = "registration-start"
    END = "end"
    REGISTRATION_END = "registration-end"


class ChapterSpace(NameSpace[Chapter]):
    PUBLISH = "publish"


NameSpace.from_table(
    User,
    children=[
        NameSpace.from_table(ChapterGrant),
        NameSpace.from_table(
            Client,
            children=[
                NameSpace.from_table(Balance),
                TradeSpace.from_table(Trade, children=[NameSpace.from_table(PnlData)]),
                NameSpace.from_table(Transfer),
                NameSpace.from_table(Execution),
            ],
        ),
        NameSpace.from_table(Alert),
        EventSpace.from_table(Event, children=[NameSpace.from_table(EventEntry)]),
        NameSpace.from_table(Journal, children=[ChapterSpace.from_table(Chapter)]),
        NameSpace.from_table(Action),
        NameSpace.from_table(
            AuthGrant,
            children=[
                NameSpace.from_table(ChapterGrant),
                NameSpace.from_table(TradeGrant),
            ],
        ),
    ],
)


class Category(Enum):
    NEW = "new"
    DELETE = "delete"
    UPDATE = "update"
    FINISHED = "finished"
    LIVE = "live"
    ADDED = "added"
    REMOVED = "removed"

    REKT = "rekt"


class Word(Enum):
    TIMESTAMP = "ts"


class ClientUpdate(BaseModel):
    id: int
    archived: Optional[bool]
    invalid: Optional[bool]
    premium: Optional[bool]


NameSpaceInput = NameSpace | TableNames | Type[BaseMixin] | Any


class Messenger:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._pubsub = self._redis.pubsub()
        self._listening = False
        self._logger = logging.getLogger("Messenger")

    def _wrap(self, coro, namespace: NameSpace, rcv_event=False):
        @wraps(coro)
        def wrapper(event: dict, *args, **kwargs):
            if rcv_event:
                data = event
            else:
                data = customjson.loads(event["data"], parse_decimal=False)
            asyncio.create_task(
                utils.return_unknown_function(
                    coro,
                    # namespace.model(**data) if namespace.model else data,
                    data,
                    *args,
                    **kwargs,
                )
            )

        return wrapper

    async def listen(self):
        self._listening = True
        self._logger.info("Started Listening.")
        async for msg in self._pubsub.listen():
            self._logger.debug(msg)
        self._logger.info("Stopped Listening.")
        self._listening = False

    async def sub(self, pattern=False, **kwargs):
        self._logger.debug(f"Subscribing {pattern=} {kwargs=}")
        if pattern:
            await self._pubsub.psubscribe(**kwargs)
        else:
            await self._pubsub.subscribe(**kwargs)
        if not self._listening:
            asyncio.create_task(self.listen())

    async def unsub(self, channel: str, is_pattern=False):
        if is_pattern:
            await self._pubsub.punsubscribe(channel)
        else:
            await self._pubsub.unsubscribe(channel)

    # async def dec_sub(self, namespace: MessengerNameSpace, topic: Any, **ids):
    #    def wrapper(callback):

    @classmethod
    def get_namespace(cls, name: NameSpaceInput):
        if isinstance(name, NameSpace):
            return name
        elif isinstance(name, Enum):
            return by_names.get(name.value)
        elif hasattr(name, "__tablename__"):
            return by_names.get(name.__tablename__)
        return by_names.get(str(name))

    def sub_channel(
        self, namespace: NameSpaceInput, topic: Any, callback: Callable, **ids
    ):
        return self.bulk_sub(self.get_namespace(namespace), {topic: callback}, **ids)

    def unsub_channel(self, namespace: NameSpaceInput, topic: Any, **ids):
        channel, pattern = self.get_namespace(namespace).format(topic, **ids)
        return self.unsub(channel=channel, is_pattern=pattern)

    def pub_instance(self, instance: Serializer, topic: Any):
        ns = self.get_namespace(instance.__tablename__)
        return self.pub_channel(ns, topic, instance.serialize(), **ns.get_ids(instance))

    async def pub_channel(
        self, namespace: NameSpaceInput, topic: Any, obj: object, **ids
    ):
        channel, pattern = self.get_namespace(namespace).format(topic, **ids)
        logging.debug(f"Pub: {channel=}")
        ret = await self._redis.publish(channel, customjson.dumps(obj))
        return ret

    # user:*:client:*:transfer:new
    # user:*:client:*:balance:new
    # user:*:client:455:trade:*:update
    # user:*:client:455:trade:new
    # user:*:client:*:trade:new
    # user:*:event:*:start
    # user:*:event:23:start
    # user:234f-345k:editing:23:chapter:new
    async def bulk_sub(
        self, namespace: NameSpaceInput, topics: dict[Any, Callable], **ids
    ):
        subscription = {}
        pattern = False
        namespace = self.get_namespace(namespace)
        for topic, callback in topics.items():
            channel, pattern = namespace.format(topic, **ids)
            subscription[channel] = self._wrap(callback, namespace)
        await self.sub(pattern=pattern, **subscription)

    async def setup_waiter(self, channel: str, is_pattern=False, timeout=0.25):
        fut = asyncio.get_running_loop().create_future()
        await self.sub(pattern=is_pattern, **{channel: fut.set_result})

        async def wait():
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.exceptions.TimeoutError:
                return False
            finally:
                await self.unsub(channel, is_pattern)

        return wait

    def listen_class(
        self,
        target_cls: Type[Serializer],
        identifier: str,
        namespace: NameSpace[Type[Serializer]],
        sub: Category | str,
        condition: Optional[Callable[[Serializer], bool]] = None,
    ):
        @event.listens_for(target_cls, identifier)
        def handler(_mapper, _connection, target: target_cls):
            realtime = getattr(target, "__realtime__", None)
            logging.debug(f"Listen: {sub=} {target=} {realtime=}")
            if realtime is not False and (not condition or condition(target)):
                asyncio.create_task(
                    self.pub_channel(
                        namespace,
                        sub,
                        obj=jsonable_encoder(target.serialize(include_none=False)),
                        **namespace.get_ids(target),
                    )
                )

    def listen_class_all(
        self, target_cls: Type[Serializer], namespace: Optional[NameSpace] = None
    ):
        if not namespace:
            namespace = self.get_namespace(target_cls.__tablename__)

        if target_cls is Trade:

            def is_finished(trade: Trade):
                return not trade.is_open

            self.listen_class(
                target_cls,
                "after_update",
                namespace,
                TradeSpace.FINISHED,
                condition=is_finished,
            )

        if target_cls is Chapter:
            self.listen_class(
                target_cls, "after_insert", namespace, ChapterSpace.PUBLISH
            )
            return

        self.listen_class(target_cls, "after_insert", namespace, Category.NEW)
        self.listen_class(target_cls, "after_update", namespace, Category.UPDATE)
        self.listen_class(target_cls, "after_delete", namespace, Category.DELETE)
