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
    entirely.  Recognized keys are "label", "exempt_maintainers" and
    "policy_url".

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
        pull_request = event.json_data["pull_request"]
        number = str(event.json_data["number"])
        sender = event.json_data["sender"]["login"]
        sha = pull_request["head"]["sha"]

        gh_repo = thing.github_repo(event.repo_name)

        result = util.evaluate_pr(
            gh_repo,
            event.repo_name,
            sender,
            pull_request["title"],
            pull_request["body"],
            label=label,
            exempt_maintainers=entry.get("exempt_maintainers", True),
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
            return

        message = messages.close_message(
            result, label, sha, policy_url=entry.get("policy_url")
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
