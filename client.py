import abc
import discord
import logging
from typing import Optional, List, Optional, Dict, Any
from requests import Request, Session, Response


class Client:
    api_key: str
    api_secret: str
    subaccount: str
    exchange = ''
    required_extra_args: List[str] = []

    def __init__(self,
                 api_key: str,
                 api_secret: str,
                 subaccount: Optional[str] = None,
                 extra_kwargs: Optional[Dict[str, Any]] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.subaccount = subaccount
        self.extra_kwargs = extra_kwargs

    @abc.abstractmethod
    def get_balance(self):
        logging.error(f'Exchange {self.exchange} does not implement get_balance!')

    @abc.abstractmethod
    def _sign_request(self, request: Request):
        logging.error(f'Exchange {self.exchange} does not implement _sign_request!')

    @abc.abstractmethod
    def _process_response(self, response: Response):
        logging.error(f'Exchange {self.exchange} does not implement _process_response')

    def _request(self, request: Request):
        s = Session()
        self._sign_request(request)
        prepared = request.prepare()
        response = s.send(prepared)
        return self._process_response(response)

    def repr(self):
        r = f'Exchange: {self.exchange}\n' \
               f'API Key: {self.api_key}\n' \
               f'API secret: {self.api_secret}'
        if self.subaccount != '':
            r += f'\nSubaccount: {self.subaccount}'

        return r

