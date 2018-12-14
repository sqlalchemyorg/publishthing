import configparser as ConfigParser
import re
from typing import Any
from typing import Dict
import urllib.parse

from .. import github
from .. import publishthing
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

            # ###### set up our repo for git reviews

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
                thing.opts['secrets_gerrit_api_username'],
                urllib.parse.quote_plus(
                    thing.opts['secrets_gerrit_api_password']),
                gerrit_host,
                gerrit_project
            ))

            # ###### get the PR and merge it, commit it

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
                thing.github_repo(event.repo_name).publish_issue_comment(
                    event.json_data['number'],
                    "Failed to create a gerrit review for this PR, "
                    "squash failed."
                )
                return

            # totally special thing
            author = git.read_author_from_squash_push()

            commit_msg = "%s\n\n%s\n\nCloses: #%s\nPull-request: %s" % (
                pr['title'],
                pr['body'],
                event.json_data['number'],
                pr['html_url']
            )

            git.commit(commit_msg, author=author)

            # ######## make the git review and note status
            with shell.shell_in(project) as gr_shell:

                output = gr_shell.output_shell_cmd("git", "review", "-R")

                # pull the gerrit review link from the git review message
                gerrit_link = re.search(
                    r'https://%s\S+' % gerrit_host, output, re.S)
                if gerrit_link:
                    gerrit_link = gerrit_link.group(0)
                else:
                    raise Exception("Could not locate PR link: %s" % output)

                thing.github_repo(event.repo_name).publish_issue_comment(
                    event.json_data['number'],
                    "Change has been squashed to Gerrit review: %s" % (
                        gerrit_link
                    )
                )

