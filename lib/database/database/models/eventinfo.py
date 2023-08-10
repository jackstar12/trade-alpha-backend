from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Union, Optional, TYPE_CHECKING

from pydantic import Field, condecimal, validator

from database import dbmodels
from database.dbmodels.action import ActionType
from database.models import OrmBaseModel, BaseModel, OutputID, CreateableModel
from database.models.action import ActionCreate
from database.models.authgrant import AuthGrantInfo
from database.models.balance import Balance
from database.models.document import DocumentModel
from database.models.gain import Gain
from database.models.platform import DiscordPlatform, WebPlatform
from database.models.user import UserPublicInfo

if TYPE_CHECKING:
    from database.dbmodels import User


class EventState(Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    REGISTRATION = "registration"
    ARCHIVED = "archived"


class _Common(BaseModel):
    registration_start: datetime
    registration_end: datetime
    start: datetime
    end: datetime
    name: str
    description: DocumentModel
    # public: Optional[bool]
    location: Union[DiscordPlatform, WebPlatform]
    max_registrations: int
    currency: Optional[str] = Field(default="USD")
    rekt_threshold: condecimal(gt=Decimal(-100), lt=Decimal(0)) = -99


class EventCreate(_Common, CreateableModel):
    actions: Optional[list[ActionCreate]]

    @validator("actions", each_item=True)
    def validate_actions(cls, value: ActionCreate):
        assert value["type"] == ActionType.EVENT.value
        return value

    def get(self, user: User) -> dbmodels.Event:
        values = {key: val for key, val in self.__dict__.items() if key != "actions"}
        event = dbmodels.Event(**values, owner=user)
        if self.actions:
            event.actions = [action.get(user) for action in self.actions]
        return event


class EventGrantInfo(AuthGrantInfo):
    registrations_left: Optional[int]


class EventBasicInfo(_Common, OrmBaseModel):
    id: OutputID
    state: list[EventState]


class EventInfo(EventBasicInfo):
    grants: list[EventGrantInfo]


class EventScore(OrmBaseModel):
    entry_id: OutputID
    rank: int
    gain: Gain
    time: datetime
    rekt_on: Optional[datetime]

    def __gt__(self, other):
        return self.gain.relative > other.gain.relative

    def __lt__(self, other):
        return self.gain.relative < other.gain.relative


class EventEntry(OrmBaseModel):
    id: OutputID
    user: UserPublicInfo
    exchange: Optional[str]
    nick_name: Optional[str]
    init_balance: Optional[Balance]
    joined_at: datetime


class EventEntryDetailed(EventEntry):
    rank_history: list[EventScore]


class EventDetailed(EventInfo):
    owner: UserPublicInfo
    entries: list[EventEntry]


class Stat(OrmBaseModel):
    best: OutputID
    worst: OutputID

    @classmethod
    def from_sorted(cls, sorted_clients: list[EventScore]):
        return cls(
            best=sorted_clients[0].entry_id,
            worst=sorted_clients[-1].entry_id,
        )


class Summary(OrmBaseModel):
    gain: Stat
    stakes: Stat
    volatility: Stat
    avg_percent: Decimal
    total: Decimal


class Leaderboard(BaseModel):
    valid: list[EventScore]
    unknown: list[OutputID]
