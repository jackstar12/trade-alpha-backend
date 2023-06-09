from typing import List

from database.models.user import ProfileData
from database.models import OrmBaseModel
from database.models.discord.guild import Guild


class DiscordUserInfo(OrmBaseModel):
    data: ProfileData
    guilds: List[Guild]
