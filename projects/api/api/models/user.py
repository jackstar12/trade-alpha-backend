import uuid
from typing import Optional

from fastapi_users import schemas

from lib.models.user import ProfileData


class OAuthInfo(schemas.BaseOAuthAccount):
    data: Optional[ProfileData]


class UserRead(schemas.BaseUser[uuid.UUID]):
    id: uuid.UUID
    oauth_accounts: list[OAuthInfo]


class UserCreate(schemas.BaseUserCreate):
    pass
