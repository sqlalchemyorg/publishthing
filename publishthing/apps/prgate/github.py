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
    "approved_label", "exempt_maintainers", "exempt_users",
    "approve_on_maintainer_reopen" and "policy_url".

    With "approve_on_maintainer_reopen" (the default), a maintainer
    reopening a pull request the gate would close counts as approval:
    an unlabeled issue it references is claimed as though it had been
    labeled, and otherwise the pull request gets the approved label.
    Either way the maintainer is told what was done.

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
        # the pull request's author, not the event's sender.  on a reopen
        # the sender is whoever pressed the button, often a maintainer,
        # and gating on that would let any pull request a maintainer
        # reopens through on the maintainer exemption without ever
        # claiming its issue.
        author = pull_request["user"]["login"]
        is_reopen = event.json_data["action"] == "reopened"
        sha = pull_request["head"]["sha"]

        gh_repo = thing.github_repo(event.repo_name)

        def holds_claim(issue_number: int) -> bool:
            return _holds_claim(gh_repo, number, issue_number)

        result = util.evaluate_pr(
            gh_repo,
            event.repo_name,
            author,
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
            exempt_users=entry.get("exempt_users", ()),
            holds_claim=holds_claim,
        )

        thing.debug(
            "prgate",
            "%s #%s from %s: %s (%s)",
            event.repo_name,
            number,
            author,
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

        approve_on_reopen = entry.get("approve_on_maintainer_reopen", True)
        if approve_on_reopen and util.is_maintainer_reopen(
            gh_repo, event, author
        ):
            _approve_on_reopen(
                gh_repo,
                request,
                number,
                result,
                event.json_data["sender"]["login"],
                sha,
                label=label,
                review_label=review_label,
                deny_label=deny_label,
                approved_label=approved_label,
            )
            return

        message = messages.close_message(
            result,
            label,
            sha,
            review_label=review_label,
            deny_label=deny_label,
            policy_url=entry.get("policy_url"),
        )

        # github redelivers webhooks, and a reopen re-runs the gate; only
        # skip the comment when we've already said this exact thing about
        # this exact commit.  the close below still runs either way, and
        # closing an already-closed pull request is a no-op.
        if _already_commented(gh_repo, number, result.reason, sha):
            thing.debug(
                "prgate",
                "already commented on %s #%s for sha %s, not repeating",
                event.repo_name,
                number,
                sha,
            )
            # a reopen that changed nothing still gets a word, so the
            # pull request isn't closed again silently.  skip it only if
            # it's already the latest comment, i.e. a redelivery.
            still_closed = messages.marker(messages.REOPEN_STILL_CLOSED, sha)
            if is_reopen and not _last_comment_has(
                gh_repo, number, still_closed
            ):
                gh_repo.publish_issue_comment(
                    number, messages.reopen_still_closed_message(sha)
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
    gh_repo: github.GithubRepo, number: str, reason: str, sha: str
) -> bool:
    marker = messages.marker(reason, sha)
    for comment in gh_repo.get_issue_comments(number):
        if marker in (comment.get("body") or ""):
            return True
    return False


def _last_comment_has(
    gh_repo: github.GithubRepo, number: str, marker: str
) -> bool:
    last = None
    for comment in gh_repo.get_issue_comments(number):
        last = comment
    return last is not None and marker in (last.get("body") or "")


def _approve_on_reopen(
    gh_repo: github.GithubRepo,
    request: wsgi.WsgiRequest,
    number: str,
    result: util.GateResult,
    sender: str,
    sha: str,
    label: str,
    review_label: str,
    deny_label: str,
    approved_label: str,
) -> None:
    """Treat a maintainer's reopen as approval, and say so.

    An open issue that simply lacks the label is claimed, the same as if
    the maintainer had labeled it first.  Anything else -- no issue, a
    closed one, one already in review or denied -- isn't something the
    reopen can fix on the issue side, so the pull request gets the
    approved label instead, which also keeps it open across later
    close/reopen cycles.

    """

    if (
        result.reason == util.CLOSE_ISSUE_UNLABELED
        and result.issue is not None
    ):
        issue = str(result.issue)
        gh_repo.add_issue_labels(issue, [review_label])
        gh_repo.remove_issue_label(issue, label)
        gh_repo.publish_issue_comment(
            number,
            messages.reopen_claimed_message(
                result.issue, sender, label, review_label
            ),
        )
        request.add_text(
            "prgate: reopened by maintainer %s, claimed issue #%s for #%s",
            sender,
            issue,
            number,
        )
        return

    gh_repo.add_issue_labels(number, [approved_label])
    # github redelivers webhooks; don't repeat the comment if it's
    # already the latest one.
    if not _last_comment_has(
        gh_repo, number, messages.marker(messages.REOPEN_APPROVED, sha)
    ):
        gh_repo.publish_issue_comment(
            number,
            messages.reopen_approved_message(
                result,
                sender,
                sha,
                approved_label,
                review_label=review_label,
                deny_label=deny_label,
            ),
        )
    request.add_text(
        "prgate: reopened by maintainer %s, approved #%s",
        sender,
        number,
    )


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
