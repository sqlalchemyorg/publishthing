"""Tests for the pull request gate decision logic.

This code closes other people's pull requests with no human in the loop,
so the parsing and the decision tree are covered directly; the webhook
wiring around them is thin enough to review by eye.

"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from publishthing.apps.prgate import util
import pytest

REPO = "sqlalchemy/testgerrit"
LABEL = "open for pull requests"
REVIEW_LABEL = "code review in progress"


class FakeRepo:
    """Stands in for GithubRepo, serving canned issue records."""

    def __init__(
        self,
        issues: Optional[Dict[int, Dict[str, Any]]] = None,
        permission: Optional[str] = None,
    ) -> None:
        self.issues = issues or {}
        self.permission = permission
        self.requested: List[str] = []

    def get_user_permission(self, username: str) -> Optional[Dict[str, Any]]:
        if self.permission is None:
            return None
        return {"permission": self.permission}

    def get_issue(self, issue_number: str) -> Optional[Dict[str, Any]]:
        self.requested.append(issue_number)
        return self.issues.get(int(issue_number))


def issue(
    state: str = "open",
    labels: Optional[List[str]] = None,
    is_pull_request: bool = False,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "state": state,
        "labels": [{"name": name} for name in labels or ()],
    }
    if is_pull_request:
        rec["pull_request"] = {"url": "https://example.com/"}
    return rec


@pytest.mark.parametrize(
    "title,body,expected",
    [
        ("fix a thing", "Fixes: #123", [123]),
        ("fix a thing", "closes #123", [123]),
        ("fix #7 in the title", "", [7]),
        ("fix", "see #12 and also #34", [12, 34]),
        ("fix", "#5 #5 #5", [5]),
        # order of appearance is preserved
        ("fix", "#9 then #2", [9, 2]),
        # issue urls for this repo count
        (
            "fix",
            "https://github.com/sqlalchemy/testgerrit/issues/42",
            [42],
        ),
        # ...but another project's tracker says nothing about ours
        ("fix", "https://github.com/pallets/flask/issues/42", []),
        # a pull request url is not an issue reference
        (
            "fix",
            "https://github.com/sqlalchemy/testgerrit/pull/42",
            [],
        ),
        # fully qualified same-repo shorthand
        ("fix", "sqlalchemy/testgerrit#77", [77]),
        # cross-repo shorthand is not ours
        ("fix", "pallets/flask#77", []),
        ("fix", "", []),
        ("fix", None, []),
        (None, None, []),
    ],
)
def test_find_issue_references(title, body, expected):
    assert util.find_issue_references(title, body, REPO) == expected


@pytest.mark.parametrize(
    "body",
    [
        # tracebacks and log output are full of things that look like
        # issue references
        "```\nERROR at #123 in the log\n```",
        "~~~\n#123\n~~~",
        "here is code: `see #123`",
        # the pull request template's own instructions live in an html
        # comment and may show an example
        "<!-- for example, Fixes: #123 -->",
        # a url fragment is not an issue reference
        "https://github.com/sqlalchemy/testgerrit/blob/main/foo.py#L123",
        # prtogerrit's badge must not be mistaken for one
        "Pull-request: https://github.com/sqlalchemy/testgerrit/pull/123\n"
        "Pull-request-sha: abc123",
    ],
)
def test_find_issue_references_ignores_noncontent(body):
    assert util.find_issue_references("fix a thing", body, REPO) == []


def test_unedited_pull_request_template_finds_nothing():
    """The shipped template must not itself reference a real issue.

    A template that carried a literal "Fixes: #1234" outside an html
    comment would resolve against whatever issue happens to have that
    number, and would authorize any pull request left unedited.

    """

    body = """\
**This project accepts pull requests only for issues that a maintainer has
marked with the `open for pull requests` label.**

### Fixes

<!-- Put the issue number after the "#" below, for example "Fixes: #1234".
     The issue must be open and must carry the "open for pull requests"
     label, or this pull request will be closed automatically. -->

Fixes: #

### Description

<!-- Describe your changes in detail. -->
"""
    assert util.find_issue_references("my change", body, REPO) == []


def test_fenced_code_does_not_swallow_later_references():
    body = "```\n#111\n```\n\nreally Fixes: #222"
    assert util.find_issue_references("fix", body, REPO) == [222]


def test_maintainer_is_exempt():
    repo = FakeRepo(permission="write")
    result = util.evaluate_pr(repo, REPO, "zzzeek", "fix", "", label=LABEL)
    assert result == util.GateResult("allow", util.ALLOW_MAINTAINER, None)
    # exempt without ever looking at issues
    assert repo.requested == []


def test_admin_is_exempt():
    repo = FakeRepo(permission="admin")
    result = util.evaluate_pr(repo, REPO, "zzzeek", "fix", "", label=LABEL)
    assert result.action == "allow"


def test_read_permission_is_not_exempt():
    repo = FakeRepo(permission="read")
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "", label=LABEL)
    assert result == util.GateResult("close", util.CLOSE_NO_ISSUE, None)


def test_exemption_can_be_disabled():
    repo = FakeRepo(permission="admin")
    result = util.evaluate_pr(
        repo,
        REPO,
        "zzzeek",
        "fix",
        "",
        label=LABEL,
        exempt_maintainers=False,
    )
    assert result == util.GateResult("close", util.CLOSE_NO_ISSUE, None)


def test_no_issue_reference_closes():
    repo = FakeRepo()
    result = util.evaluate_pr(
        repo, REPO, "someone", "fix a thing", "no reference here", LABEL
    )
    assert result == util.GateResult("close", util.CLOSE_NO_ISSUE, None)


def test_open_labeled_issue_allows():
    repo = FakeRepo({5: issue(labels=[LABEL])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "Fixes: #5", LABEL)
    assert result == util.GateResult("allow", util.ALLOW_QUALIFIED_ISSUE, 5)


def test_label_match_is_case_insensitive():
    repo = FakeRepo({5: issue(labels=["Open For Pull Requests"])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "Fixes: #5", LABEL)
    assert result.action == "allow"


def test_open_unlabeled_issue_closes():
    repo = FakeRepo({5: issue(labels=["bug"])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "Fixes: #5", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_UNLABELED, 5)


def test_closed_labeled_issue_closes():
    repo = FakeRepo({5: issue(state="closed", labels=[LABEL])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "Fixes: #5", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_CLOSED, 5)


def test_nonexistent_issue_closes_as_no_issue():
    repo = FakeRepo({})
    result = util.evaluate_pr(
        repo, REPO, "someone", "fix", "Fixes: #999", LABEL
    )
    assert result == util.GateResult("close", util.CLOSE_NO_ISSUE, None)


def test_reference_to_a_pull_request_is_not_an_issue():
    repo = FakeRepo({5: issue(labels=[LABEL], is_pull_request=True)})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "see #5", LABEL)
    assert result == util.GateResult("close", util.CLOSE_NO_ISSUE, None)


def test_first_qualifying_issue_wins():
    repo = FakeRepo(
        {
            1: issue(labels=["bug"]),
            2: issue(state="closed", labels=[LABEL]),
            3: issue(labels=[LABEL]),
        }
    )
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#1 #2 #3", LABEL)
    assert result == util.GateResult("allow", util.ALLOW_QUALIFIED_ISSUE, 3)


def test_evaluation_stops_at_the_qualifying_issue():
    repo = FakeRepo({1: issue(labels=[LABEL]), 2: issue(labels=[LABEL])})
    util.evaluate_pr(repo, REPO, "someone", "fix", "#1 #2", LABEL)
    assert repo.requested == ["1"]


def test_most_specific_close_reason_is_reported():
    # an issue that exists but lacks the label is more actionable than
    # one that's closed, which is more actionable than nothing at all
    repo = FakeRepo(
        {
            1: issue(state="closed", labels=[LABEL]),
            2: issue(labels=["bug"]),
        }
    )
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#1 #2", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_UNLABELED, 2)


def test_close_reason_falls_back_when_only_closed_issues():
    repo = FakeRepo({1: issue(state="closed", labels=[LABEL])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#1", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_CLOSED, 1)


def test_issue_in_review_closes():
    repo = FakeRepo({5: issue(labels=[REVIEW_LABEL])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#5", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_IN_REVIEW, 5)


def test_in_review_is_the_most_specific_close_reason():
    # "someone is already working on this" is more useful to a
    # contributor than "nobody has authorized this yet"
    repo = FakeRepo(
        {
            1: issue(state="closed", labels=[LABEL]),
            2: issue(labels=["bug"]),
            3: issue(labels=[REVIEW_LABEL]),
        }
    )
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#1 #2 #3", LABEL)
    assert result == util.GateResult("close", util.CLOSE_ISSUE_IN_REVIEW, 3)


def test_claim_holder_is_allowed_back_in():
    """The reopen path for a pull request that already claimed its issue.

    By the time it is reopened the issue no longer carries the label
    that let it through, so without the claim the gate would close the
    very pull request it previously accepted.

    """

    repo = FakeRepo({5: issue(labels=[REVIEW_LABEL])})
    result = util.evaluate_pr(
        repo,
        REPO,
        "someone",
        "fix",
        "#5",
        LABEL,
        holds_claim=lambda issue_number: issue_number == 5,
    )
    assert result == util.GateResult("allow", util.ALLOW_EXISTING_CLAIM, 5)


def test_claim_on_a_different_issue_does_not_let_you_in():
    repo = FakeRepo({5: issue(labels=[REVIEW_LABEL])})
    result = util.evaluate_pr(
        repo,
        REPO,
        "someone",
        "fix",
        "#5",
        LABEL,
        holds_claim=lambda issue_number: issue_number == 99,
    )
    assert result == util.GateResult("close", util.CLOSE_ISSUE_IN_REVIEW, 5)


def test_open_for_prs_wins_over_in_review():
    # if both labels are somehow present, the issue is open for work
    repo = FakeRepo({5: issue(labels=[LABEL, REVIEW_LABEL])})
    result = util.evaluate_pr(repo, REPO, "someone", "fix", "#5", LABEL)
    assert result == util.GateResult("allow", util.ALLOW_QUALIFIED_ISSUE, 5)


def test_review_label_is_configurable():
    repo = FakeRepo({5: issue(labels=["under review"])})
    result = util.evaluate_pr(
        repo, REPO, "someone", "fix", "#5", LABEL, review_label="under review"
    )
    assert result == util.GateResult("close", util.CLOSE_ISSUE_IN_REVIEW, 5)
