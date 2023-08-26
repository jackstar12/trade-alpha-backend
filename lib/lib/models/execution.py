from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import Field

from lib.models import BaseModel, OutputID
from lib.db.enums import ExecType, Side


class Execution(BaseModel):
    id: OutputID
    symbol: str
    price: Optional[Decimal]
    qty: Optional[Decimal]
    side: Optional[Side]
    time: datetime
    type: Optional[ExecType] = Field(default=ExecType.TRADE)
    commission: Optional[Decimal]
    realized_pnl: Optional[Decimal]

    class Config:
        orm_mode = True
