import collections
import re
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import NamedTuple
from typing import Optional
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
    thing: publishthing.PublishThing, opts: gerrit.GerritHookEvent
) -> Optional[PullRequestRec]:
    change_commit = thing.gerrit_api.get_change_current_revision(opts.change)

    current_revision = change_commit["current_revision"]
    message = change_commit["revisions"][current_revision]["commit"]["message"]

    pr_num_match = get_pullreq_for_gerrit_commit_message(opts.project, message)
    if pr_num_match is None:
        thing.debug(
            "prtogerrit",
            "Did not locate a pull request in comment for gerrit " "review %s",
            opts.change,
        )
        return None

    thing.debug(
        "prtogerrit",
        "Located pull request %s sha %s in gerrit review %s",
        pr_num_match.number,
        pr_num_match.sha,
        opts.change,
    )
    return pr_num_match


def get_pullreq_for_gerrit_commit_message(
    project: str, commit_message: str
) -> Optional[PullRequestRec]:
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
        opts.Verified is not None and opts.Verified_oldValue is not None
    ) or (
        opts.Code_Review is not None and opts.Code_Review_oldValue is not None
    )


def github_pr_is_opened(event: github.GithubEvent) -> bool:
    return bool(
        event.json_data["action"]
        in ("opened", "edited", "synchronize", "reopened")
    )


def github_pr_review_is_submitted(event: github.GithubEvent) -> bool:
    return bool(event.json_data["action"] == "submitted")


def github_pr_comment_is_created(event: github.GithubEvent) -> bool:
    return bool(event.json_data["action"] == "created")


def github_comment_is_pullrequest(event: github.GithubEvent) -> bool:
    return bool(event.json_data["issue"].get("pull_request"))


def github_pr_is_authorized_reviewer_request(
    thing: publishthing.PublishThing, wait_for_reviewer: str
) -> Callable[[github.GithubEvent], bool]:
    def is_reviewer_request(event: github.GithubEvent) -> bool:
        is_review_request = event.json_data[
            "action"
        ] == "review_requested" and wait_for_reviewer in {
            rec["login"]
            for rec in event.json_data["pull_request"]["requested_reviewers"]
        }

        if not is_review_request:
            return False

        review_requester = event.json_data["sender"]["login"]

        gh_repo = thing.github_repo(event.repo_name)
        permission_json = gh_repo.get_user_permission(review_requester)
        is_authorized = permission_json and permission_json["permission"] in (
            "admin",
            "write",
        )

        if not is_authorized:
            is_review_request = False
            owner, project = event.repo_name.split("/")

            gh_repo.create_pr_review(
                event.json_data["number"],
                "Hi, this is **%s** and I see you've pinged me for review. "
                "However, user **%s** is not authorized to initiate CI jobs.  "
                "Please wait for a project member to do this!"
                % (wait_for_reviewer, review_requester),
                event="COMMENT",
            )

        return is_review_request

    return is_reviewer_request


def format_gerrit_comment_for_github(
    change_url: str, author_fullname: str, author_username: str, message: str
) -> str:

    # strip out email address if present, some events include it
    if "@" in author_fullname:
        author_fullname = re.sub(
            r"<?[\w_\.]+@[\w_\.]+>?", "", author_fullname
        ).strip('" ')

    message = re.sub(r"^\s*Patch Set \d+:\s*", "", message)

    return "**%s** (%s) wrote:\n\n%s\n\nView this in Gerrit at %s" % (
        author_fullname,
        author_username,
        message,
        change_url,
    )


def format_github_comment_for_gerrit(
    author_username: str, message: str
) -> str:

    return "%s@github wrote:\n\n%s" % (author_username, message)


class GerritComments:
    """operations and services specific to the list of comments on a
    gerrit review."""

    def __init__(self, gerrit_api: gerrit.GerritApi, change: str) -> None:

        # this API call is still the one that gives us text that will
        # be in what the command line hook sends us, so still using this.
        gerrit_messages = gerrit_api.get_change_standalone_comments(change)[
            "messages"
        ]

        _change_message_id_to_msg = {
            msg["id"]: msg["message"] for msg in gerrit_messages
        }

        # in gerrit 3.3, we can get all the comment data with this API request;
        # previously the "non file" comments weren't here (or maybe I just
        # missed them)
        gerrit_inline_comments = gerrit_api.get_change_inline_comments(change)

        # We want to organize the comments into:
        #
        # patch level comment
        #       |
        #       +--> file comment
        #       +--> file comment
        # etc.

        gerrit_comments_by_change_message_id = {}
        for file_, items in gerrit_inline_comments.items():
            for item in items:
                change_message_id = item["change_message_id"]

                if change_message_id in gerrit_comments_by_change_message_id:
                    lead_comment = gerrit_comments_by_change_message_id[
                        change_message_id
                    ]
                else:
                    lead_comment = gerrit_comments_by_change_message_id[
                        change_message_id
                    ] = {
                        "change_message_id": change_message_id,
                        "patch_set": item["patch_set"],
                        "updated": item["updated"],
                        "commit_id": item["commit_id"],
                        "line_comments": [],
                        "author": item["author"],
                        "command_line_message": _change_message_id_to_msg[
                            change_message_id
                        ],
                    }
                if "line" in item:
                    lead_comment["line_comments"].append(item)
                    item["path"] = file_
                else:
                    assert "message" not in lead_comment
                    lead_comment.update(item)

        self._lead_comments = sorted(
            gerrit_comments_by_change_message_id.values(),
            key=lambda item: item["updated"],
        )

    def __iter__(self) -> Iterable[gerrit.GerritJsonRec]:
        return iter(self._lead_comments)

    def _compare_message(self, message: str, hook_message: str) -> bool:
        return re.sub(
            r"(?:^Patch Set \d+\:)|\\.|\n|\t", "", message
        ) == re.sub(r"(?:^Patch Set \d+\:)|\\.|\n|\t", "", hook_message)

    def most_recent_comment_matching(
        self, username: str, text: str
    ) -> Optional[gerrit.GerritJsonRec]:
        # comments are in ascending timestamp so go in reverse order.
        # it's *easy* to find dupes here because per-line reviews are often
        # left without a main comment body.

        for lead_gerrit_comment in reversed(self._lead_comments):
            if (
                self._compare_message(
                    lead_gerrit_comment["command_line_message"], text
                )
                or "message" in lead_gerrit_comment
                and self._compare_message(lead_gerrit_comment["message"], text)
            ):
                return lead_gerrit_comment

        return None


class GithubPullRequest:
    """operations and services specific to a pull request record."""

    def __init__(
        self,
        gh_repo: github.GithubRepo,
        issue_num: str,
        existing_pullreq: Optional[github.GithubJsonRec] = None,
    ) -> None:
        self.gh_repo = gh_repo
        self.number = issue_num
        if existing_pullreq is not None:
            self._pullreq = existing_pullreq

    @memoized_property
    def _pullreq(self) -> github.GithubJsonRec:
        return self.gh_repo.get_pull_request(self.number)

    def convert_gerrit_line_number(
        self, line: GerritReviewLine
    ) -> Optional[GithubReviewPosition]:
        return self._gerrit_line_index.get(line)

    def convert_github_line_position(
        self, position: GithubReviewPosition
    ) -> Optional[GerritReviewLine]:
        return self._gerrit_line_index.get(position)

    _PositionMapType = Dict[
        Union[GithubReviewPosition, GerritReviewLine],
        Union[GithubReviewPosition, GerritReviewLine],
    ]

    @memoized_property
    def _gerrit_line_index(self) -> _PositionMapType:
        return self._create_github_position_map(
            self.gh_repo.get_pull_request_diff(self.number)
        )

    def _create_github_position_map(
        self, unified_diff_text: str
    ) -> _PositionMapType:
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
                        patch.path, line.diff_line_no - line_offset
                    )

                    if line.source_line_no is not None:
                        gerrit_source_line = GerritReviewLine(
                            patch.path, line.source_line_no, True
                        )
                        line_index[gerrit_source_line] = github_position

                    if line.target_line_no is not None:
                        gerrit_target_line = GerritReviewLine(
                            patch.path, line.target_line_no, False
                        )

                        line_index[github_position] = gerrit_target_line

                        line_index[gerrit_target_line] = github_position
                    elif line.source_line_no is not None:
                        line_index[github_position] = gerrit_source_line

        return line_index

    @memoized_property
    def _comment_index(
        self,
    ) -> Dict[GithubReviewPosition, List[github.GithubJsonRec]]:

        comments = self.gh_repo.get_pull_request_comments(self.number)
        _comment_index: Dict[
            GithubReviewPosition, List[github.GithubJsonRec]
        ] = collections.defaultdict(list)

        for comment in comments:
            _comment_index[
                GithubReviewPosition(comment["path"], comment["position"])
            ].append(comment)

        return _comment_index

    def get_head_sha(self) -> str:
        return str(self._pullreq["head"]["sha"])

    def get_lead_review_comment(
        self, position: GithubReviewPosition
    ) -> Optional[github.GithubJsonRec]:

        comments = self._comment_index.get(position, [])
        for c in comments:
            # for now, return the first comment in the list of comments
            # that has no reply-to, have the reply be to that.
            # for greater accuracy, we can try to compare the text of the
            # gerrit lead comment to the github comment.
            if not c.get("in_reply_to_id"):
                return c
        else:
            return None
