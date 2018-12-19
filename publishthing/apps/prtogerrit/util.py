import collections
import re
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

import unidiff

from ... import gerrit
from ... import github
from ... import publishthing
from ...util import memoized_property

class PullRequestRec(NamedTuple):
    """A key referring to a specific version of a github pull request,
    which we serialize into the commit messages of the changesets we
    push to gerrit."""
    number: str
    sha: str

class GerritReviewLine(NamedTuple):
    path: str
    line_number: int
    is_parent: bool

class GithubReviewPosition(NamedTuple):
    path: str
    position: int



def get_pullreq_for_gerrit_change(
        thing: publishthing.PublishThing,
        opts: gerrit.GerritHookEvent) -> Optional[PullRequestRec]:
    change_commit = thing.gerrit_api.get_change_current_revision(opts.change)

    current_revision = change_commit["current_revision"]
    message = change_commit["revisions"][
        current_revision]["commit"]["message"]

    pr_num_match = get_pullreq_for_gerrit_commit_message(
        opts.project, message
    )
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
        pr_num_match.number,
        pr_num_match.sha,
        opts.change
    )
    return pr_num_match

def get_pullreq_for_gerrit_commit_message(
        project: str, commit_message: str) -> Optional[PullRequestRec]:
    search_url = (
        r"Pull-request: https://github.com/%s/pull/(\d+)\n"
        "Pull-request-sha: (.+?)\n" % (project,)
    )

    pr_num_match = re.search(search_url, commit_message, re.M)
    if pr_num_match is not None:
        return PullRequestRec(pr_num_match.group(1), pr_num_match.group(2))
    else:
        return None


def gerrit_comment_includes_verify(opts: gerrit.GerritHookEvent) -> bool:
    return (
        opts.Verified is not None and
        opts.Verified_oldValue is not None) or (
        opts.Code_Review is not None and
        opts.Code_Review_oldValue is not None)


def github_pr_is_opened(event: github.GithubEvent) -> bool:
    return bool(event.json_data['action'] in (
        "opened", "edited", "synchronize", "reopened"))


def github_pr_review_is_submitted(event: github.GithubEvent) -> bool:
    return bool(event.json_data['action'] == "submitted")

def github_pr_comment_is_created(event: github.GithubEvent) -> bool:
    return bool(event.json_data['action'] == "created")

def github_comment_is_pullrequest(event: github.GithubEvent)-> bool:
    return bool(event.json_data["issue"].get("pull_request"))

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




def format_gerrit_comment_for_github(
        author_fullname: str, author_username: str, message: str) -> str:

    # strip out email address if present, some events include it
    if "@" in author_fullname:
        author_fullname = re.sub(
            r'<?[\w_\.]+@[\w_\.]+>?', '', author_fullname).strip('" ')

    message = re.sub(r'^\s*Patch Set \d+:\s*', '', message)

    return "**%s** (%s) wrote:\n\n%s" % (
        author_fullname, author_username, message
    )

def format_github_comment_for_gerrit(
        author_username: str, message: str) -> str:

    return "%s@github wrote:\n\n%s" % (
        author_username, message
    )


class GerritComments:
    """operations and services specific to the list of comments on a
    gerrit review."""

    def __init__(self, gerrit_api: gerrit.GerritApi, change: str) -> None:
        gerrit_comments = gerrit_api.\
            get_change_standalone_comments(change)['messages']

        for gerrit_comment in gerrit_comments:
            gerrit_comment['line_comments'] = []
        self._lead_comments = gerrit_comments
        self._gerrit_comments_by_timestamp = {
            gerrit_comment['date']: gerrit_comment
            for gerrit_comment in gerrit_comments
        }
        self._gerrit_comments_by_id = {
            gerrit_comment['id']: gerrit_comment
            for gerrit_comment in gerrit_comments
        }

        gerrit_inline_comments = gerrit_api.get_change_inline_comments(
            change)

        self._gerrit_comments_by_id.update({
            gerrit_inline_comment['id']: gerrit_inline_comment
            for gerrit_file_comments in gerrit_inline_comments.values()
            for gerrit_inline_comment in gerrit_file_comments
        })
        for path, gerrit_file_comments in gerrit_inline_comments.items():
            for gerrit_file_comment in gerrit_file_comments:
                timestamp = gerrit_file_comment["updated"]
                lead_comment = self._gerrit_comments_by_timestamp[timestamp]
                lead_comment['line_comments'].append(gerrit_file_comment)
                gerrit_file_comment['path'] = path
                gerrit_file_comment['parent_id'] = lead_comment['id']

    def __iter__(self) -> Iterable[gerrit.GerritJsonRec]:
        return iter(self._lead_comments)

    def _compare_message(self, message: str, hook_message: str) -> bool:
        return re.sub(r'\\.|\n|\t', '', message) == \
            re.sub(r'\\.|\n|\t', '', hook_message)

    def most_recent_comment_matching(
            self, username: str, text: str) -> Optional[gerrit.GerritJsonRec]:
        # comments are in ascending timestamp so go in reverse order.
        # it's *easy* to find dupes here because per-line reviews are often
        # left without a main comment body.
        for lead_gerrit_comment in reversed(self._lead_comments):
            if lead_gerrit_comment["author"]["username"] == username and \
                    self._compare_message(
                        lead_gerrit_comment["message"], text):
                return lead_gerrit_comment

        return None

    def get_comment_by_id(self, id: str) -> Optional[gerrit.GerritJsonRec]:
        return self._gerrit_comments_by_id.get(id)


class GithubPullRequest:
    """operations and services specific to a pull request record."""

    def __init__(
            self, gh_repo: github.GithubRepo, issue_num: str,
            existing_pullreq: Optional[github.GithubJsonRec]=None) -> None:
        self.gh_repo = gh_repo
        self.number = issue_num
        if existing_pullreq is not None:
            self._pullreq = existing_pullreq

    @memoized_property
    def _pullreq(self) -> github.GithubJsonRec:
        return self.gh_repo.get_pull_request(self.number)


    def convert_gerrit_line_number(
            self, line: GerritReviewLine) -> Optional[GithubReviewPosition]:
        return self._gerrit_line_index.get(line)

    def convert_github_line_position(
            self, position: GithubReviewPosition) -> \
            Optional[GerritReviewLine]:
        return self._gerrit_line_index.get(position)

    _PositionMapType = Dict[
        Union[GithubReviewPosition, GerritReviewLine],
        Union[GithubReviewPosition, GerritReviewLine]
    ]

    @memoized_property
    def _gerrit_line_index(self) -> _PositionMapType:
        return self._create_github_position_map(
            self.gh_repo.get_pull_request_diff(self.number)
        )

    def _create_github_position_map(
            self, unified_diff_text: str) -> _PositionMapType:
        line_index: GithubPullRequest._PositionMapType = {}
        for patch in unidiff.PatchSet(unified_diff_text):
            # github measures position relative to @@ per file,
            # so create an offset for this file
            line_offset = patch[0][0].diff_line_no - 1
            for hunk in patch:
                for line in hunk:
                    # github position =
                    # path, position in diff file for that path

                    # gerrit position =
                    # path, position in file, parent or current revision side

                    github_position = GithubReviewPosition(
                        patch.path, line.diff_line_no - line_offset)

                    if line.source_line_no is not None:
                        gerrit_source_line = GerritReviewLine(
                            patch.path, line.source_line_no, True)
                        line_index[gerrit_source_line] = github_position


                    if line.target_line_no is not None:
                        gerrit_target_line = GerritReviewLine(
                            patch.path, line.target_line_no, False)

                        line_index[github_position] = gerrit_target_line

                        line_index[gerrit_target_line] = github_position
                    elif line.source_line_no is not None:
                        line_index[github_position] = gerrit_source_line

        return line_index

    @memoized_property
    def _comment_index(self) -> Dict[
                GithubReviewPosition, List[github.GithubJsonRec]]:

        comments = self.gh_repo.get_pull_request_comments(self.number)
        _comment_index : Dict[
                GithubReviewPosition, List[github.GithubJsonRec]] = \
            collections.defaultdict(list)

        for comment in comments:
            _comment_index[
                GithubReviewPosition(comment['path'], comment['position'])
            ].append(comment)

        return _comment_index

    def get_head_sha(self) -> str:
        return str(self._pullreq['head']['sha'])

    def get_lead_review_comment(self, position: GithubReviewPosition) -> \
        Optional[github.GithubJsonRec]:

        comments = self._comment_index.get(position, [])
        for c in comments:
            # for now, return the first comment in the list of comments
            # that has no reply-to, have the reply be to that.
            # for greater accuracy, we can try to compare the text of the
            # gerrit lead comment to the github comment.
            if not c.get('in_reply_to_id'):
                return c
        else:
            return None


