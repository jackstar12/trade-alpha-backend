from datetime import datetime
from typing import Optional

from pydantic import root_validator
from pydantic.utils import GetterDict

from database.dbmodels.authgrant import DiscordPermission, AssociationType, AuthGrant
from database.models import CreateableModel, OrmBaseModel, OutputID
from database.models.user import UserPublicInfo


class AuthGrantCreate(CreateableModel):
    expires: Optional[datetime]
    public: Optional[bool]
    discord: Optional[DiscordPermission]
    wildcards: Optional[list[AssociationType]]
    name: Optional[str]

    def dict(self, *, exclude_none=False, **kwargs):
        return super().dict(exclude_none=False, **kwargs)

    def get(self, user) -> AuthGrant:
        return AuthGrant(**self.dict(), user=user)


class AuthGrantInfo(AuthGrantCreate, OrmBaseModel):
    id: Optional[OutputID]
    owner: UserPublicInfo
    token: Optional[str]

    @root_validator(pre=True)
    def parse_grant_values(cls, values: GetterDict):
        if "grant" in values:
            other = {}
            for field in cls.__fields__.keys():
                if field in values:
                    other[field] = values[field]
                else:
                    other[field] = getattr(values["grant"], field)
            return other
        return values
