from __future__ import annotations
from typing import NamedTuple, Optional, List
from lib.db.models.balance import Balance


class History(NamedTuple):
    data: List[Balance]
    initial: Optional[Balance]
