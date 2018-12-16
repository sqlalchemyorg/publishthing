import re

from typing import Any
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
        return event.json_data['action'] == "review_requested"

    @thing.github_webhook.event(  # type: ignore
        "pull_request", is_reviewer_request)
    def review_requested(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:

        gh_repo = thing.github_repo(event.repo_name)

        gh_repo.create_pr_review(
            event.json_data['number'],
            "OK, this is **%s** setting up my work to try to get this PR into "
            "gerrit so we can run tests and reviews and "
            "stuff" % wait_for_reviewer,
            event="COMMENT"
        )
        owner, project = event.repo_name.split("/")

        pr = event.json_data['pull_request']

        with thing.shell_in(workdir).shell_in(owner, create=True) as shell:

            git = shell.git_repo(
                project, origin=pr['base']['repo']['git_url'], create=True)

            target_branch = pr['base']['ref']
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
                    event.json_data['pull_request']['head']['sha'],
                    "Failed to create a gerrit review, git squash "
                    "against branch '%s' failed" % target_branch,
                    state="error",
                    context="gerrit_review"
                )
                raise

            # get the author from the squash so we can maintain it
            author = git.read_author_from_squash_pull()

            commit_msg = (
                "%s\n\n%s\n\nCloses: #%s\nPull-request: %s\n"
                "Pull-request-sha: %s\n" % (
                    pr['title'],
                    pr['body'],
                    event.json_data['number'],
                    pr['html_url'],
                    event.json_data['pull_request']['head']['sha'],
                )
            )

            # gerrit commit will make sure the change-id is written
            # without relying on a git commit hook
            git.gerrit.commit(commit_msg, author=author)

            gerrit_link = git.gerrit.review()

            gh_repo.publish_pr_comment_w_status_change(
                event.json_data['number'],
                event.json_data['pull_request']['head']['sha'],
                "Change has been squashed to Gerrit review",
                state="success",
                context="gerrit_review",
                target_url=gerrit_link
            )


def gerrit_hook(thing: publishthing.PublishThing) -> None:

    def includes_verify(opts: Any) -> bool:
        return opts.Verified is not None and opts.Verified_oldValue is not None

    @thing.gerrit_hook.event("patchset-created")   # type: ignore
    @thing.gerrit_hook.event("comment-added", includes_verify)   # type: ignore
    def verified_status_changed(opts: Any) -> None:
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
            return

        thing.debug(
            "prtogerrit",
            "Located pull request %s sha %s in gerrit review %s",
            pr_num_match.group(1),
            pr_num_match.group(2),
            opts.change
        )
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

