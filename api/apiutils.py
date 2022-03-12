from functools import wraps

from flask import request
from http import HTTPStatus
from typing import List, Tuple, Union, Callable, Dict
from api.database import app
import flask_jwt_extended as flask_jwt


def check_args_before_call(callback, arg_names, *args, **kwargs):
    for arg_name, required in arg_names:
        value = None
        if request.json:
            value = request.json.get(arg_name)
            kwargs[arg_name] = value
        if not value and request.args:
            kwargs[arg_name] = request.args.get(arg_name)
        if not value and required:
            return {'msg': f'Missing parameter {arg_name}'}, HTTPStatus.BAD_REQUEST
    return callback(*args, **kwargs)


def require_args(arg_names: List[Tuple[str, bool]]):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return check_args_before_call(fn, arg_names, *args, **kwargs)
        return wrapper
    return decorator


def create_endpoint(
        route: str,
        methods: Dict[str, Dict[str, Union[List[Tuple[str, bool]], Callable]]],
        jwt_auth=False):

    def wrapper(*args, **kwargs):
        if request.method in methods:
            arg_names = methods[request.method]['args']
            callback = methods[request.method]['callback']
        else:
            return {'msg': f'This is a bug in the API.'}, HTTPStatus.INTERNAL_SERVER_ERROR
        return check_args_before_call(callback, arg_names, *args, **kwargs)

    wrapper.__name__ = route

    if jwt_auth:
        app.route(route, methods=list(methods.keys()))(
            flask_jwt.jwt_required()(wrapper)
        )
    else:
        app.route(route, methods=list(methods.keys()))(
            wrapper
        )