from aiohttp import ClientResponseError, ClientResponse
from typing import Optional


class UserInputError(Exception):
    def __init__(self, reason: str, user_id: Optional[int] = None, *args):
        super().__init__(*args)
        self.reason = reason
        self.user_id = user_id


class InternalError(Exception):
    def __init__(self, reason: str, *args):
        super().__init__(*args)
        self.reason = reason


class ResponseError(Exception):
    def __init__(
        self,
        human: str,
        root_error: Optional[ClientResponseError] = None,
        response: Optional[ClientResponse] = None,
    ):
        self.root_error = root_error
        if response:
            self.root_error = ClientResponseError(response.request_info, (response,))
        self.human = human


class WebsocketError(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class ClientDeletedError(Exception):
    pass


class InvalidClientError(ResponseError):
    pass


class CriticalError(ResponseError):
    def __init__(self, retry_ts: Optional[int] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_ts = retry_ts


class RateLimitExceeded(CriticalError):
    pass


class ExchangeUnavailable(CriticalError):
    pass


class ExchangeMaintenance(CriticalError):
    pass
