from typing import Any

from . import util
from ... import gerrit
from ... import publishthing


def gerrit_hook(thing: publishthing.PublishThing) -> None:
    # unfortunately there's no gerrit event for "vote removed"
    # in the hooks plugin, even though "stream events" has it.
    # this will cause the github status to be wrong until another comment
    # corrects it.
    @thing.gerrit_hook.event("patchset-created")  # type: ignore
    @thing.gerrit_hook.event(  # type: ignore
        "comment-added", util.gerrit_comment_includes_verify
    )
    def verified_status_changed(opts: gerrit.GerritHookEvent) -> None:
        """receive events where verified/code-review status can change and
        send status updates to the pull request"""

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
            (
                verified_approved,
                "ci_verification",
                "success",
                "Gerrit review has been verified",
            ),
            (
                verified_rejected,
                "ci_verification",
                "failure",
                "Gerrit review has failed verification",
            ),
            (
                verified_neutral,
                "ci_verification",
                "pending",
                "Needs CI verified status",
            ),
            (
                codereview_approved,
                "code_review",
                "success",
                "Received code review +2",
            ),
            (
                codereview_rejected,
                "code_review",
                "failure",
                "Code review has been rejected",
            ),
            (
                codereview_neutral,
                "code_review",
                "pending",
                "Needs code review +2",
            ),
        ]:
            if send:
                gh_repo.create_status(
                    pull_request_match.sha,
                    description=message,
                    state=state,
                    context=context,
                    target_url=opts.change_url,
                )

    @thing.gerrit_hook.event("change-merged")  # type: ignore
    @thing.gerrit_hook.event("change-abandoned")  # type: ignore
    @thing.gerrit_hook.event("change-deleted")  # type: ignore
    @thing.gerrit_hook.event("change-restored")  # type: ignore
    def change_merged_or_abandoned(opts: Any) -> None:
        pull_request_match = util.get_pullreq_for_gerrit_change(thing, opts)

        if pull_request_match is None:
            return

        gh_repo = thing.github_repo(opts.project)

        if opts.hook == "change-merged":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **merged**. "
                "Congratulations! :)" % opts.change_url,
            )
        elif opts.hook == "change-deleted":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **deleted**. Hmm, maybe "
                "the admins are doing something here." % opts.change_url,
            )
        elif opts.hook == "change-abandoned":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                util.format_gerrit_comment_for_github(
                    opts.change_url,
                    opts.abandoner,
                    opts.abandoner_username,
                    opts.reason,
                ),
            )
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **abandoned**.  That means that "
                "at least for the moment I need to close this pull request. "
                "Sorry it didn't work out :(" % opts.change_url,
            )
            gh_repo.set_pull_request_status(
                pull_request_match.number, closed=True
            )
        elif opts.hook == "change-restored":
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                util.format_gerrit_comment_for_github(
                    opts.change_url,
                    opts.restorer,
                    opts.restorer_username,
                    opts.reason,
                ),
            )
            gh_repo.publish_issue_comment(
                pull_request_match.number,
                "Gerrit review %s has been **restored**.  That means "
                "I can reopen this pull request!  Hooray :)"
                % (opts.change_url),
            )
            gh_repo.set_pull_request_status(
                pull_request_match.number, closed=False
            )

    @thing.gerrit_hook.event("comment-added")  # type: ignore
    def mirror_reviews(opts: gerrit.GerritHookEvent) -> None:
        """mirror comments posted to the gerrit review to the github PR."""
        hook_user = opts.author_username

        # skip if this is a bot comment, as that would produce
        # endless loops
        if hook_user in set(
            thing.opts.get("ignore_comment_usernames", [])
        ).union([thing.opts["gerrit_api_username"]]):
            thing.debug(
                "prtogerrit",
                "Gerrit user %s is in the ignore list, "
                "not mirroring comment",
                hook_user,
            )
            return

        # locate a pull request linked in the gerrit
        pull_request_match = util.get_pullreq_for_gerrit_change(thing, opts)

        # no pr, skip
        if pull_request_match is None:
            return

        change = opts.change

        hook_comment = opts.comment
        hook_user = opts.author_username

        # index the comments for the gerrit
        gerrit_comments = util.GerritComments(thing.gerrit_api, change)

        # the commandline hook gives us no identifier or timestamp so we just
        # search by text and username, getting most recent comment first.
        # Races are therefore possible here, in practice would require someone
        # submitting two reviews within a second of each other.
        lead_gerrit_comment = gerrit_comments.most_recent_comment_matching(
            hook_user, hook_comment
        )
        if lead_gerrit_comment is None:
            thing.debug(
                "prtogerrit",
                "Gerrit API returned no comment that matches "
                "user %s, message start '%s...'",
                hook_user,
                hook_comment[0:25],
            )
            return

        # index the comments on the PR
        gh_repo = thing.github_repo(opts.project)
        pullreq = util.GithubPullRequest(gh_repo, pull_request_match.number)

        outgoing_inline_comments = []
        outgoing_inline_replies = []
        outgoing_external_line_comments = []

        for gerrit_file_comment in lead_gerrit_comment["line_comments"]:
            path = gerrit_file_comment["path"]
            line_number = gerrit_file_comment["line"]
            is_parent = gerrit_file_comment.get("side", None) == "PARENT"
            github_position = pullreq.convert_gerrit_line_number(
                util.GerritReviewLine(path, line_number, is_parent)
            )

            if github_position is not None:
                if gerrit_file_comment.get("in_reply_to", None):
                    github_parent_comment = pullreq.get_lead_review_comment(
                        github_position
                    )  # MARKMARK

                    if github_parent_comment:
                        outgoing_inline_replies.append(
                            {
                                "in_reply_to": github_parent_comment["id"],
                                "body": util.format_gerrit_comment_for_github(
                                    opts.change_url,
                                    gerrit_file_comment["author"]["name"],
                                    gerrit_file_comment["author"]["username"],
                                    gerrit_file_comment.get(
                                        "message", "(no message)"
                                    ),
                                ),
                            }
                        )
                        continue

                outgoing_inline_comments.append(
                    {
                        "path": path,
                        "position": github_position.position,
                        "body": util.format_gerrit_comment_for_github(
                            opts.change_url,
                            gerrit_file_comment["author"]["name"],
                            gerrit_file_comment["author"]["username"],
                            gerrit_file_comment["message"],
                        ),
                    }
                )
            else:
                # gerrit lets you comment on any line in the whole
                # file, as well as on COMMIT_MSG, which aren't
                # available in github.  add these lines separately
                outgoing_external_line_comments.append(
                    "* %s (line %s): %s"
                    % (path, line_number, gerrit_file_comment["message"])
                )

        github_comment_body = util.format_gerrit_comment_for_github(
            opts.change_url,
            lead_gerrit_comment["author"]["name"],
            lead_gerrit_comment["author"]["username"],
            lead_gerrit_comment.get("message", "code review left on gerrit"),
        )

        if outgoing_inline_comments or outgoing_external_line_comments:
            if outgoing_external_line_comments:
                github_comment_body += "\n\n" + "\n".join(
                    outgoing_external_line_comments
                )

            github_review = {
                "commit_id": pullreq.get_head_sha(),
                "body": github_comment_body,
                "event": "COMMENT",
                "comments": outgoing_inline_comments,
            }

            # only publish as a review if we have inline comments.
            gh_repo.publish_review(pullreq.number, github_review)
        else:
            gh_repo.publish_issue_comment(
                pull_request_match.number, github_comment_body
            )
        if outgoing_inline_replies:
            for reply in outgoing_inline_replies:
                gh_repo.publish_pr_review_comment(pullreq.number, reply)
