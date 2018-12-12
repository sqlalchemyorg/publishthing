"""Post comments to a github issue when it is referenced in a patchset commit.

Build a configuration .py as follows::

    from publishthing.apps import gerrit_patchset_comment
    import publishthing

    mapping = dict(
        fixes_re=r"[Ff]ixes:? +#(\d+)",
        fixes_message="**%(author)s** has proposed a fix for this "
        "issue:\n\n**%(summary)s** %(gerritlink)s",
        references_re=r"[Rr]eferences:? +#(\d+)",
        references_message="**%(author)s** referenced this "
        "issue:\n\n**%(summary)s** %(gerritlink)s",
    )


    thing = publishthing.PublishThing(
        github_access_token="some_github_personal_token",
        gerrit_api_url="https://gerrit.sqlalchemy.org",
        gerrit_api_username="gerrit-username",
        gerrit_api_password="gerrit-http-password",
        gerrit_approval_categories=['Workflow']
    )


    gerrit_patchset_comment.gerrit_patchset_comment(thing, mapping)

    if __name__ == '__main__':
        thing.gerrit_hook.main()

Then create a gerrit hook, in ``/var/gerrit/hooks/patchset-created``::

    #!/bin/sh

    python -m myconfig.py "patchset-created" "$@"

A new gerrit review to ``orgname/projectname`` that mentions an issue
will post to the Github repository ``orgname/projectname``, to that issue
number.

"""
from .. import publishthing
from typing import Dict
from typing import List, Tuple, Any

import collections
import difflib
import re


def gerrit_patchset_comment(
        thing: publishthing.PublishThing,
        mapping: Dict[str, str]) -> None:

    regs = {}
    for key in mapping:
        if key.endswith("_re"):

            reg = mapping[key]
            message = mapping["%s_message" % key[0:-3]]

            regs[key[0:-3]] = {
                "reg": reg,
                "comment": message
            }

    @thing.gerrit_hook.event("patchset-created")
    def patchset_created(opts: Any) -> None:
        this_revision = thing.gerrit_api.get_patchset_commit(
            opts.change, opts.patchset)

        author = this_revision['author']['name']
        summary = this_revision['message'].split("\n")[0]

        if opts.patchset > 1:
            # look for lines that were added in this patchset
            # compared to the previous one.
            previous_revision = thing.gerrit_api.get_patchset_commit(
                opts.change, opts.patchset - 1)
            lines = list(
                difflib.unified_diff(
                    previous_revision['message'].split("\n"),
                    this_revision['message'].split("\n"))
            )
            issue_numbers = _grep_issue_numbers(
                regs,
                [l[1:] for l in lines if l.startswith('+')]
            )
        else:
            # this is the first patchset, all lines are new
            lines = this_revision['message'].split("\n")
            issue_numbers = _grep_issue_numbers(regs, lines)

        # e.g. gerrit sqlalchemy/testgerrit is also github
        # sqlalchemy/testgerrit.   this can be more configurable
        github_repo = thing.github_repo(opts.project)

        for message, issue_number in issue_numbers:
            complete_message = message % {
                "user": opts.uploader_username,
                "author": author,
                "gerritlink": opts.change_url,
                "summary": summary
            }
            github_repo.publish_issue_comment(issue_number, complete_message)


def _grep_issue_numbers(
        regs: Dict[str, str],
        lines: List[str]) -> List[Tuple[str, str]]:
    outputs = collections.defaultdict(set)
    for line in lines:
        for key, value in regs.items():
            match = re.match(value['reg'], line)
            if match:
                outputs[key].add(match.group(1))
    return [
        (regs[key]["comment"], value)
        for key in outputs for value in outputs[key]
    ]

