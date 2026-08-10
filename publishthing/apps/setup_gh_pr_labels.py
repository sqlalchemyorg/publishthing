"""Create the "open for pull requests" label on one or more repos.

Idempotent, so it's safe to re-run across the whole set::

    publish_gh_pr_labels --access-token TOKEN \\
        sqlalchemy/sqlalchemy sqlalchemy/alembic \\
        sqlalchemy/mako sqlalchemy/dogpile.cache

The label name has to match what the webhook gate is configured with
exactly; a mismatch silently closes every pull request against that
repo, which is why creating it by hand in four web UIs is a bad idea.

"""

import argparse
from typing import List
from typing import Optional

from .prgate import util
from .. import publishthing

DEFAULT_COLOR = "0e8a16"
DEFAULT_DESCRIPTION = (
    "Maintainers have approved this issue for an outside pull request"
)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="create the pull request gate label on github repos"
    )
    parser.add_argument(
        "repo", nargs="+", help="user/reponame string on github"
    )
    parser.add_argument(
        "--access-token", type=str, required=True, help="oauth access token"
    )
    parser.add_argument(
        "--label", type=str, default=util.DEFAULT_LABEL, help="label name"
    )
    parser.add_argument(
        "--color", type=str, default=DEFAULT_COLOR, help="hex color, no '#'"
    )
    parser.add_argument("--description", type=str, default=DEFAULT_DESCRIPTION)

    opts = parser.parse_args(argv)

    thing = publishthing.PublishThing(github_access_token=opts.access_token)

    for repo in opts.repo:
        gh_repo = thing.github_repo(repo)

        existing = {rec["name"].lower() for rec in gh_repo.get_labels()}
        if opts.label.lower() in existing:
            print("%s: label %r already present" % (repo, opts.label))
        else:
            gh_repo.create_label(opts.label, opts.color, opts.description)
            print("%s: created label %r" % (repo, opts.label))
