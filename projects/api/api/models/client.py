from datetime import datetime
from typing import Dict, Optional, TypedDict

from fastapi import Query
from pydantic import UUID4

import lib.db.models.client as qmxin
from api.models.template import TemplateInfo
from api.models.transfer import Transfer
from lib.models.trade import BasicTrade
from lib.db.models.client import ClientType, ClientState
from lib.db.enums import IntervalType
from lib.models import BaseModel, OutputID, InputID
from lib.models.balance import Balance
from lib.models.client import ClientCreate, ClientApiInfo
from lib.models.eventinfo import EventBasicInfo
from lib.models.interval import Interval, FullInterval


def get_query_params(
    client_id: set[InputID] = Query(default=[]),
    currency: str = Query(default=None),
    since: datetime = Query(default=None),
    to: datetime = Query(default=None),
    order: str = Query(default="asc"),
):
    return qmxin.ClientQueryParams(
        client_ids=client_id, currency=currency, since=since, to=to, order=order
    )


class ClientCreateBody(ClientCreate):
    pass


class ClientCreateResponse(BaseModel):
    token: str
    balance: Balance


class ClientConfirm(BaseModel):
    token: str


class ClientEdit(BaseModel):
    name: Optional[str]
    state: Optional[ClientState]
    type: Optional[ClientType]
    trade_template_id: Optional[InputID]
    api: Optional[ClientApiInfo]


class ClientInfo(BaseModel):
    id: OutputID
    user_id: Optional[UUID4]
    discord_user_id: Optional[OutputID]
    exchange: str
    name: Optional[str]
    type: ClientType
    state: ClientState
    currency: str
    trade_template_id: Optional[OutputID]
    currently_realized: Optional[Balance]

    class Config:
        orm_mode = True


class Test(TypedDict):
    name: str


class ClientDetailed(ClientInfo):
    # More detailed information
    created_at: datetime
    last_edited: Optional[datetime]
    subaccount: Optional[str]
    api_key: str
    extra: Optional[Dict]

    # Relations
    trade_template: Optional[TemplateInfo]
    events: Optional[list[EventBasicInfo]]


class _Common(BaseModel):
    total: FullInterval
    transfers: list[Transfer]


class ClientOverviewCache(_Common):
    id: int
    daily_balance: list[Balance]


class ClientOverview(_Common):
    recent_trades: list[BasicTrade]
    intervals: dict[IntervalType, list[Interval]]
