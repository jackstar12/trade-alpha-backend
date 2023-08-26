from typing import Literal, TypedDict, Union, Optional

from pydantic import HttpUrl

from lib.db.models.action import Action, ActionTrigger, ActionType
from lib.models import OrmBaseModel, CreateableModel, OutputID
from lib.models.platform import DiscordPlatform, PlatformModel


class WebhookData(TypedDict):
    url: HttpUrl


class WebhookPlatform(PlatformModel):
    name: Literal["webhook"]
    data: WebhookData


class ActionCreate(CreateableModel):
    __table__ = Action

    name: Optional[str]
    type: ActionType
    topic: str
    platform: Union[DiscordPlatform, WebhookPlatform]
    message: Optional[str]
    trigger_type: ActionTrigger
    trigger_ids: dict


class ActionInfo(OrmBaseModel, ActionCreate):
    id: OutputID
