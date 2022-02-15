import abc
import logging
from typing import List, Optional, Dict, Any, Callable
from requests import Request, Session, Response
from models.trade import Trade
from models.balance import Balance
from datetime import datetime
from typing import Tuple


class Client:
    api_key: str
    api_secret: str
    subaccount: str
    exchange = ''
    required_extra_args: List[str] = []

    rekt_on: datetime = None
    initial_balance: Tuple[datetime, Balance] = None
    track_trades: bool = False
    trades: List[Trade] = None

    def __init__(self,
                 api_key: str,
                 api_secret: str,
                 subaccount: Optional[str] = None,
                 extra_kwargs: Optional[Dict[str, Any]] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.subaccount = subaccount
        self.extra_kwargs = extra_kwargs
        self._session = Session()
        self._on_trade = None
        self._identifier = id

    @abc.abstractmethod
    def get_balance(self):
        logging.error(f'Exchange {self.exchange} does not implement get_balance!')

    def on_trade(self, callback: Callable[[str, Trade], None], identifier):
        self._on_trade = callback
        self._identifier = identifier

    @abc.abstractmethod
    def _sign_request(self, request: Request):
        logging.error(f'Exchange {self.exchange} does not implement _sign_request!')

    @abc.abstractmethod
    def _process_response(self, response: Response):
        logging.error(f'Exchange {self.exchange} does not implement _process_response')

    def _request(self, request: Request, sign=True):
        if sign:
            self._sign_request(request)
        prepared = request.prepare()
        response = self._session.send(prepared)
        return self._process_response(response)

    def repr(self):
        r = f'Exchange: {self.exchange}\n' \
               f'API Key: {self.api_key}\n' \
               f'API secret: {self.api_secret}'
        if self.subaccount != '':
            r += f'\nSubaccount: {self.subaccount}'

        return r
