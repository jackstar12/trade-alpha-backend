from datetime import datetime
from typing import Dict, Optional, Any

import pydantic

from database.models import BaseModel
from database.dbmodels import Client
from database.dbmodels.client import ClientType
from database.dbmodels.user import User


class ClientApiInfo(BaseModel):
    api_key: str
    api_secret: str
    extra: Optional[Dict]
    subaccount: Optional[str]


class ClientCreate(ClientApiInfo):
    name: Optional[str]
    exchange: str
    sandbox: bool
    type: ClientType = ClientType.FULL
    import_since: Optional[datetime]

    def get(self, user: User = None) -> Client:
        client = Client(user=user, **self.dict(exclude={'import_since'}))
        if self.import_since:
            client.last_execution_sync = self.import_since
            client.last_transfer_sync = self.import_since
        return client
