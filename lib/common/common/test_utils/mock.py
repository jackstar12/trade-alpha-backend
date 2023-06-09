from datetime import datetime, timedelta

from core.utils import utc_now
from database.models.document import DocumentModel
from database.models.eventinfo import EventCreate, WebPlatform


def event_mock(now: datetime = None, interval: timedelta = timedelta(seconds=2)):
    now = now or utc_now()
    return EventCreate(
        name='Mock',
        description=DocumentModel(type='doc'),
        registration_start=now,
        start=now + 1 * interval,
        registration_end=now + 2 * interval,
        end=now + 3 * interval,
        location=WebPlatform(name='web', data={}),
        max_registrations=100,
    )
