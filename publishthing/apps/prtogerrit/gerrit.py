
from typing import Any

from ... import gerrit
from ... import publishthing
from . import util


def gerrit_hook(thing: publishthing.PublishThing) -> None:

    # unfortunately there's no gerrit event for "vote removed"
    # in the hooks plugin, even though "stream events" has it.
    # this will cause the github status to be wrong until another comment
    # corrects it.
    @thing.gerrit_hook.event("patchset-created")   # type: ignore
    @thing.gerrit_hook.event(  # type: ignore
        "comment-added",
        util.gerrit_comment_includes_verify)
    def verified_status_changed(opts: gerrit.GerritHookEvent) -> None:
        pull_request_match = util.get_pullreq_for_gerrit_change(thing, opts)

        if pull_request_match is None:
            return

        detail = thing.gerrit_api.get_change_detail(opts.change)

        verified = detail["labels"]["Verified"]
        verified_approved = "approved" in verified
        verified_rejected = "rejected" in verified
        verified_neutral = not verified_approved and not verified_rejected

        codereview = detail["labels"]["Code-Review"]
        codereview_approved = "approved" in codereview
        codereview_rejected = (
            "disliked" in codereview or "rejected" in codereview
        )
        codereview_neutral = (
            not codereview_approved and not codereview_rejected
        )

        gh_repo = thing.github_repo(opts.project)

        for send, context, state, message in [
            (verified_approved,
             "ci_verification", "success", "Gerrit review has been verified"),
            (verified_rejected, "ci_verification", "failure",
             "Gerrit review has failed verification"),
            (verified_neutral, "ci_verification", "pending",
             "Needs CI verified status"),
            (codereview_approved,
             "code_review", "success", "Received code review +2"),
            (codereview_rejected,
             "code_review", "failure", "Code review has been rejected"),
            (codereview_neutral, "code_review", "pending",
             "Needs code review +2")
        ]:
            if send:
                gh_repo.create_status(
                    pull_request_match.sha,
                    description=message,
                    state=state,
                    context=context,
                    target_url=opts.change_url
                )

    @thing.gerrit_hook.event("change-merged")   # type: ignore
    @thing.gerrit_hook.event("change-abandoned")   # type: ignore
    @thing.gerrit_hook.event("change-deleted")   # type: ignore
    @thing.gerrit_hook.event("change-restored")   # type: ignore
    def change_merged_or_abandoned(opts: Any) -> None:
        pull_request_match = util.get_pullreq_for_gerrit_change(thing, opts)

        if pull_request_match is None:
            return

        gh_repo = thing.github_repo(opts.project)

        if opts.hook == "change-merged":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **merged**. "
                "Congratulations! :)" % opts.change_url
            )
        elif opts.hook == "change-deleted":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **deleted**. Hmm, maybe "
                "the admins are doing something here." % opts.change_url
            )
        elif opts.hook == "change-abandoned":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **abandoned**.  That means that "
                "at least for the moment I need to close this pull request. "
                "Sorry it didn't work out :(" % opts.change_url
            )
            gh_repo.set_pull_request_status(
                pull_request_match.number, closed=True)
        elif opts.hook == "change-restored":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **restored**.  That means "
                "I can reopen this pull request!  Hooray :)" % (
                    opts.change_url
                )
            )
            gh_repo.set_pull_request_status(
                pull_request_match.number, closed=False)

