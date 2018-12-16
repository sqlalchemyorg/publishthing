from .. import git as _git
from .. import github
from .. import publishthing
from .. import shell as _shell
from .. import wsgi

def prtogerrit(
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
                    state="failure",
                    context="gerrit_review"
                )
                raise

            # get the author from the squash so we can maintain it
            author = git.read_author_from_squash_pull()

            commit_msg = "%s\n\n%s\n\nCloses: #%s\nPull-request: %s" % (
                pr['title'],
                pr['body'],
                event.json_data['number'],
                pr['html_url']
            )

            # gerrit commit will make sure the change-id is written
            # without relying on a git commit hook
            git.gerrit.commit(commit_msg, author=author)

            gerrit_link = git.gerrit.review()

            gh_repo.publish_pr_comment_w_status_change(
                event.json_data['number'],
                event.json_data['pull_request']['head']['sha'],
                "Change has been squashed to Gerrit review",
                state="pending",
                context="gerrit_review",
                target_url=gerrit_link
            )

