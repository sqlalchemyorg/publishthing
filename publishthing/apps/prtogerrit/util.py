from typing import NamedTuple
import re
from typing import Callable
from typing import Optional
from typing import Dict
from typing import Tuple
import unidiff

from ... import gerrit
from ... import github
from ... import publishthing

class PullRequestRec(NamedTuple):
    number: str
    sha: str


def get_pullreq_for_gerrit_change(
        thing: publishthing.PublishThing,
        opts: gerrit.GerritHookEvent) -> Optional[PullRequestRec]:
    change_commit = thing.gerrit_api.get_change_current_commit(opts.change)

    current_revision = change_commit["current_revision"]
    message = change_commit["revisions"][
        current_revision]["commit"]["message"]

    search_url = (
        r"Pull-request: https://github.com/%s/pull/(\d+)\n"
        "Pull-request-sha: (.+?)\n" % (
            opts.project,
        )
    )

    pr_num_match = re.search(search_url, message, re.M)
    if pr_num_match is None:
        thing.debug(
            "prtogerrit",
            "Did not locate a pull request in comment for gerrit "
            "review %s",
            opts.change)
        return None

    thing.debug(
        "prtogerrit",
        "Located pull request %s sha %s in gerrit review %s",
        pr_num_match.group(1),
        pr_num_match.group(2),
        opts.change
    )
    return PullRequestRec(pr_num_match.group(1), pr_num_match.group(2))


def gerrit_comment_includes_verify(opts: gerrit.GerritHookEvent) -> bool:
    return (
        opts.Verified is not None and
        opts.Verified_oldValue is not None) or (
        opts.Code_Review is not None and
        opts.Code_Review_oldValue is not None)


def github_pr_is_opened(event: github.GithubEvent) -> bool:
    return event.json_data['action'] in (
        "opened", "edited", "synchronize", "reopened")


def github_pr_is_reviewer_request(
        wait_for_reviewer: str) -> Callable[[github.GithubEvent], bool]:
    def is_reviewer_request(event: github.GithubEvent) -> bool:
        return \
            event.json_data['action'] == "review_requested" and \
            wait_for_reviewer in {
                rec["login"] for rec in
                event.json_data['pull_request']['requested_reviewers']
            }
    return is_reviewer_request


def create_github_position_map(
        unified_diff_text: str) -> Dict[Tuple[str, int, bool], str]:
    line_index = {}
    for patch in unidiff.PatchSet(unified_diff_text):
        # github measures position relative to @@ per file,
        # so create an offset for this file
        line_offset = patch[0][0].diff_line_no - 1
        for hunk in patch:
            for line in hunk:
                if line.source_line_no is not None:
                    line_index[
                        (patch.path, line.source_line_no, True)
                    ] = line.diff_line_no - line_offset
                if line.target_line_no is not None:
                    line_index[
                        (patch.path, line.target_line_no, False)
                    ] = line.diff_line_no - line_offset
    return line_index