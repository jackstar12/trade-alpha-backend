from lib.db.models import User
from lib.db.models.label import Label, LabelGroup
from lib.models import BaseModel, OutputID, InputID, CreateableModel
from lib.models import OrmBaseModel


class CreateLabel(CreateableModel):
    name: str
    color: str
    group_id: InputID

    def get(self, user: User):
        return Label(**self.dict())


class LabelInfo(OrmBaseModel, CreateLabel):
    id: OutputID
    group_id: OutputID


class LabelGroupCreate(CreateableModel):
    name: str

    def get(self, user: User):
        return LabelGroup(name=self.name, labels=[], user_id=user.id)


class LabelGroupInfo(OrmBaseModel, LabelGroupCreate):
    id: OutputID
    labels: list[LabelInfo]


class RemoveLabel(BaseModel):
    trade_id: InputID
    label_id: InputID


class AddLabel(RemoveLabel):
    pass
