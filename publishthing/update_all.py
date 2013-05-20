"""Update every DVCS in a directory.
"""
import os
from core import update_git_mirror, is_git, log, \
        CalledProcessError, is_hg, update_hg_mirror
import argparse

def main(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument("path", type=str,
            help="Directory containing repositories.")
    args = parser.parse_args(argv)

    basepath = os.path.abspath(args.path)
    for dirname in os.listdir(basepath):
        fullpath = os.path.join(basepath, dirname)
        if is_git(fullpath):
            log("Updating git repo: %s", dirname)
            try:
                update_git_mirror(fullpath, "origin")
            except CalledProcessError, e:
                log("Error occurred: %s", e)
        elif is_hg(fullpath):
            log("Updating hg repo: %s", dirname)
            try:
                update_hg_mirror(fullpath)
            except CalledProcessError, e:
                log("Error occurred: %s", e)
        else:
            log("Skipping path: %s", dirname)

if __name__ == '__main__':
    main()
