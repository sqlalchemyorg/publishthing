from ... import github
from ... import publishthing
from ... import shell as _shell
from . import util
from ... import wsgi


def github_hook(
        thing: publishthing.PublishThing,
        workdir: str,
        wait_for_reviewer: str,
        git_email: str) -> None:

    @thing.github_webhook.event(  # type: ignore
        "pull_request", util.github_pr_is_opened)
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

    @thing.github_webhook.event(  # type: ignore
        "pull_request", util.github_pr_is_reviewer_request(wait_for_reviewer))
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

