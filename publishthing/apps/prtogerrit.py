import configparser as ConfigParser
import re
from typing import Tuple
import urllib.parse

from .. import git as _git
from .. import github
from .. import publishthing
from .. import shell as _shell
from .. import wsgi

def prtogerrit(
        thing: publishthing.PublishThing,
        workdir: str,
        wait_for_reviewer: str) -> None:

    def is_opened(event: github.GithubEvent):
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

    def is_reviewer_request(event: github.GithubEvent):
        return event.json_data['action'] == "review_requested"

    @thing.github_webhook.event(
        "pull_request", is_reviewer_request)  # type: ignore
    def receive_push(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:

        owner, project = event.repo_name.split("/")

        pr = event.json_data['pull_request']

        with thing.shell_in(workdir).shell_in(owner, create=True) as shell:

            git, gerrit_host = _setup_gerrit_for_pr_base(shell, pr, project)

            _set_pr_into_commit(shell, git, event, pr)

            _publish_git_review(shell, git, event, project, gerrit_host)


def _setup_gerrit_for_pr_base(
        shell: _shell.Shell,
        pr: github.GithubJsonRec, project: str) -> Tuple[_git.GitRepo, str]:
    # fetch base repository
    git = shell.git_repo(
        project, origin=pr['base']['repo']['git_url'], create=True)

    # checkout the base branch as detached, usually master
    git.checkout(
        "origin/%s" % (
            pr['base']['ref']
        ),
        detached=True
    )

    # make sure gerrit remote is there
    config = ConfigParser.ConfigParser(interpolation=None)

    with shell.shell_in(project) as gr_shell:
        config.read_file(gr_shell.open(".gitreview"))
        gerrit_host = config['gerrit']['host']
        gerrit_project = config['gerrit']['project']

    git.remote_ensure("gerrit", "https://%s:%s@%s/%s" % (
        shell.thing.opts['secrets_gerrit_api_username'],
        urllib.parse.quote_plus(
            shell.thing.opts['secrets_gerrit_api_password']),
        gerrit_host,
        gerrit_project
    ))

    return git, gerrit_host


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

    body = pr['body']
    change_id = re.match(r'(.*)(Change-Id: .*)', body, re.S)

    footer = "\nCloses: #%s\nPull-request: %s" % (
        event.json_data['number'],
        pr['html_url']
    )

    if change_id:
        commit_msg = "%s\n\n%s%s%s" % (
            pr['title'],
            change_id.group(1),
            footer,
            change_id.group(2)
        )
    else:
        commit_msg = "%s\n\n%s%s" % (
            pr['title'],
            body,
            footer
        )

    git.commit(commit_msg, author=author)


def _publish_git_review(
        shell: _shell.Shell, git: _git.GitRepo,
        event: github.GithubEvent, project: str, gerrit_host: str) -> None:

    with shell.shell_in(project) as gr_shell:

        output = gr_shell.output_shell_cmd("git", "review")

        # pull the gerrit review link from the git review message
        gerrit_link = re.search(
            r'https://%s\S+' % gerrit_host, output, re.S)
        if gerrit_link:
            gerrit_link = gerrit_link.group(0)
        else:
            raise Exception("Could not locate PR link: %s" % output)

        shell.thing.github_repo(event.repo_name).publish_issue_comment(
            event.json_data['number'],
            "Change has been squashed to Gerrit review: %s" % (
                gerrit_link
            )
        )
