from datetime import timedelta

import pytest

from lib.test_utils.fixtures import Channel, Messages
from lib.messenger import TableNames, EventSpace
from lib.test_utils.mock import event_mock

pytestmark = pytest.mark.anyio


async def test_event_messages(event_service, test_user, messenger, db):
    async with Messages.create(
        Channel(TableNames.EVENT, EventSpace.REGISTRATION_START),
        Channel(TableNames.EVENT, EventSpace.START),
        Channel(TableNames.EVENT, EventSpace.REGISTRATION_END),
        Channel(TableNames.EVENT, EventSpace.END),
        messenger=messenger,
    ) as listener:
        event = event_mock(interval=timedelta(seconds=1)).get(test_user)
        db.add(event)
        await db.commit()
        await listener.wait(5)
