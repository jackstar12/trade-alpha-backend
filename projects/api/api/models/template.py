from typing import Optional

from api.routers.authgrant import AuthGrantInfo
from lib.db.models.editing.template import TemplateType
from lib.models import BaseModel, InputID, OutputID
from lib.models.document import DocumentModel


class TemplateCreate(BaseModel):
    type: TemplateType
    journal_id: Optional[InputID]
    client_id: Optional[InputID]


class TemplateUpdate(BaseModel):
    doc: Optional[DocumentModel]
    public: Optional[bool]


class TemplateInfo(TemplateCreate):
    id: OutputID
    title: Optional[str]
    type: TemplateType

    class Config:
        orm_mode = True


class TemplateDetailed(TemplateInfo):
    grants: Optional[list[AuthGrantInfo]]
    doc: Optional[DocumentModel]

    class Config:
        orm_mode = True
