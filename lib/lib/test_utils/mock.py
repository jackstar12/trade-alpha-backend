from datetime import datetime, timedelta

from lib.utils import utc_now
from lib.models.document import DocumentModel
from lib.models.eventinfo import EventCreate, WebPlatform
from typing import Optional


def event_mock(
    now: Optional[datetime] = None, interval: timedelta = timedelta(seconds=2)
):
    now = now or utc_now()
    return EventCreate(
        name="Mock",
        description=DocumentModel(type="doc"),
        registration_start=now,
        start=now + 1 * interval,
        registration_end=now + 2 * interval,
        end=now + 3 * interval,
        location=WebPlatform(name="web", data={}),
        max_registrations=100,
    )
