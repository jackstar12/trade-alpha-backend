from datetime import datetime
from typing import Optional, Dict, List

from lib.models import BaseModel


class WebsocketMessage(BaseModel):
    type: str
    channel: Optional[str]
    data: Optional[Dict]


class ClientConfig(BaseModel):
    id: Optional[List[int]]
    since: Optional[datetime]
    to: Optional[datetime]
    currency: Optional[str]
