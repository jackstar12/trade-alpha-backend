from pydantic import UUID4
import uuid
from datetime import datetime
from typing import Optional, TypedDict

from database.models import OrmBaseModel
from database.models.document import DocumentModel


class ProfileData(TypedDict):
    name: str
    avatar_url: str


class UserProfile(ProfileData):
    #id: uuid.UUID
    src: Optional[str]


#class UserPublicInfo(OrmBaseModel):
#    id: uuid.UUID
#    created_at: datetime
#    profile: UserProfile
#    about_me: Optional[DocumentModel]


class UserPublicInfo(OrmBaseModel):
    id: uuid.UUID
    created_at: datetime
    profile: UserProfile
    about_me: Optional[DocumentModel]
