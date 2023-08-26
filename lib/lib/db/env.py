from pydantic import SecretStr
from lib.env import Environment


class DatabaseEnv(Environment):
    PG_URL: str
    REDIS_URL: str
    ENCRYPTION: SecretStr


ENV = DatabaseEnv()
