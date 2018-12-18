from typing import NamedTuple
import re
from typing import Callable
from typing import Optional
from typing import Dict
from typing import Tuple
from typing import Iterable
from typing import Iterator
from typing import List
import unidiff
import collections

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




def format_gerrit_comment_for_github(
        author_fullname: str, author_username: str, message: str) -> str:

    # strip out email address if present, some events include it
    if "@" in author_fullname:
        author_fullname = re.sub(
            r'<?[\w_\.]+@[\w_\.]+>?', '', author_fullname).strip()

    return "**%s** (%s) wrote:\n\n%s" % (
        author_fullname, author_username, message
    )


class GerritComments:
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


class GithubPullRequestComments:
    def __init__(self, gh_repo: github.GithubRepo, issue_num: str) -> None:
        self.number = issue_num
        self._pullreq = gh_repo.get_pull_request(issue_num)

        comments = gh_repo.get_pull_request_comments(issue_num)
        self._comment_index : Dict[
                Tuple[str, int], List[github.GithubJsonRec]] = \
            collections.defaultdict(list)

        for comment in comments:
            self._comment_index[
                (comment['path'], comment['position'])].append(comment)

        self._gerrit_line_index = self._create_github_position_map(
            gh_repo.get_pull_request_diff(issue_num)
        )

    def get_head_sha(self) -> str:
        return self._pullreq['head']['sha']

    def convert_gerrit_line_number(
            self, path: str, line_number: int,
            is_parent: bool) -> Optional[int]:
        return self._gerrit_line_index.get((path, line_number, is_parent))

    def get_lead_review_comment(self, path: str, line_number: int) -> \
        Optional[github.GithubJsonRec]:

        comments = self._comment_index.get((path, line_number), [])
        for c in comments:
            # for now, return the first comment in the list of comments
            # that has no reply-to, have the reply be to that.
            # for greater accuracy, we can try to compare the text of the
            # gerrit lead comment to the github comment.
            if not c.get('in_reply_to_id'):
                return c
        else:
            return None

    def _create_github_position_map(
            self, unified_diff_text: str) -> Dict[Tuple[str, int, bool], int]:
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

