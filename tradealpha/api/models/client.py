from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Set, Optional, Any

import pydantic
from fastapi import Query
from pydantic import BaseModel, UUID4

from tradealpha.api.models.execution import Execution
from tradealpha.api.models.trade import Trade
from tradealpha.api.models.transfer import Transfer
from tradealpha.common.dbmodels.base import OrmBaseModel
from tradealpha.common.dbmodels.transfer import TransferType


class ClientQueryParams:
    def __init__(self,
                 id: List[int] = Query(default=[]),
                 currency: str = Query(default='$'),
                 since: datetime = Query(default=None),
                 to: datetime = Query(default=None)):
        self.id = id
        self.currency = currency
        self.since = since
        self.to = to


class RegisterBody(BaseModel):
    name: str
    exchange: str
    api_key: str
    api_secret: str
    subaccount: Optional[str]
    extra: Optional[Dict]

    @pydantic.root_validator(pre=True)
    def build_extra(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        all_required_field_names = {
            field.alias for field in cls.__fields__.values() if field.alias != 'extra'
        }  # to support alias

        extra: Dict[str, Any] = {}
        for field_name in list(values):
            if field_name not in all_required_field_names:
                extra[field_name] = values.pop(field_name)
        values['extra'] = extra
        return values


class ConfirmBody(BaseModel):
    token: str


class DeleteBody(BaseModel):
    id: int


class UpdateBody(BaseModel):
    id: int
    name: Optional[str]
    archived: Optional[bool]
    discord: Optional[bool]
    servers: Optional[Set[int]]
    events: Optional[Set[int]]


class ClientInfo(BaseModel):
    id: int
    user_id: Optional[UUID4]
    discord_user_id: Optional[int]

    api_key: str
    exchange: str
    subaccount: Optional[str]
    extra_kwargs: Dict

    name: Optional[str]
    rekt_on: Optional[datetime]

    archived: bool
    invalid: bool

    class Config:
        orm_mode = True


class Balance(OrmBaseModel):
    time: Optional[datetime]
    realized: Decimal
    unrealized: Decimal
    total_transfered: Decimal

    def __add__(self, other):
        return Balance.construct(
            realized=self.realized + other.realized,
            unrealized=self.unrealized + other.unrealized,
            total_transfered=self.total_transfered + other.total_transfered,
            time=min(self.time, other.time) if self.time else None
        )


class ClientOverview(BaseModel):
    initial_balance: Balance
    current_balance: Balance
    trades_by_id: dict[str, Trade]
    transfers: dict[str, Transfer]
    daily: dict[date, Balance]