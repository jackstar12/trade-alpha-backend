from datetime import datetime
from typing import Dict, Optional


from lib.models import BaseModel
from lib.db.models import Client
from lib.db.models.client import ClientType
from lib.db.models.user import User


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

    def get(self, user: Optional[User] = None) -> Client:
        client = Client(user=user, **self.dict(exclude={"import_since"}))
        if self.import_since:
            client.last_execution_sync = self.import_since
            client.last_transfer_sync = self.import_since
        return client
