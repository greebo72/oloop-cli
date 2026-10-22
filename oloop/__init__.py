from importlib.metadata import PackageNotFoundError, version as _version

from .store import (
    Comment,
    Conflict,
    NotFound,
    Project,
    Store,
    StoreError,
    Task,
    ValidationError,
)

try:
    # The version lives in pyproject.toml and nowhere else; the installed package knows it.
    __version__ = _version("oloop")
except PackageNotFoundError:  # run from a source tree that was never installed
    __version__ = "unknown"

__all__ = [
    "__version__",
    "Comment",
    "Conflict",
    "NotFound",
    "Project",
    "Store",
    "StoreError",
    "Task",
    "ValidationError",
]
