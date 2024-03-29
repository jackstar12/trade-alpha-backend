from datetime import datetime
from decimal import Decimal
from typing import Optional

from lib.models import BaseModel


class OHLC(BaseModel):
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Optional[Decimal]
