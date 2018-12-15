import os
from typing import Optional

from . import publishthing  # noqa
from . import shell as _shell  # noqa
from . import gerrit
from . import util


class GitError(Exception):
    pass


class GitRepo:
    was_created = False

    def __init__(
            self, thing: "publishthing.PublishThing",
            shell: "_shell.Shell",
            local_name: str,
            origin: Optional[str] = None,
            bare: bool = False, create: bool = False) -> None:
        self.thing = thing
        self.origin = origin
        self.shell = shell
        self.local_name = local_name
        self.bare = bare
        self.create = create
        if not self._ensure():
            if create:
                self._create()
                self.was_created = True
            else:
                raise GitError("No git repository at %s" % self.shell.path)

    def checkout(
            self, branchname: str, detached: Optional[bool] = False) -> None:
        self._assert_not_bare()
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd("git", "checkout", branchname)
            if not detached:
                shell.call_shell_cmd("git", "pull", "origin", branchname)

    def create_branch(self, branchname: str, force: bool=False) -> None:
        self._assert_not_bare()
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(
                "git", "checkout", "-B" if force else "-b", branchname)

    @property
    def checkout_location(self) -> str:
        self._assert_not_bare()
        return os.path.join(self.shell.path, self.local_name)

    def checkout_shell(self) -> _shell.Shell:
        self._assert_not_bare()
        return self.shell.shell_in(self.local_name)

    def enable_gerrit(
        self, git_identity: str, git_email: str, git_remote_username: str,
            git_remote_password: str) -> None:
        self.gerrit = gerrit.GerritGit(
            self, git_identity, git_email,
            git_remote_username, git_remote_password)

    def _assert_not_bare(self) -> None:
        if self.bare:
            raise GitError(
                "Checkout %s is a bare repository, pulls and "
                "file operations cannot be performed" % self._git_bare_path)

    @util.memoized_property
    def _git_bare_path(self) -> str:
        if self.bare:
            return os.path.join(self.shell.path, self.local_name)
        else:
            return os.path.join(self.shell.path, self.local_name, ".git")

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
        if not os.path.exists(self.checkout_location):
            if not os.path.exists(self.shell.path):
                raise GitError(
                    "working directory '%s' does not exist" % self.shell.path)
            if self.origin is None:
                raise GitError("no origin is defined")
            args = ["git", "clone", self.origin, self.local_name]
            if self.bare:
                args.append("--bare")
            self.shell.call_shell_cmd(*args)

    def set_identity(self, git_identity: str, git_email: str) -> None:
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(
                "git", "config", "--local", "user.name", git_identity)
            shell.call_shell_cmd(
                "git", "config", "--local", "user.email", git_email)

    def remote_add(self, remote: str, url: str) -> None:
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd("git", "remote", "add", remote, url)

    def remote_set_url(self, remote: str, url: str) -> None:
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd("git", "remote", "set-url", remote, url)

    def update_remote(self, remote: str) -> None:
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd("git", "remote", "update", "--prune", remote)
            shell.call_shell_cmd("git", "update-server-info")

    def push(self, remote: str, mirror: bool = False) -> None:
        args = ["git", "push"]
        if mirror:
            args += ["--mirror"]
        args += [remote]
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(*args)

    def pull(self, repository: str, branch: Optional[str] = None,
             squash: bool = False) -> None:
        args = ["git", "pull", repository]
        if branch:
            args += [branch]
        if squash:
            args += ["--squash"]
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(*args)

    def reset(self, hard: bool=False) -> None:
        args = ["git", "reset"]
        if hard:
            args += ["--hard"]
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(*args)

    def read_author_from_squash_push(self) -> str:
        self._assert_not_bare()
        with self.shell.shell_in(self.local_name) as shell:
            with shell.open(".git/SQUASH_MSG") as f:
                for line in f:
                    if line.startswith("Author:"):
                        author = line[8:]
                        return author
                else:
                    raise Exception("could not determine author for PR.")

    def commit(
            self, comment: str, author: Optional[str] = None,
            amend: bool = False) -> None:
        self._assert_not_bare()
        args = ["git", "commit", "-m", comment]
        if author:
            args += ["--author", author]
        if amend:
            args += ["--amend"]
        with self.shell.shell_in(self.local_name) as shell:
            shell.call_shell_cmd(*args)

