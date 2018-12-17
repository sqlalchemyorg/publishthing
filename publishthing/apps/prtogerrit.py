import re

from typing import Any
from typing import Optional
from typing import Match
from .. import github
from .. import publishthing
from .. import shell as _shell
from .. import wsgi


def github_hook(
        thing: publishthing.PublishThing,
        workdir: str,
        wait_for_reviewer: str,
        git_email: str) -> None:

    def is_opened(event: github.GithubEvent) -> bool:
        return event.json_data['action'] in (
            "opened", "edited", "synchronize", "reopened")

    @thing.github_webhook.event("pull_request", is_opened)  # type: ignore
    def make_pr_pending(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:
        """as soon as a PR is created, we mark the status as "pending" to
        indicate a particular reviewer needs to be added"""

        gh_repo = thing.github_repo(event.repo_name)
        gh_repo.create_status(
            event.json_data['pull_request']['head']['sha'],
            state="pending",
            description="Waiting for pull request to receive a reviewer",
            context="gerrit_review"
        )

    def is_reviewer_request(event: github.GithubEvent) -> bool:
        return \
            event.json_data['action'] == "review_requested" and \
            wait_for_reviewer in {
                rec["login"] for rec in
                event.json_data['pull_request']['requested_reviewers']
            }

    @thing.github_webhook.event(  # type: ignore
        "pull_request", is_reviewer_request)
    def review_requested(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:

        gh_repo = thing.github_repo(event.repo_name)
        owner, project = event.repo_name.split("/")

        pr = event.json_data['pull_request']

        gh_repo.create_pr_review(
            event.json_data['number'],
            "OK, this is **%s** setting up my work to try to get revision %s "
            "of this pull request into gerrit so we can run tests and "
            "reviews and stuff" % (wait_for_reviewer, pr['head']['sha']),
            event="COMMENT"
        )

        with thing.shell_in(workdir).shell_in(owner, create=True) as shell:

            git = shell.git_repo(
                project, origin=pr['base']['repo']['git_url'], create=True)

            target_branch = pr['base']['ref']

            git.fetch(all=True)

            # checkout the base branch as detached, usually master
            git.checkout("origin/%s" % (target_branch, ), detached=True)

            # sets everything up for gerrit
            git.enable_gerrit(
                wait_for_reviewer, git_email,
                shell.thing.opts['gerrit_api_username'],
                shell.thing.opts['gerrit_api_password']
            )

            # name the new branch against the PR
            git.create_branch(
                "pr_github_%s" % event.json_data['number'], force=True)

            # pull remote PR into the local repo
            try:
                git.pull(
                    pr['head']['repo']['clone_url'],
                    pr['head']['ref'], squash=True
                )
            except _shell.CalledProcessError:
                git.reset(hard=True)
                gh_repo.publish_pr_comment_w_status_change(
                    event.json_data['number'],
                    pr['head']['sha'],
                    "Failed to create a gerrit review, git squash "
                    "against branch '%s' failed" % target_branch,
                    state="error",
                    context="gerrit_review"
                )
                raise

            # get the author from the squash so we can maintain it
            author = git.read_author_from_squash_pull()

            pull_request_badge = "Pull-request: %s" % pr['html_url']

            commit_msg = (
                "%s\n\n%s\n\nCloses: #%s\n%s\n"
                "Pull-request-sha: %s\n" % (
                    pr['title'],
                    pr['body'],
                    event.json_data['number'],
                    pull_request_badge,
                    pr['head']['sha'],
                )
            )

            results = thing.gerrit_api.search(
                status="open", message=pull_request_badge)
            if results:
                # there should be only one, but in any case use the
                # most recent, which is first in the list
                existing_gerrit = results[0]
            else:
                existing_gerrit = None

            if existing_gerrit:
                is_new_gerrit = False
                git.gerrit.commit(
                    commit_msg, author=author,
                    change_id=existing_gerrit["change_id"])
            else:
                # gerrit commit will make sure the change-id is written
                # without relying on a git commit hook
                is_new_gerrit = True
                git.gerrit.commit(commit_msg, author=author)

            gerrit_link = git.gerrit.review()

            gh_repo.publish_pr_comment_w_status_change(
                event.json_data['number'],
                event.json_data['pull_request']['head']['sha'],
                (
                    "New Gerrit review created" if is_new_gerrit else
                    "Patchset added to existing Gerrit review"
                ),
                state="success",
                context="gerrit_review",
                target_url=gerrit_link,
                long_message=(
                    (
                        "New Gerrit review created for change %s: %s" % (
                            event.json_data['pull_request']['head']['sha'],
                            gerrit_link
                        )
                    ) if is_new_gerrit else (
                        "Patchset %s added to existing Gerrit review %s" % (
                            event.json_data['pull_request']['head']['sha'],
                            gerrit_link
                        )
                    )
                )
            )


def gerrit_hook(thing: publishthing.PublishThing) -> None:

    def pr_for_gerrit_change(opts: Any) -> Optional[Match[str]]:
        change_commit = thing.gerrit_api.get_change_current_commit(opts.change)

        current_revision = change_commit["current_revision"]
        message = change_commit["revisions"][
            current_revision]["commit"]["message"]

        search_url = (
            r"Pull-request: https://github.com/%s/pull/(\d+)\n"
            "Pull-request-sha: (.+?)\n" % (
                opts.project,
            )
        )

        pr_num_match = re.search(search_url, message, re.M)
        if pr_num_match is None:
            thing.debug(
                "prtogerrit",
                "Did not locate a pull request in comment for gerrit "
                "review %s",
                opts.change)
            return None

        thing.debug(
            "prtogerrit",
            "Located pull request %s sha %s in gerrit review %s",
            pr_num_match.group(1),
            pr_num_match.group(2),
            opts.change
        )
        return pr_num_match

    def includes_verify(opts: Any) -> bool:
        return (
            opts.Verified is not None and
            opts.Verified_oldValue is not None) or (
            opts.Code_Review is not None and
            opts.Code_Review_oldValue is not None)

    # unfortunately there's no gerrit event for "vote removed"
    # in the hooks plugin, even though "stream events" has it.
    # this will cause the github status to be wrong until another comment
    # corrects it.
    @thing.gerrit_hook.event("patchset-created")   # type: ignore
    @thing.gerrit_hook.event("comment-added", includes_verify)   # type: ignore
    def verified_status_changed(opts: Any) -> None:
        pr_num_match = pr_for_gerrit_change(opts)

        if pr_num_match is None:
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
                    pr_num_match.group(2),
                    description=message,
                    state=state,
                    context=context,
                    target_url=opts.change_url
                )

    @thing.gerrit_hook.event("change-merged")   # type: ignore
    @thing.gerrit_hook.event("change-abandoned")   # type: ignore
    @thing.gerrit_hook.event("change-deleted")   # type: ignore
    def change_merged_or_abandoned(opts: Any) -> None:
        pr_num_match = pr_for_gerrit_change(opts)

        if pr_num_match is None:
            return

        gh_repo = thing.github_repo(opts.project)

        if opts.hook == "change-merged":
            gh_repo.publish_issue_comment(
                pr_num_match.group(1),
                "Gerrit review %s has been **merged**. "
                "Congratulations! :)" % opts.change_url
            )
        elif opts.hook == "change-deleted":
            gh_repo.publish_issue_comment(
                pr_num_match.group(1),
                "Gerrit review %s has been **deleted**. Hmm, maybe "
                "the admins are doing something here." % opts.change_url
            )
        elif opts.hook == "change-abandoned":
            gh_repo.publish_issue_comment(
                pr_num_match.group(1),
                "Gerrit review %s has been **abandoned**.  That means that "
                "at least for the moment I need to close this pull request. "
                "Sorry it didn't work out :(" % opts.change_url
            )
            gh_repo.close_pull_request(pr_num_match.group(1))


