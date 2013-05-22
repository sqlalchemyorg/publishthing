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
            fn, args = update_git_mirror, (fullpath, "origin")
        elif is_hg(fullpath):
            fn, args = update_hg_mirror, (fullpath, )
        else:
            log("Skipping path: %s", dirname)
            continue

        try:
            fn(*args)
        except CalledProcessError, e:
            log("Error occurred: %s", e)


if __name__ == '__main__':
    main()
