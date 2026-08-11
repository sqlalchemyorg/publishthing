"""Close pull requests that weren't authorized by a labeled issue.

A pull request is authorized if it references an issue in the same repo
that is open and carries the "open for pull requests" label.  Anything
else is commented on and closed as soon as it is opened.

Registered from the webhook wsgi app alongside the other handlers::

    prgate.github_hook(
        thing,
        repos={
            "sqlalchemy/testgerrit": {
                "label": "open for pull requests",
                "exempt_maintainers": False,
            },
        },
    )

Register this *before* prtogerrit's hook: handlers run in registration
order, and prtogerrit stamps a "waiting for a reviewer" status on every
opened pull request, which is noise on one the gate is about to close.

"""

from typing import Any
from typing import Dict
from typing import Optional

from . import messages
from . import util
from ... import github
from ... import publishthing
from ... import wsgi

RepoConfig = Dict[str, Dict[str, Any]]


def github_hook(
    thing: publishthing.PublishThing,
    repos: RepoConfig,
    close_pull_requests: bool = True,
) -> None:
    """Register the pull request gate.

    ``repos`` maps "owner/name" to a config dict; one wsgi app serves
    every project, so a repo absent from this mapping is ignored
    entirely.  Recognized keys are "label", "review_label", "deny_label",
    "approved_label", "exempt_maintainers" and "policy_url".

    ``close_pull_requests=False`` leaves the comment but doesn't close,
    which is the way to defang the gate from configuration alone if the
    reference parsing ever misbehaves in production.

    """

    @thing.github_webhook.event(  # type: ignore
        "pull_request", util.pr_is_opened_or_reopened
    )
    def gate_pull_request(
        event: github.GithubEvent, request: wsgi.WsgiRequest
    ) -> None:

        entry = repos.get(event.repo_name)
        if entry is None:
            return

        label = entry.get("label", util.DEFAULT_LABEL)
        review_label = entry.get("review_label", util.DEFAULT_REVIEW_LABEL)
        deny_label = entry.get("deny_label", util.DEFAULT_DENY_LABEL)
        approved_label = entry.get(
            "approved_label", util.DEFAULT_APPROVED_LABEL
        )
        pull_request = event.json_data["pull_request"]
        number = str(event.json_data["number"])
        sender = event.json_data["sender"]["login"]

        gh_repo = thing.github_repo(event.repo_name)

        def holds_claim(issue_number: int) -> bool:
            return _holds_claim(gh_repo, number, issue_number)

        result = util.evaluate_pr(
            gh_repo,
            event.repo_name,
            sender,
            pull_request["title"],
            pull_request["body"],
            label=label,
            review_label=review_label,
            deny_label=deny_label,
            approved_label=approved_label,
            # labels on the pull request itself.  an outside submitter
            # can't apply these -- github requires triage permission or
            # above -- which is what makes the override trustworthy.
            pr_labels=[
                rec["name"] for rec in pull_request.get("labels") or ()
            ],
            exempt_maintainers=entry.get("exempt_maintainers", True),
            holds_claim=holds_claim,
        )

        thing.debug(
            "prgate",
            "%s #%s from %s: %s (%s)",
            event.repo_name,
            number,
            sender,
            result.action,
            result.reason,
        )
        request.add_text(
            "prgate: %s #%s %s (%s)",
            event.repo_name,
            number,
            result.action,
            result.reason,
        )

        if result.action == "allow":
            if result.reason == util.ALLOW_QUALIFIED_ISSUE:
                _claim_issue(
                    gh_repo, number, result.issue, label, review_label
                )
                request.add_text(
                    "prgate: claimed issue #%s for #%s",
                    str(result.issue),
                    number,
                )
            return

        message = messages.close_message(
            result,
            label,
            review_label=review_label,
            deny_label=deny_label,
            policy_url=entry.get("policy_url"),
        )

        # github redelivers webhooks, and a reopen re-runs the gate; only
        # skip the comment when we've already said this exact thing on
        # this pull request.  the close below still runs either way, and
        # closing an already-closed pull request is a no-op.
        if _already_commented(gh_repo, number, result.reason):
            thing.debug(
                "prgate",
                "already commented on %s #%s for %s, not repeating",
                event.repo_name,
                number,
                result.reason,
            )
        else:
            # comment before closing: if the close fails we'd rather have
            # an explained open pull request than a silent closed one.
            gh_repo.publish_issue_comment(number, message)

        if close_pull_requests:
            gh_repo.set_pull_request_status(number, closed=True)
        else:
            request.add_text("prgate: close disabled, leaving open")


def _already_commented(
    gh_repo: github.GithubRepo, number: str, reason: str
) -> bool:
    marker = messages.marker_match(reason)
    for comment in gh_repo.get_issue_comments(number):
        if marker in (comment.get("body") or ""):
            return True
    return False


def _holds_claim(
    gh_repo: github.GithubRepo, number: str, issue_number: int
) -> bool:
    """True if this pull request is the one that claimed the issue."""

    marker = messages.claim_marker(issue_number)
    for comment in gh_repo.get_issue_comments(number):
        if marker in (comment.get("body") or ""):
            return True
    return False


def _claim_issue(
    gh_repo: github.GithubRepo,
    number: str,
    issue_number: Optional[int],
    label: str,
    review_label: str,
) -> None:
    """Move the issue from "open for pull requests" to "in review".

    Adding the review label before removing the other one means that a
    failure between the two leaves the issue closed to new pull requests
    rather than open to all of them, which is the safer way to land.

    Nothing ever puts the labels back automatically: pull requests here
    are merged from gerrit rather than on github, so every pull request
    ends up closed-not-merged and "the pull request was closed" carries
    no signal about whether the work is done.

    """

    if issue_number is None:
        return

    issue = str(issue_number)
    gh_repo.add_issue_labels(issue, [review_label])
    gh_repo.remove_issue_label(issue, label)
    gh_repo.publish_issue_comment(
        number, messages.accepted_message(issue_number, label, review_label)
    )
