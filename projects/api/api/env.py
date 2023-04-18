from pydantic import SecretStr, AnyHttpUrl

from core.env import EnvBase, Environment


class Env(Environment):
    OAUTH2_CLIENT_ID: str
    OAUTH2_CLIENT_SECRET: SecretStr


ENV = Env()
