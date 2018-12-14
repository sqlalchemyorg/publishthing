import logging
from typing import Any
from typing import Callable
from typing import Optional

from . import gerrit
from . import git
from . import github
from . import publish
from . import shell
from .util import memoized_property
from . import wsgi

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)


class PublishThing:
    def __init__(self, **opts: Any):
        self.opts = opts

    @memoized_property
    def github_webhook(self) -> "github.GithubWebhook":
        return github.GithubWebhook(self)

    @memoized_property
    def gerrit_hook(self) -> "gerrit.GerritHook":
        return gerrit.GerritHook(self)

    @memoized_property
    def gerrit_api(self) -> "gerrit.GerritApi":
        return gerrit.GerritApi(self)

    def wsgi_request(
            self, environ: "wsgi.WsgiEnviron",
            start_response: "wsgi.WsgiStartResponse") -> "wsgi.WsgiRequest":
        return wsgi.WsgiRequest(self, environ, start_response)

    def github_repo(self, repo: str) -> "github.GithubRepo":
        return github.GithubRepo(self, repo)

    def git_repo(
            self, path: str, origin: Optional[str] = None,
            bare: bool = False, create: bool = False) -> "git.GitRepo":
        return git.GitRepo(
            self, path, origin=origin, bare=bare, create=create)

    @memoized_property
    def publisher(self) -> "publish.Publisher":
        return publish.Publisher(self)

    def message(self, message: str, *arg: Any) -> None:
        print(message % arg)

    def warning(self, message: str, *arg: Any) -> None:
        print(message % arg)

    def debug(self, category: str, message: str, *arg: Any) -> None:
        logger = logging.getLogger("%s.%s" % (__name__, category))
        logger.debug(message, *arg)

    def cmd_error(self, message: str) -> None:
        raise Exception(message)

    def shell_in(self, path: str, create: bool=False) -> "shell.Shell":
        return shell.Shell(self, path, create)


