import argparse
import os
from typing import List
from typing import Optional

from .. import publishthing


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--zeekofile", action="store_true", help="Run zeekofile"
    )
    parser.add_argument("--type", help="legacy", type=str)
    parser.add_argument(
        "--local-base", type=str, help="Full path to local directory for sites"
    )
    parser.add_argument(
        "--local-prefix",
        type=str,
        help="Path prefix inside of a local site location",
    )
    parser.add_argument(
        "--repo-prefix",
        type=str,
        help="Optional path prefix inside the repo itself",
    )
    parser.add_argument(
        "--dry", action="store_true", help="Don't actually publish"
    )
    parser.add_argument(
        "--domain",
        type=str,
        help="Fully qualified domain name, defaults to dirname of repo",
    )
    parser.add_argument(
        "--branch",
        type=str,
        help="Branch name to check out on, by default no checkout occurs",
    )
    parser.add_argument("source", type=str, help="Source repository path")
    parser.add_argument("destination", choices=["local"], help="Destination")
    args = parser.parse_args(argv)

    # path to a bare git repo where the stuff is.
    repo_path: str = os.path.abspath(args.source)

    thing: publishthing.PublishThing = publishthing.PublishThing()

    sitename: str = args.domain
    if not sitename:
        sitename = os.path.basename(repo_path)
        if sitename.endswith(".git"):
            sitename = sitename[0:-4]
    thing.message("Site name %s", sitename)

    # make "work" sibling path to where the git repo is
    work_dir: str = os.path.join(os.path.dirname(repo_path), "work")
    with thing.shell_in(work_dir, create=True) as shell:
        git_repo = shell.git_repo(sitename, origin=repo_path, create=True)
        if args.branch:
            git_repo.checkout(args.branch)
        else:
            git_repo.pull_current()

    copy_from: str

    if args.zeekofile:
        copy_from = thing.publisher.zeekofile_build(git_repo, args.repo_prefix)
    else:
        if args.repo_prefix:
            copy_from = os.path.join(
                git_repo.checkout_location, args.repo_prefix
            )
        else:
            copy_from = os.path.join(git_repo.checkout_location)

    if args.destination == "local":
        thing.publisher.publish_local(
            copy_from, sitename, args.local_base, args.local_prefix, args.dry
        )
    else:
        thing.cmd_error("no destination specified")
