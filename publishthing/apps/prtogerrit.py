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
        print("got event w/ %s action" % event.json_data['action'])

        gh_repo = thing.github_repo(event.repo_name)
        print(
            "marking sha %s with pending" % (
                event.json_data['pull_request']['head']['sha'], )
        )
        gh_repo.create_status(
            event.json_data['pull_request']['head']['sha'],
            {
                "state": "pending",
                "description": "This PR must be marked for review",
                "context": "wait_for_reviewer"
            }
        )

    def is_reviewer_request(event: github.GithubEvent) -> bool:
        return event.json_data['action'] == "review_requested"

    @thing.github_webhook.event(  # type: ignore
        "pull_request", is_reviewer_request)
    def receive_push(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:

        owner, project = event.repo_name.split("/")

        pr = event.json_data['pull_request']

        with thing.shell_in(workdir).shell_in(owner, create=True) as shell:

            git = shell.git_repo(
                project, origin=pr['base']['repo']['git_url'], create=True)

            # checkout the base branch as detached, usually master
            git.checkout("origin/%s" % (pr['base']['ref'], ), detached=True)

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
                thing.github_repo(event.repo_name).publish_issue_comment(
                    event.json_data['number'],
                    "Failed to create a gerrit review for this PR, "
                    "squash failed."
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

            thing.github_repo(event.repo_name).publish_issue_comment(
                event.json_data['number'],
                "Change has been squashed to Gerrit review: %s" % (
                    gerrit_link
                )
            )

