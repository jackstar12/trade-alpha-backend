from datetime import datetime
from enum import Enum
from typing import Type, Optional

import sqlalchemy.exc
from sqlalchemy.inspection import inspect
from sqlalchemy.orm.dynamic import AppenderQuery

from lib.models import OrmBaseModel


class Serializer:
    __tablename__: str
    __serializer_anti_recursion__ = False
    __serializer_forbidden__ = []
    __serializer_data_forbidden__ = []
    __model__: Optional[Type[OrmBaseModel]]

    @classmethod
    def is_data(cls):
        return False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # The full flag is needed to avoid cyclic serializations
    def serialize(self, full=False, data=True, include_none=True, *args, **kwargs):
        if self.__class__.__model__:
            return self.__class__.__model__.from_orm(self).dict()
        elif not self.__class__.__serializer_anti_recursion__:
            self.__class__.__serializer_anti_recursion__ = True
            try:
                s = None
                if data or not self.is_data():
                    s = {}
                    for k in inspect(self).attrs.keys():
                        forbidden = self.__serializer_forbidden__
                        if k not in forbidden:
                            try:
                                v = getattr(self, k)
                            except sqlalchemy.exc.InvalidRequestError:
                                continue
                            # if v is None:
                            #    continue
                            if issubclass(type(v), list):
                                if full:
                                    v = Serializer.serialize_list(
                                        v, data=data, full=full, *args, **kwargs
                                    )
                                else:
                                    continue
                            elif isinstance(v, datetime):
                                v = v.timestamp()
                            elif issubclass(type(v), Enum):
                                v = v.value
                            elif issubclass(type(v), Serializer):
                                if full:
                                    v = v.serialize(
                                        full=full,
                                        data=data,
                                        include_none=include_none,
                                        *args,
                                        **kwargs
                                    )
                                else:
                                    continue
                            elif issubclass(type(v), AppenderQuery):
                                continue
                            s[k] = v
                self.__class__.__serializer_anti_recursion__ = False
                return s
            except Exception as e:
                self.__class__.__serializer_anti_recursion__ = False
                raise e

    @staticmethod
    def serialize_list(
        list_, data=True, full=False, include_none=True, *args, **kwargs
    ):
        r = []
        for m in list_:
            s = m.serialize(full, data, *args, include_none=include_none, **kwargs)
            if s:
                r.append(s)
        return r
