"""Update every DVCS in a directory.
"""
import os
from core import update_git_mirror, is_git, log
import argparse

def main(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument("path", type=str,
            help="Directory containing repositories.")
    args = parser.parse_args(argv)

    for dirname in os.listdir(args.path):
        fullpath = os.path.join(args.path, dirname)
        if is_git(fullpath):
            log("Updating git repo: %s", dirname)
            update_git_mirror(fullpath, "origin")
        else:
            log("Skipping path: %s", dirname)

if __name__ == '__main__':
    main()
