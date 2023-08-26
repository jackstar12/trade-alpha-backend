from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Any

from pydantic import Extra, validator

from lib.utils import get_timedelta
from lib.models import OutputID
from lib.models.execution import Execution
from lib.models.pnldata import PnlData
from lib.db.enums import Side, Status, TradeSession
from lib.models import OrmBaseModel, BaseModel, InputID
from lib.models.balance import AmountBase
from lib.models.document import DocumentModel


class BasicTrade(OrmBaseModel):
    id: OutputID
    client_id: OutputID
    symbol: str
    entry: Decimal
    exit: Optional[Decimal]
    side: Side
    status: Status
    transferred_qty: Decimal
    total_commissions: Optional[Decimal]
    size: Decimal
    realized_pnl: Decimal
    net_pnl: Decimal
    open_time: datetime
    settle: str
    weekday: int
    duration: Optional[timedelta]


class Trade(BasicTrade):
    qty: Decimal
    open_qty: Decimal
    label_ids: List[str]
    close_time: Optional[datetime]
    net_gain: Decimal
    account_size: Optional[Decimal]
    init_amount: Optional[AmountBase]

    @validator("duration", pre=True)
    def duration_parse(cls, v: Any):
        if isinstance(v, str):
            return get_timedelta(v)
        return v

    # initial: Execution
    # initial_execution_id: int


class DetailledTrade(Trade):
    tp: Optional[Decimal]
    sl: Optional[Decimal]

    max_pnl: Optional[PnlData]
    min_pnl: Optional[PnlData]
    sessions: list[TradeSession]

    fomo_ratio: Optional[Decimal]
    greed_ratio: Optional[Decimal]
    risk_to_reward: Optional[Decimal]
    realized_r: Optional[Decimal]
    account_gain: Optional[Decimal]
    notes: Optional[DocumentModel]

    executions: List[Execution]

    class Config:
        orm_mode = True
        arbitrary_types_allowed = False
        extra = Extra.ignore


class LabelUpdate(BaseModel):
    group_ids: set[InputID]
    label_ids: set[InputID]


class UpdateTrade(BaseModel):
    labels: Optional[LabelUpdate]
    notes: Optional[DocumentModel]
    template_id: Optional[InputID]


class UpdateTradeResponse(BaseModel):
    label_ids: Optional[list[str]]
    notes: Optional[DocumentModel]
