import json
import os
from subprocess import CalledProcessError
from subprocess import check_call
from subprocess import check_output
from typing import Any
from typing import AnyStr
from typing import IO
from typing import Optional

from . import publishthing  # noqa

from . import git

class Shell:

    CalledProcessError: CalledProcessError = CalledProcessError

    def __init__(
            self, thing: "publishthing.PublishThing",
            path: str, create: bool = False) -> None:
        self.thing = thing
        self.path = path
        self.create = create
        if create and not os.path.exists(self.path):
            self.thing.message("mkdir -p %s", self.path)
            os.makedirs(self.path)

    def shell_in(self, path: str, create: bool=False) -> "Shell":
        """work in a subdirectory of this shell."""
        assert not path.startswith("/"), "Path %r is not relative" % path
        return Shell(
            self.thing, os.path.normpath(os.path.join(self.path, path)),
            create=create)

    def shell_out(self, path: str, create: bool=False) -> "Shell":
        """work in a new directory outside of this shell."""
        assert path.startswith("/"), "Path %r is not absolute" % path
        return Shell(self.thing, os.path.normpath(path), create=create)

    def __enter__(self) -> "Shell":
        self.thing.debug("shell", "cwd=%s" % self.path)
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def call_shell_cmd(self, *args: str) -> int:
        self.thing.debug("shell", " ".join(args))
        return check_call(args, cwd=self.path)

    def output_shell_cmd(self, *args: str) -> Any:
        self.thing.debug("shell", " ".join(args))
        return check_output(args, encoding='utf-8', cwd=self.path)

    def file_exists(self, filename: str) -> bool:
        return os.path.exists(
            os.path.join(self.path, filename)
        )

    def open(self, filename: str, mode: str = "r") -> IO[AnyStr]:
        path = os.path.join(self.path, filename)
        return open(path, mode)

    def write_file(
            self, filename: str, content: AnyStr, binary: bool=False) -> None:
        assert "/" not in filename, (
            "Received filename %r.  Use a sub-shell_in() for subdirectories" %
            filename)
        path = os.path.join(self.path, filename)
        self.thing.message("Writing %s bytes to %s", len(content), path)
        with open(path, "wb" if binary else "w") as file_:
            file_.write(content)

    def write_json_file(self, filename: str, json_data: Any) -> None:
        path = os.path.join(self.path, filename)
        self.thing.message("Writing json to %s", path)
        with open(path, "w") as file_:
            json.dump(json_data, file_, indent=4)

    def git_repo(
            self, local_name: str, origin: Optional[str] = None,
            bare: bool = False, create: bool = False) -> "git.GitRepo":
        return git.GitRepo(
            self.thing, self, local_name,
            origin=origin, bare=bare, create=create)

