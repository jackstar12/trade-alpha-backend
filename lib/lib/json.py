import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import orjson
from pydantic import BaseModel


def default(obj: Any):
    if isinstance(obj, Decimal):
        return round(float(obj), ndigits=6)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, BaseModel):
        return obj.dict()
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    raise TypeError


def dumps(obj: Any, indent=False):
    test = orjson.dumps(
        obj,
        default=default,
        option=orjson.OPT_OMIT_MICROSECONDS | orjson.OPT_INDENT_2
        if indent
        else orjson.OPT_OMIT_MICROSECONDS,
    )
    return test


def dumps_no_bytes(obj: Any):
    return orjson.dumps(
        obj, default=default, option=orjson.OPT_OMIT_MICROSECONDS
    ).decode("utf-8")


def loads_bytes(obj: Any):
    return orjson.loads(obj)


def loads(obj: Any, parse_decimal=True):
    return json.loads(obj, parse_float=Decimal if parse_decimal else float)
