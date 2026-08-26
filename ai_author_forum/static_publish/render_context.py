from contextlib import contextmanager
from contextvars import ContextVar

_static_release_version = ContextVar("static_release_version", default="")


def get_static_release_version():
    return _static_release_version.get()


@contextmanager
def static_release_context(version):
    token = _static_release_version.set(str(version or ""))
    try:
        yield
    finally:
        _static_release_version.reset(token)
