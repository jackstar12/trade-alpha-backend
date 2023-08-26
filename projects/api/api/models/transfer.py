from datetime import datetime
from decimal import Decimal
from typing import Optional

from lib.models import OutputID
from lib.models import OrmBaseModel
from lib.db.models.transfer import TransferType


class Transfer(OrmBaseModel):
    id: OutputID
    note: Optional[str]
    coin: str
    amount: Decimal
    size: Decimal
    time: datetime
    commission: Optional[Decimal]
    type: TransferType
    extra_currencies: Optional[dict[str, Decimal]]
