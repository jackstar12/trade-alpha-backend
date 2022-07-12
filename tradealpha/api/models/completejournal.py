from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Literal, Optional, NamedTuple, Set

from pydantic import BaseModel

from tradealpha.api.models.template import TemplateInfo
from tradealpha.api.models.amount import FullBalance


class BaseOrmModel(BaseModel):
    class Config:
        orm_mode = True


class JournalCreate(BaseModel):
    clients: List[str]
    title: str
    chapter_interval: timedelta
    auto_generate: bool


class JournalInfo(JournalCreate):
    id: str

    class Config:
        orm_mode = True


class JournalDetailledInfo(JournalInfo):
    overview: Optional[dict]
    templates: list[TemplateInfo]


class JournalUpdate(BaseOrmModel):
    clients: Optional[Set[str]]
    title: Optional[str]
    notes: Optional[str]
    auto_generate: Optional[bool]


class Gain(NamedTuple):
    relative: Decimal
    absolute: Decimal


class ChapterInfo(BaseModel):
    id: str
    start_date: date
    end_date: date
    balances: List[FullBalance]
    performance: Optional[Gain]
    title: str
    #start_balance: FullBalance
    #end_balance: FullBalance

    class Config:
        orm_mode = True


class ChapterUpdate(BaseModel):
    notes: Optional[str]
    #trades: Optional[Set[str]]


class ChapterCreate(BaseModel):
    start_date: date
    title: str
    parent_id: Optional[int]


class Chapter(ChapterInfo):
    #trades: List[Trade]
    notes: Optional[str]


class CompleteJournal(JournalInfo):
    chapters: List[ChapterInfo]
