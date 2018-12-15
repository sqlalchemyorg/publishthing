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

            git = _setup_gerrit_for_pr_base(
                shell, pr, project, wait_for_reviewer, git_email)

            _set_pr_into_commit(shell, git, event, pr)

            _publish_git_review(shell, git, event, project)


def _setup_gerrit_for_pr_base(
        shell: _shell.Shell,
        pr: github.GithubJsonRec,
        project: str,
        git_identity: str,
        git_email: str) -> _git.GitRepo:

    git = shell.git_repo(
        project, origin=pr['base']['repo']['git_url'], create=True)

    # checkout the base branch as detached, usually master
    git.checkout(
        "origin/%s" % (
            pr['base']['ref']
        ),
        detached=True
    )

    # sets everything up for gerrit
    git.enable_gerrit(
        git_identity, git_email,
        shell.thing.opts['gerrit_api_username'],
        shell.thing.opts['gerrit_api_password']
    )

    return git


def _set_pr_into_commit(
        shell: _shell.Shell,
        git: _git.GitRepo,
        event: github.GithubEvent,
        pr: github.GithubJsonRec) -> None:

    # name the new branch against the PR
    git.create_branch("pr_github_%s" % event.json_data['number'],
                      force=True)

    # pull remote PR into the local repo
    try:
        git.pull(
            pr['head']['repo']['clone_url'],
            pr['head']['ref'],
            squash=True
        )
    except shell.CalledProcessError:
        git.reset(hard=True)
        shell.thing.github_repo(event.repo_name).publish_issue_comment(
            event.json_data['number'],
            "Failed to create a gerrit review for this PR, "
            "squash failed."
        )
        raise

    # totally special thing
    author = git.read_author_from_squash_push()

    footer = "\nCloses: #%s\nPull-request: %s" % (
        event.json_data['number'],
        pr['html_url']
    )

    commit_msg = "%s\n\n%s%s" % (
        pr['title'],
        pr['body'],
        footer
    )

    # gerrit commit will make sure the changelog is written
    git.gerrit.commit(commit_msg, author=author)


def _publish_git_review(
        shell: _shell.Shell, git: _git.GitRepo,
        event: github.GithubEvent, project: str) -> None:

    gerrit_link = git.gerrit.review()

    shell.thing.github_repo(event.repo_name).publish_issue_comment(
        event.json_data['number'],
        "Change has been squashed to Gerrit review: %s" % (
            gerrit_link
        )
    )
