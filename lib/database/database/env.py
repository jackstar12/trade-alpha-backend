from typing import Optional

from pydantic import SecretStr
from sqlalchemy.engine import URL

from core.env import EnvBase


class Environment(EnvBase):
    PG_URL: str
    REDIS_URL: str
    ENCRYPTION: SecretStr


ENV = Environment()
