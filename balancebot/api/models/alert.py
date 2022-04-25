from typing import List

from pydantic import BaseModel

from balancebot.common.enums import Side


class Alert(BaseModel):
    id: int
    discord_user_id: int

    symbol: str
    price: float
    exchange: str
    side: Side
    note: str

    class Config:
        orm_mode = True
