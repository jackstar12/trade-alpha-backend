from typing import List

from lib.models.user import ProfileData
from lib.models import OrmBaseModel
from lib.models.discord.guild import Guild


class DiscordUserInfo(OrmBaseModel):
    data: ProfileData
    guilds: List[Guild]
