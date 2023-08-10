from datetime import datetime, timedelta
from typing import Optional, Type, TypeVar

import pytest
import pytz
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from requests import Response

from api.routers.event import EventUpdate, EventJoinBody
from api.utils.responses import ResponseModel
from common.exchanges import SANDBOX_CLIENTS
from common.test_utils.mock import event_mock
from database.models.eventinfo import EventInfo, EventDetailed, Leaderboard, EventEntry

T = TypeVar("T")

pytestmark = pytest.mark.anyio


def parse_response(
    resp: Response, model: Optional[Type[T]] = None
) -> tuple[Response, T]:
    data = resp.json()
    try:
        if "result" in data:
            return resp, ResponseModel(**data)
        else:
            return resp, model(**data)
    except (ValidationError, TypeError):
        return resp, None


@pytest.fixture
def event(api_client_logged_in):
    now = datetime.now(pytz.utc)

    resp = api_client_logged_in.post("event", json=jsonable_encoder(event_mock(now)))

    assert resp.ok, resp.json()
    resp, event = parse_response(resp, EventInfo)

    yield event

    resp = api_client_logged_in.delete(f"event/{event.id}")
    assert resp.ok


def test_create_event(event):
    pass


def test_modify_event(event, api_client_logged_in):
    def modify(updates: EventUpdate):
        return parse_response(
            api_client_logged_in.patch(
                f"event/{event.id}", json=jsonable_encoder(updates)
            ),
            EventInfo,
        )

    resp, modified_event = modify(EventUpdate(name="Mock New"))
    assert modified_event.name == "Mock New"

    now = datetime.now(pytz.utc)

    resp, modified_event = modify(
        EventUpdate.construct(
            name="Mock Old", start=now, end=now - timedelta(seconds=5)
        )
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("confirmed_client", [SANDBOX_CLIENTS[0]], indirect=True)
async def test_register_event(event, api_client_logged_in, confirmed_client):
    event_url = f"event/{event.id}"
    entries = []

    resp, entry = parse_response(
        api_client_logged_in.post(
            event_url + "/registrations",
            json=EventJoinBody(client_id=confirmed_client.id).dict(),
        ),
        EventEntry,
    )

    assert resp.ok, resp.json()
    entries.append(entry)

    resp = api_client_logged_in.post(
        event_url + "/registrations",
        json=EventJoinBody(client_id=confirmed_client.id).dict(),
    )
    assert resp.status_code == 400

    resp, result = parse_response(api_client_logged_in.get(event_url), EventDetailed)

    assert resp.ok, resp.json()
    assert len(result.entries) == len(entries)
    assert result.id == event.id

    resp, result = parse_response(
        api_client_logged_in.get(event_url + "/leaderboard"), Leaderboard
    )

    assert len(result.unknown) == len(entries)

    for entry in entries:
        resp = api_client_logged_in.delete(event_url + f"/registrations/{entry.id}")
        assert resp.ok, resp.json()


def test_get_all(event, api_client_logged_in):
    resp = api_client_logged_in.get("event/")

    assert resp.ok
    results = resp.json()
    assert len(results) == 1
    result = EventInfo(**results[0])
    assert result.id == event.id
