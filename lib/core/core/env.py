from typing import Optional

import dotenv
import os

from pydantic import BaseSettings, SecretStr, HttpUrl, AnyHttpUrl

dotenv.load_dotenv()


class EnvBase(BaseSettings):
    class Config:
        env_file = "../../.env"


class Environment(EnvBase):
    TESTING: bool = False
    LOG_OUTPUT_DIR: str = "/LOGS/"
    DATA_PATH: str = "/data/"

    FRONTEND_URL: AnyHttpUrl = "http://localhost:3000"


ENV = Environment()

TESTING = ENV.TESTING
LOG_OUTPUT_DIR = ENV.LOG_OUTPUT_DIR
DATA_PATH = ENV.DATA_PATH
