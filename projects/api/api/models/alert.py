from database.models import BaseModel, OutputID

from database.enums import Side


class Alert(BaseModel):
    id: OutputID
    discord_user_id: str

    symbol: str
    price: float
    exchange: str
    side: Side
    note: str

    class Config:
        orm_mode = True
