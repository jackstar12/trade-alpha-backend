from pydantic import SecretStr

from lib.env import EnvBase


class Env(EnvBase):
    BOT_KEY: SecretStr


environment = Env()
