import logging
import asyncio

from typing import Callable

from models.async_websocket_manager import WebsocketManager
import aiohttp


# https://binance-docs.github.io/apidocs/futures/en/#user-data-streams
class FuturesWebsocketClient(WebsocketManager):
    _ENDPOINT = 'wss://fstream.binance.com'

    def __init__(self, client, session: aiohttp.ClientSession, on_message: Callable = None):
        super().__init__(session=session)
        self._client = client
        self._listenKey = None
        self._on_message = on_message

    def _get_url(self):
        return self._ENDPOINT + f'/ws/{self._listenKey}'

    def _on_message(self, ws, message):
        event = message['e']
        print(message)
        if event == 'listenKeyExpired':
            self._renew_listen_key()
        elif callable(self._on_message):
            self._on_message(self, message)

    async def start(self):
        if self._listenKey is None:
            self._listenKey = await self._client.start_user_stream()
        asyncio.create_task(self._keep_alive())
        await self.connect()

    def stop(self):
        self._listenKey = None

    async def _renew_listen_key(self):
        self._listenKey = await self._client.start_user_stream()
        self.reconnect()

    async def _keep_alive(self):
        while self.connected:
            if self._listenKey:
                logging.info('Trying to reconnect binance websocket')
                self._listenKey = await self._client.keep_alive()
                await asyncio.sleep(45 * 60)
