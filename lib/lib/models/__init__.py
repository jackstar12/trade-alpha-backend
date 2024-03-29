from __future__ import annotations

from typing import Optional, Type, TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from lib.db.models import User
    from lib.db.dbsync import BaseMixin


class InputID(int):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(anyOf=[{"type": "integer"}, {"type": "string"}], type=None)

    @classmethod
    def validate(cls, v):
        return cls(v)


OutputID = str

Model = TypeVar("Model", bound="BaseModel")


class BaseModel(PydanticBaseModel):
    @classmethod
    def deep_construct(cls, _fields_set=None, **values):
        m = cls.__new__(cls)
        fields_values = {}

        config = cls.__config__

        for name, field in cls.__fields__.items():
            key = field.alias
            # Added this to allow population by field name
            if key not in values and config.allow_population_by_field_name:
                key = name

            if key in values:
                # Moved this check since None value can be passed for Optional nested field
                if values[key] is None and not field.required:
                    fields_values[name] = field.get_default()
                else:
                    if issubclass(field.type_.__class__, BaseModel):
                        if isinstance(values[key], field.type_):
                            fields_values[name] = values[key]
                        elif field.shape == 2:
                            fields_values[name] = [
                                e
                                if isinstance(e, field.type_)
                                else field.type_.construct(**e)
                                for e in values[key]
                            ]
                        elif field.shape == 12:
                            fields_values[name] = {
                                k: e
                                if isinstance(e, field.type_)
                                else field.type_.construct(**e)
                                for k, e in values[key].items()
                            }
                        else:
                            fields_values[name] = field.outer_type_.construct(
                                **values[key]
                            )
                    else:
                        fields_values[name] = values[key]
            elif not field.required:
                fields_values[name] = field.get_default()

        object.__setattr__(m, "__dict__", fields_values)
        if _fields_set is None:
            _fields_set = set(values.keys())
        object.__setattr__(m, "__fields_set__", _fields_set)
        m._init_private_attributes()
        return m

    @classmethod
    def get_sa_type(cls, validate=False, **dict_options) -> Type[TypeDecorator]:
        class SAType(TypeDecorator):
            impl = JSONB
            cache_ok = True

            def process_bind_param(self, value, dialect):
                if isinstance(value, cls):
                    return value.dict(**dict_options)
                return value

            def process_result_value(self, value, dialect):
                if value:
                    if isinstance(value, str):
                        return value
                    if validate:
                        return cls(**value)
                    return cls.deep_construct(**value)
                return value

        return SAType

    @classmethod
    def from_orm_with(
        cls: Type[Model], obj: Any, extra: Optional[dict] = None
    ) -> Model:
        values = extra or {}

        for field in cls.__fields__.keys():
            if field not in extra:
                values[field] = getattr(obj, field)

        return cls(**values)


class CreateableModel(BaseModel):
    __table__: Type[BaseMixin]

    def get(self, user: User) -> BaseMixin:
        return self.__table__(**self.dict(), user=user)


class ValidatableModel(BaseModel):
    __table__: Type[BaseMixin]


class OrmBaseModel(BaseModel):
    class Config:
        orm_mode = True


__all__ = ["OrmBaseModel", "BaseModel", "InputID", "OutputID", "CreateableModel"]
