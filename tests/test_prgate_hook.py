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


def test_authorized_pr_is_left_alone():
    gh_repo = FakeRepo({5: {"state": "open", "labels": [{"name": LABEL}]}})
    run(gh_repo, make_event(body="Fixes: #5"))

    assert gh_repo.closed == []
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


def test_new_commit_gets_a_fresh_comment():
    gh_repo = FakeRepo(
        comments=[messages.marker(util.CLOSE_NO_ISSUE, "different-sha")]
    )
    run(gh_repo, make_event(body="no reference"))

    assert len(gh_repo.comments) == 2


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


def test_marker_distinguishes_reason_and_sha():
    assert messages.marker("no_issue", "aaa") != messages.marker(
        "no_issue", "bbb"
    )
    assert messages.marker("no_issue", "aaa") != messages.marker(
        "issue_closed", "aaa"
    )


def test_already_commented_matches_only_its_own_marker():
    gh_repo = FakeRepo(comments=["a normal human comment mentioning #5"])
    assert not prgate_github._already_commented(
        gh_repo, "7", util.CLOSE_NO_ISSUE, SHA
    )
