import os
from subprocess import check_call, CalledProcessError
import sys
import contextlib


def update_git_mirror(path, origin):
    """Update a git repo that is mirroring with --mirror
    """
    with chdir_as(path):
        check_call(["git", "remote", "update", "--prune", origin])


def update_hg_mirror(path):
    """Update an hg repo
    """
    with chdir_as(path):
        check_call(["hg", "pull"])

@contextlib.contextmanager
def chdir_as(path):
    currdir = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(currdir)


def git_checkout_files(repo, work_dir, dirname):
    os.environ.pop('GIT_DIR', None)
    checkout = os.path.join(work_dir, dirname)
    if not os.path.exists(checkout):
        os.chdir(work_dir)
        log("Cloning %s into %s", repo, os.path.join(work_dir, dirname))
        check_call(["git", "clone", repo, dirname])
        os.chdir(checkout)
    else:
        os.chdir(checkout)
        log("Updating %s", checkout)
        check_call(["git", "pull"])
    return checkout

def hg_checkout_files(repo, work_dir, dirname):
    checkout = os.path.join(work_dir, dirname)
    if not os.path.exists(checkout):
        os.chdir(work_dir)
        log("Cloning %s into %s", repo, os.path.join(work_dir, dirname))
        check_call(["hg", "clone", repo, dirname])
        os.chdir(checkout)
    else:
        os.chdir(checkout)
        log("Updating %s", checkout)
        check_call(["hg", "pull"])
        check_call(["hg", "up"])
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

def log(msg, *args):
    print(msg % args)
