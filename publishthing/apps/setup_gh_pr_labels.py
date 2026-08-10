"""Create the pull request gate's labels on one or more repos.

Idempotent, so it's safe to re-run across the whole set::

    publish_gh_pr_labels --access-token TOKEN \\
        sqlalchemy/sqlalchemy sqlalchemy/alembic \\
        sqlalchemy/mako sqlalchemy/dogpile.cache

The names have to match what the webhook gate is configured with
exactly; a mismatch silently closes every pull request against that
repo, which is why creating these by hand in four web UIs is a bad idea.

The "code review in progress" definition here matches the one that has
been in use on sqlalchemy/sqlalchemy by hand for years, so running this
against that repo leaves the existing label alone.

"""

import argparse
from typing import List
from typing import NamedTuple
from typing import Optional

from .prgate import util
from .. import publishthing


class LabelSpec(NamedTuple):
    name: str
    color: str
    description: str


LABELS = [
    LabelSpec(
        util.DEFAULT_LABEL,
        "0e8a16",
        "Maintainers have approved this issue for an outside pull request",
    ),
    LabelSpec(
        util.DEFAULT_REVIEW_LABEL,
        "4B98F5",
        "code has been provided that's in review as PR and/or gerrit",
    ),
    LabelSpec(
        util.DEFAULT_DENY_LABEL,
        "ca2720",
        "Please do not submit pull requests for this issue.  "
        "We are doing it internally",
    ),
    LabelSpec(
        util.DEFAULT_APPROVED_LABEL,
        "5319e7",
        "Maintainer override: this pull request may proceed regardless "
        "of the labels on any issue",
    ),
]


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="create the pull request gate labels on github repos"
    )
    parser.add_argument(
        "repo", nargs="+", help="user/reponame string on github"
    )
    parser.add_argument(
        "--access-token", type=str, required=True, help="oauth access token"
    )

    opts = parser.parse_args(argv)

    thing = publishthing.PublishThing(github_access_token=opts.access_token)

    for repo in opts.repo:
        gh_repo = thing.github_repo(repo)

        existing = {rec["name"].lower() for rec in gh_repo.get_labels()}

        for spec in LABELS:
            if spec.name.lower() in existing:
                print("%s: label %r already present" % (repo, spec.name))
            else:
                gh_repo.create_label(spec.name, spec.color, spec.description)
                print("%s: created label %r" % (repo, spec.name))
