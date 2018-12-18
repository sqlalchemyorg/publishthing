
from typing import Any
import re

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

    def _compare_message(message, hook_message):
        return re.sub(r'\\.|\n|\t', '', message) == \
            re.sub(r'\\.|\n|\t', '', hook_message)

    @thing.gerrit_hook.event("comment-added")  # type: ignore
    def mirror_reviews(opts: gerrit.GerritHookEvent) -> None:
        hook_user = opts.author_username

        # skip if this is a bot comment
        if hook_user in set(
            thing.opts.get('ignore_comment_usernames', [])
        ).union([thing.opts['gerrit_api_username']]):
            return

        # locate a pull request linked in the gerrit
        pull_request_match = util.get_pullreq_for_gerrit_change(thing, opts)

        # no pr, skip
        if pull_request_match is None:
            return

        change = opts.change

        # get this set of comments from the API.   the commandline hook gives
        # us no identifier or timestamp so we just search by text
        hook_comment = opts.comment
        hook_user = opts.author_username

        comments = thing.gerrit_api.get_change_standalone_comments(change)

        for lead_comment in comments['messages']:
            if lead_comment["author"]["username"] == hook_user and \
                    _compare_message(lead_comment["message"], hook_comment):
                lead_comment_date = lead_comment["date"]
                break
        else:
            thing.debug(
                "prtogerrit",
                "Gerrit API returned no comment that matches user %s, "
                "message start '%s...'", hook_user, hook_comment[0:25]
            )
            return

        # convert into a github pull request review
        def _format_message(author_name, author_user, message):
            return "**%s** (%s) wrote:\n\n%s" % (
                author_name, author_user, message
            )

        git_repo = thing.github_repo(opts.project)
        pullreq = git_repo.get_pull_request(pull_request_match.number)

        github_review = {
            "commit_id": pullreq['head']['sha'],
            "body": _format_message(
                lead_comment["author"]["name"],
                lead_comment["author"]["username"],
                lead_comment["message"],
            ),
            "event": "COMMENT",
            "comments": [],
            "extra_comments": []
        }

        # inline code comments, convert line numbers
        inline_comments = thing.gerrit_api.get_change_inline_comments(change)

        line_index = util.create_github_position_map(
            git_repo.get_pull_request_diff(pull_request_match.number)
        )

        for path, comments in inline_comments.items():
            for comment in comments:
                timestamp = comment["updated"]
                if timestamp == lead_comment_date:
                    line_number = comment["line"]
                    is_parent = comment.get('side', None) == 'PARENT'

                    github_line = line_index.get(
                        (path, line_number, is_parent)
                    )
                    if github_line:
                        github_review["comments"].append({
                            "path": path,
                            "position": github_line,
                            "body": _format_message(
                                comment["author"]["name"],
                                comment["author"]["username"],
                                comment["message"],
                            ),
                        })
                    else:
                        # gerrit lets you comment on any line in the whole
                        # file, as well as on COMMIT_MSG, which aren't
                        # available in github.  add these lines separately
                        github_review["extra_comments"].append(
                            "* %s (line %s): %s" % (
                                path, line_number, comment["message"]
                            )
                        )

        extra_comments = github_review.pop("extra_comments")
        if extra_comments:
            github_review["body"] += "\n\n" + "\n".join(extra_comments)

        git_repo.publish_review(pull_request_match.number, github_review)

