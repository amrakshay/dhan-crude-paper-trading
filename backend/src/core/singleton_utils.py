"""Minimal singleton helper for FastAPI dependencies.

Controllers here are stateless orchestrators over a per-request session, so the
singleton is the *controller class*, re-bound to the request's session.
"""
from typing import Any, Callable, Dict, Type

_INSTANCES: Dict[Type, Any] = {}


def SingletonDepends(cls: Type, called_inside_fastapi_depends: bool = False) -> Callable:
    def _factory(*args: Any, **kwargs: Any) -> Any:
        return cls(*args, **kwargs)

    return _factory


def clear_singletons() -> None:
    _INSTANCES.clear()
