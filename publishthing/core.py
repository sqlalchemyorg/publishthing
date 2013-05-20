import os
from subprocess import check_call
import sys

def update_git_mirror(path, origin):
    """Update a git repo that is mirroring with --mirror
    """
    os.chdir(path)
    check_call(["git", "remote", "update", "--prune", origin])


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

def log(msg, *args):
    print(msg % args)