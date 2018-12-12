from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Dict
from typing import Type
from typing import Tuple
from types import TracebackType

from webob import Request
from webob import Response

from . import publishthing  # noqa


ExcInfo = Tuple[Type[Exception], Exception, TracebackType]
WsgiEnviron = Dict[str, str]
WsgiHeaders = Dict[str, str]
WsgiParams = Dict[str, str]
WsgiResponse = Callable[[bytes], None]
WsgiStartResponse = Callable[[int, WsgiHeaders, ExcInfo], WsgiResponse]


class WsgiRequest:
    def __init__(self, thing: "publishthing.PublishThing",
                 environ: WsgiEnviron,
                 start_response: WsgiStartResponse) -> None:
        self.thing = thing
        self.environ = environ
        self.start_response = start_response
        self.request = Request(environ)
        self.response = Response()
        self.response.content_type = 'text/plain'
        self._text : List[str] = []

    @property
    def body(self) -> bytes:
        return self.request.body

    def add_text(self, message: str, *args: str) -> None:
        if args:
            message = message % args
        self._text.append(message)

    def respond(
        self, status_code: Optional[int] = None,
            message: Optional[str] = None) -> WsgiResponse:

        if status_code:
            self.response.status_code = status_code
        if message:
            self.response.text = message
        else:
            self.response.text = "\n".join(self._text)
        return self.response(self.environ, self.start_response)

    def debug(self, category: str, message: str, *arg: Any) -> None:
        self.thing.debug(category, message, *arg)

    @property
    def params(self) -> WsgiParams:
        return self.request.params

    @property
    def headers(self) -> WsgiHeaders:
        return self.request.headers


