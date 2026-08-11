"""Tests for the webhook wiring around the gate decision.

Covers dispatch (which repos and which actions reach the handler) and
the side effects (comment, close, redelivery suppression) without going
near the network.

"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from publishthing.apps import prgate
from publishthing.apps.prgate import github as prgate_github
from publishthing.apps.prgate import messages
from publishthing.apps.prgate import util
import pytest

REPO = "sqlalchemy/testgerrit"
LABEL = "open for pull requests"
REVIEW_LABEL = "code review in progress"
SHA = "abc123"


class FakeRepo:
    def __init__(
        self,
        issues: Optional[Dict[int, Dict[str, Any]]] = None,
        permission: Optional[str] = None,
        comments: Optional[List[str]] = None,
    ) -> None:
        self.issues = issues or {}
        self.permission = permission
        self.comments = list(comments or ())
        self.closed: List[str] = []
        self.labels_added: List[Any] = []
        self.labels_removed: List[Any] = []

    def get_user_permission(self, username: str) -> Optional[Dict[str, Any]]:
        if self.permission is None:
            return None
        return {"permission": self.permission}

    def get_issue(self, issue_number: str) -> Optional[Dict[str, Any]]:
        return self.issues.get(int(issue_number))

    def get_issue_comments(self, issue_number: str) -> List[Dict[str, Any]]:
        return [{"body": body} for body in self.comments]

    def publish_issue_comment(self, issue_number: str, message: str) -> None:
        self.comments.append(message)

    def set_pull_request_status(
        self, issue_number: str, closed: bool = True
    ) -> None:
        self.closed.append(issue_number)

    def add_issue_labels(self, issue_number: str, labels: List[str]) -> None:
        self.labels_added.append((issue_number, labels))
        rec = self.issues.get(int(issue_number))
        if rec is not None:
            rec["labels"] = list(rec.get("labels") or ()) + [
                {"name": name} for name in labels
            ]

    def remove_issue_label(self, issue_number: str, label: str) -> None:
        self.labels_removed.append((issue_number, label))
        rec = self.issues.get(int(issue_number))
        if rec is not None:
            rec["labels"] = [
                item
                for item in rec.get("labels") or ()
                if item["name"] != label
            ]


class FakeWebhook:
    """Records handlers the way the real Hooks registry does."""

    def __init__(self) -> None:
        self.handlers: List[Any] = []

    def event(self, event_name, filter_=None):
        def decorate(fn):
            self.handlers.append((event_name, filter_, fn))
            return fn

        return decorate

    def deliver(self, event, request) -> None:
        for event_name, filter_, fn in self.handlers:
            if event_name != event.event:
                continue
            if filter_ is None or filter_(event):
                fn(event, request)


class FakeThing:
    def __init__(self, gh_repo: FakeRepo) -> None:
        self.github_webhook = FakeWebhook()
        self._gh_repo = gh_repo

    def github_repo(self, repo: str) -> FakeRepo:
        return self._gh_repo

    def debug(self, category: str, message: str, *arg: Any) -> None:
        pass


class FakeRequest:
    def __init__(self) -> None:
        self.text: List[str] = []

    def add_text(self, message: str, *args: Any) -> None:
        self.text.append(message % args if args else message)


def make_event(
    action: str = "opened",
    repo: str = REPO,
    body: str = "",
    sender: str = "someone",
    number: int = 7,
    pr_labels: Optional[List[str]] = None,
) -> Any:
    from publishthing import github as gh

    return gh.GithubEvent(
        {
            "action": action,
            "number": number,
            "repository": {"full_name": repo},
            "sender": {"login": sender},
            "pull_request": {
                "title": "a change",
                "body": body,
                "head": {"sha": SHA},
                "labels": [{"name": name} for name in pr_labels or ()],
            },
        },
        "pull_request",
        "delivery-1",
    )


def run(
    gh_repo: FakeRepo,
    event: Any,
    repos: Optional[Dict[str, Any]] = None,
    close_pull_requests: bool = True,
) -> FakeRequest:
    thing = FakeThing(gh_repo)
    prgate.github_hook(
        thing,
        repos if repos is not None else {REPO: {"label": LABEL}},
        close_pull_requests=close_pull_requests,
    )
    request = FakeRequest()
    thing.github_webhook.deliver(event, request)
    return request


def test_unauthorized_pr_is_commented_and_closed():
    gh_repo = FakeRepo()
    run(gh_repo, make_event(body="no reference"))

    assert gh_repo.closed == ["7"]
    assert len(gh_repo.comments) == 1
    assert messages.MARKER_PREFIX in gh_repo.comments[0]


def test_authorized_pr_stays_open_and_claims_its_issue():
    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})
    run(gh_repo, make_event(body="Fixes: #5"))

    assert gh_repo.closed == []
    assert gh_repo.labels_added == [("5", [REVIEW_LABEL])]
    assert gh_repo.labels_removed == [("5", LABEL)]

    # one comment, on the pull request, carrying the claim
    assert len(gh_repo.comments) == 1
    assert messages.claim_marker(5) in gh_repo.comments[0]


def test_review_label_is_added_before_the_other_is_removed():
    """Failing between the two calls must leave the issue closed to new
    pull requests, not open to all of them."""

    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})
    order: List[str] = []
    gh_repo.add_issue_labels = (  # type: ignore[method-assign]
        lambda issue_number, labels: order.append("add")
    )
    gh_repo.remove_issue_label = (  # type: ignore[method-assign]
        lambda issue_number, label: order.append("remove")
    )

    run(gh_repo, make_event(body="Fixes: #5"))

    assert order == ["add", "remove"]


def test_second_pr_on_a_claimed_issue_is_closed():
    gh_repo = FakeRepo(
        {5: {"state": "open", "labels": [{"name": REVIEW_LABEL}]}}
    )
    run(gh_repo, make_event(body="Fixes: #5", number=11))

    assert gh_repo.closed == ["11"]
    assert util.CLOSE_ISSUE_IN_REVIEW in gh_repo.comments[0]


def test_claim_survives_close_and_reopen():
    """The regression this whole claim mechanism exists for.

    An accepted pull request strips "open for pull requests" from its
    issue; when it is reopened the gate must not then close it for the
    absence of the label it removed itself.

    """

    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})

    run(gh_repo, make_event(body="Fixes: #5"))
    assert gh_repo.closed == []

    # the issue now carries only the review label
    names = {rec["name"] for rec in gh_repo.issues[5]["labels"]}
    assert names == {REVIEW_LABEL}

    run(gh_repo, make_event(action="reopened", body="Fixes: #5"))

    assert gh_repo.closed == []
    # no second claim comment, and no second label swap
    assert len(gh_repo.comments) == 1
    assert len(gh_repo.labels_added) == 1


def test_a_different_pr_cannot_ride_the_first_ones_claim():
    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})
    run(gh_repo, make_event(body="Fixes: #5", number=10))

    # a second pull request has its own, empty, comment list
    other_repo = FakeRepo(
        {5: {"state": "open", "labels": [{"name": REVIEW_LABEL}]}}
    )
    run(other_repo, make_event(body="Fixes: #5", number=11))

    assert other_repo.closed == ["11"]


def test_maintainer_pr_does_not_claim_the_issue():
    # exempt before we ever look at issues, so there's nothing to claim
    gh_repo = FakeRepo(
        {5: {"state": "open", "labels": [{"name": LABEL}]}},
        permission="admin",
    )
    run(gh_repo, make_event(body="Fixes: #5"))

    assert gh_repo.labels_added == []
    assert gh_repo.labels_removed == []
    assert gh_repo.comments == []


def test_repo_not_in_mapping_is_ignored():
    gh_repo = FakeRepo()
    run(
        gh_repo,
        make_event(repo="sqlalchemy/sqlalchemy", body="no reference"),
    )

    assert gh_repo.closed == []
    assert gh_repo.comments == []


@pytest.mark.parametrize("action", ["synchronize", "edited", "closed"])
def test_gate_does_not_fire_on_other_actions(action):
    gh_repo = FakeRepo()
    run(gh_repo, make_event(action=action, body="no reference"))

    assert gh_repo.closed == []
    assert gh_repo.comments == []


@pytest.mark.parametrize("action", ["opened", "reopened"])
def test_gate_fires_on_open_and_reopen(action):
    gh_repo = FakeRepo()
    run(gh_repo, make_event(action=action, body="no reference"))

    assert gh_repo.closed == ["7"]


def test_redelivery_does_not_repeat_the_comment():
    gh_repo = FakeRepo()
    event = make_event(body="no reference")

    run(gh_repo, event)
    run(gh_repo, event)

    # commented once, but closed both times -- closing an already closed
    # pull request is a no-op, and we'd rather be sure it ends up closed
    assert len(gh_repo.comments) == 1
    assert gh_repo.closed == ["7", "7"]


def test_new_commit_does_not_get_a_fresh_comment():
    """A reopen after a push is the same rejection, not a new one.

    The marker used to carry the head sha, so pushing a commit and
    reopening produced a second identical comment.

    """

    gh_repo = FakeRepo(comments=[messages.marker(util.CLOSE_NO_ISSUE)])
    run(gh_repo, make_event(body="no reference"))

    assert len(gh_repo.comments) == 1


def test_comments_from_the_old_sha_bearing_marker_are_recognized():
    gh_repo = FakeRepo(
        comments=["<!-- prgate:%s:some-sha -->" % util.CLOSE_NO_ISSUE]
    )
    run(gh_repo, make_event(body="no reference"))

    assert len(gh_repo.comments) == 1


def test_close_can_be_disabled():
    gh_repo = FakeRepo()
    request = run(
        gh_repo, make_event(body="no reference"), close_pull_requests=False
    )

    assert gh_repo.closed == []
    assert len(gh_repo.comments) == 1
    assert any("close disabled" in line for line in request.text)


def test_maintainer_exemption_is_per_repo():
    gh_repo = FakeRepo(permission="admin")
    run(
        gh_repo,
        make_event(body="no reference"),
        repos={REPO: {"label": LABEL, "exempt_maintainers": False}},
    )

    assert gh_repo.closed == ["7"]


def test_marker_distinguishes_reason():
    assert messages.marker("no_issue") != messages.marker("issue_closed")


def test_already_commented_matches_only_its_own_marker():
    gh_repo = FakeRepo(comments=["a normal human comment mentioning #5"])
    assert not prgate_github._already_commented(
        gh_repo, "7", util.CLOSE_NO_ISSUE
    )


def test_already_commented_does_not_match_a_different_reason():
    gh_repo = FakeRepo(comments=[messages.marker(util.CLOSE_ISSUE_CLOSED)])
    assert not prgate_github._already_commented(
        gh_repo, "7", util.CLOSE_NO_ISSUE
    )


APPROVED_LABEL = "approved for development"
DENY_LABEL = "NO pull requests please"


def test_approved_pr_is_left_open_and_claims_nothing():
    """The escape hatch: a maintainer labels a closed pull request and
    reopens it, and the gate waves it through."""

    gh_repo = FakeRepo()
    run(
        gh_repo,
        make_event(
            action="reopened",
            body="no reference at all",
            pr_labels=[APPROVED_LABEL],
        ),
    )

    assert gh_repo.closed == []
    assert gh_repo.comments == []
    assert gh_repo.labels_added == []


def test_approved_pr_does_not_consult_the_issue():
    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})
    run(
        gh_repo,
        make_event(body="Fixes: #5", pr_labels=[APPROVED_LABEL]),
    )

    # allowed as an override, so the issue is left exactly as it was
    assert gh_repo.closed == []
    assert gh_repo.labels_added == []
    assert gh_repo.labels_removed == []


def test_denied_issue_closes_the_pr():
    gh_repo = FakeRepo(
        {5: {"state": "open", "labels": [{"name": DENY_LABEL}]}}
    )
    run(gh_repo, make_event(body="Fixes: #5"))

    assert gh_repo.closed == ["7"]

    comment = gh_repo.comments[0]
    assert util.CLOSE_ISSUE_DENIED in comment
    assert DENY_LABEL in comment
    # the generic message invites them to wait for the label to be
    # added; on a denied issue that's exactly the wrong instruction
    assert "Once a maintainer adds the label" not in comment
    assert "Wait for a maintainer to add" not in comment
