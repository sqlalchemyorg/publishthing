import os

from . import publishthing  # noqa

from typing import Optional
from . import util


class GitError(Exception):
    pass


class GitRepo:
    def __init__(
            self, thing: "publishthing.PublishThing",
            path: str, origin: Optional[str] = None,
            bare: bool = False, create: bool = False) -> None:
        self.thing = thing
        self.origin = origin
        self.path = path
        self.bare = bare
        self.create = create
        if not self._ensure():
            if create:
                self._create()
            else:
                raise GitError("No git repository at %s" % self.path)

    def checkout(self, branchname: str) -> None:
        self._assert_not_bare()
        with self.thing.shell_in(self.path) as shell:
            shell.call_shell_cmd("git", "checkout", branchname)
            shell.call_shell_cmd("git", "pull", "origin", branchname)

    @property
    def checkout_location(self) -> str:
        self._assert_not_bare()
        return self.path

    def _assert_not_bare(self) -> None:
        if self.bare:
            raise GitError(
                "Checkout %s is a bare repository, pulls and "
                "file operations cannot be performed" % self.path)

    @util.memoized_property
    def _git_bare_path(self) -> str:
        if self.bare:
            return self.path
        else:
            return os.path.join(self.path, ".git")

    def _ensure_looks_like_git(self, path: str) -> None:
        for dirname in "refs", "objects":
            if not os.path.isdir(os.path.join(path, dirname)):
                raise GitError(
                    "Git %s repostory path %s does not have a %s directory" %
                    ("bare" if self.bare else "full", path, dirname))

    def _ensure(self) -> bool:
        if not os.path.exists(self._git_bare_path):
            return False

        self._ensure_looks_like_git(self._git_bare_path)
        return True

    def _create(self) -> None:
        if not os.path.exists(self.path):
            workdir = os.path.dirname(self.path)
            if not os.path.exists(workdir):
                raise GitError(
                    "working directory '%s' does not exist" % workdir)
            if self.origin is None:
                raise GitError("no origin is defined")
            with self.thing.shell_in(workdir) as shell:
                args = ["git", "clone", self.origin, self.path]
                if self.bare:
                    args.append("--bare")
                shell.call_shell_cmd(*args)

    def update_remote(self, remote: str) -> None:
        with self.thing.shell_in(self.path) as shell:
            shell.call_shell_cmd("git", "remote", "update", "--prune", remote)
            shell.call_shell_cmd("git", "update-server-info")

    def push(self, remote: str, mirror: bool = False) -> None:
        args = ["git", "push"]
        if mirror:
            args += ["--mirror"]
        args += [remote]
        with self.thing.shell_in(self.path) as shell:
            shell.call_shell_cmd(*args)
