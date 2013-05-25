import os
from subprocess import check_call, CalledProcessError
import sys
import contextlib


def update_git_mirror(path, origin, update_server_info=False):
    """Update a git repo that is mirroring with --mirror
    """
    with chdir_as(path):
        call_cmd(["git", "remote", "update", "--prune", origin])
        # TODO: not sure how to get this to trigger
        # as part of the git remote update
        if update_server_info:
            call_cmd(["git", "update-server-info"])

def git_push(path, remote):
    with chdir_as(path):
        call_cmd(["git", "push", "--mirror", remote])

def update_hg_mirror(path):
    """Update an hg repo
    """
    with chdir_as(path):
        call_cmd(["hg", "pull"])

def hg_push(path, remote):
    with chdir_as(path):
        call_cmd(["hg", "push", remote])

def call_cmd(args):
    log(" ".join(args))
    check_call(args)

@contextlib.contextmanager
def chdir_as(path):
    currdir = os.getcwd()
    chdir(path)
    yield
    os.chdir(currdir)

def chdir(path):
    os.chdir(path)
    log("cd %s", path)

def git_checkout_files(repo, work_dir, dirname):
    os.environ.pop('GIT_DIR', None)
    checkout = os.path.join(work_dir, dirname)
    if not os.path.exists(checkout):
        chdir(work_dir)
        call_cmd(["git", "clone", repo, dirname])
        chdir(checkout)
    else:
        chdir(checkout)
        call_cmd(["git", "pull"])
    return checkout

def hg_checkout_files(repo, work_dir, dirname):
    checkout = os.path.join(work_dir, dirname)
    if not os.path.exists(checkout):
        chdir(work_dir)
        call_cmd(["hg", "clone", repo, dirname])
        chdir(checkout)
    else:
        chdir(checkout)
        call_cmd(["hg", "pull"])
        call_cmd(["hg", "up"])
    return checkout

def is_git(path):
    return os.path.exists(os.path.join(path, ".git")) or \
        (
            os.path.exists(os.path.join(path, "config")) and
            os.path.exists(os.path.join(path, "hooks")) and
            os.path.exists(os.path.join(path, "refs"))
        )

def is_hg(path):
    return os.path.exists(os.path.join(path, ".hg")) or \
            os.path.exists(os.path.join(path, "hgrc"))

# TODO: use logging
def log(msg, *args):
    print("[publishthing] " + (msg % args))
