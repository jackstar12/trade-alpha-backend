from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Dict, NamedTuple, Optional, Any, Tuple

from pydantic import BaseModel, Field, Extra

from tradealpha.api.models.trade import DetailledTrade
from tradealpha.common.enums import Filter

class Calculation(Enum):
    PNL = "pnl"
    WINRATE = "winrate"



class Performance(NamedTuple):
    relative: Decimal
    absolute: Decimal
    #filter_values: Dict[Filter, Any]
    filter_values: List[Any]


class FilteredPerformance(BaseModel):
    filters: Tuple[Filter, ...]
    performances: List[Performance]


class ClientAnalytics(BaseModel):
    id: str
    filtered_performance: FilteredPerformance
    trades: List[DetailledTrade]
