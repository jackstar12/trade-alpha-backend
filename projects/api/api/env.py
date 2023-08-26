from pydantic import SecretStr

from lib.env import Environment


class Env(Environment):
    OAUTH2_CLIENT_ID: str
    OAUTH2_CLIENT_SECRET: SecretStr


ENV = Env()
