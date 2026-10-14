from __future__ import annotations

import threading
from functools import wraps
from typing import Callable, TypeVar, cast


# Imports, edits and exports share one in-process boundary.  The web service is
# threaded, so without this lock an export could validate one revision and
# serialize another one a few milliseconds later.
STATE_LOCK = threading.RLock()

F = TypeVar("F", bound=Callable)


def synchronized(function: F) -> F:
    @wraps(function)
    def guarded(*args, **kwargs):
        with STATE_LOCK:
            return function(*args, **kwargs)

    return cast(F, guarded)
