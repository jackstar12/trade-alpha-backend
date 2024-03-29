from datetime import datetime
from typing import Optional

from api.models.completejournal import JournalInfo
from lib.models import OrmBaseModel, OutputID
from lib.models.document import DocumentModel


class PageInfo(OrmBaseModel):
    id: OutputID
    title: Optional[str]
    # group: Optional[str]
    data: Optional[dict]
    created_at: datetime
    journal: Optional[JournalInfo]

    # balances: List[Balance]
    # performance: Optional[Gain]
    # start_balance: FullBalance
    # end_balance: FullBalance


class FullPage(PageInfo):
    doc: DocumentModel

    # balances: List[Balance]
    # performance: Optional[Gain]
    # start_balance: FullBalance
    # end_balance: FullBalance
