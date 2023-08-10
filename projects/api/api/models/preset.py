from database.models import OrmBaseModel, OutputID


class PresetInfo(OrmBaseModel):
    id: OutputID
    name: str
    type: str
    attrs: dict


class PresetCreate(OrmBaseModel):
    name: str
    type: str
