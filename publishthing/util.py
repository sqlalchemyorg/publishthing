import collections
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from . import publishthing  # noqa


class memoized_property:
    """A read-only @property that is only evaluated once."""

    def __init__(
        self, fget: Callable[[Any], Any], doc: Optional[str] = None
    ) -> None:
        self.fget = fget
        self.__doc__ = doc or fget.__doc__
        self.__name__ = fget.__name__

    def __get__(self, obj: Optional[object], cls: type) -> Any:
        if obj is None:
            return self
        obj.__dict__[self.__name__] = result = self.fget(obj)
        return result


EventHook = Callable[..., None]
EventFilter = Callable[[Any], None]
_HookRecord = Tuple[EventHook, Optional[EventFilter]]


class Hooks:
    thing: "publishthing.PublishThing"

    def __init__(self) -> None:
        self.hooks: Dict[str, List[_HookRecord]] = collections.defaultdict(
            list
        )

    def event(
        self, event: str, filter_: Optional[EventFilter] = None
    ) -> Callable[[EventHook], EventHook]:
        def decorate(fn: EventHook) -> EventHook:
            self.hooks[event].append((fn, filter_))
            return fn

        return decorate

    def _run_hooks(self, name: str, target: Any, *arg: Any, **kw: Any) -> None:
        for handler, filter_ in self.hooks.get(name, []):
            if filter_ is None or filter_(target):
                self.thing.debug("hooks", "Running hook %s", handler)
                handler(target, *arg, **kw)
