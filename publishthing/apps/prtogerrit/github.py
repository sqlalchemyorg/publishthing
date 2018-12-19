import collections
from typing import Dict
from typing import List
from typing import Union

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

    # it looks like pull request review comments are always part
    # of a review that was submitted so we only need to catch
    # reviews, not review comments separately
    @thing.github_webhook.event("pull_request_review",
                                util.github_pr_review_is_submitted)
    def mirror_pr_comments(
            event: github.GithubEvent, request: wsgi.WsgiRequest) -> None:

        pr = event.json_data["pull_request"]

        review = event.json_data["review"]
        github_user = review["user"]["login"]

        # skip if this is a bot comment/review, as that would produce
        # endless loops
        if github_user in set(
            thing.opts.get('ignore_comment_usernames', [])
        ).union([thing.opts['github_api_username']]):
            thing.debug(
                "prtogerrit",
                "Gerrit user %s is in the ignore list, "
                "not mirroring comment / pullrequest", github_user)
            return

        # search in gerrit reviews for this pull request URL
        # in commit comments
        pull_request_badge = "Pull-request: %s" % pr['html_url']
        results = thing.gerrit_api.search(message=pull_request_badge)

        if results:
            # there should be only one, but in any case use the
            # most recent, which is first in the list
            existing_gerrit = results[0]
        else:
            thing.debug("prtogerrit",
                        "Can't find a gerrit review for pull request: %s",
                        pr['html_url'])
            return

        # we're going to post, so if this is a full review, load up
        # the comments for it
        gh_repo = thing.github_repo(event.repo_name)
        review_comments = gh_repo.get_review_comments(
            pr['number'], review['id']
        )
        comments_to_post = list(review_comments)
        review_commit_id = review["commit_id"]

        # now get all revisions from the gerrit so we can locate the
        # correct revision where the comment(s) from github is targeted.
        # the git review can be ahead of the gerrit, in which case
        # we just render the comment outside of the code, or the gerrit
        # could be ahead of the review if new patches were submitted
        # directly to gerrit, in which case the comments go to an older
        # rev in the gerrit.
        all_revisions = thing.gerrit_api.get_change_all_revisions(
            existing_gerrit['id'])

        for gerrit_revision_sha, gerrit_revision in \
                all_revisions['revisions'].items():
            pullreq_match = util.get_pullreq_for_gerrit_commit_message(
                event.repo_name,
                gerrit_revision['commit']['message']
            )
            if pullreq_match and pullreq_match.sha == review_commit_id:
                break
        else:
            thing.debug(
                "prtogerrit",
                "Can't find commit %s in Gerrit pull request messages "
                "while trying to mirror a github comment, pull request %s",
                review_commit_id, pr['html_url']
            )
            # use the latest revision
            gerrit_revision_sha = all_revisions['current_revision']

        pullreq_index = util.GithubPullRequest(
            gh_repo, pr['number'], existing_pullreq=pr
        )

        inline_comments : Dict[str, List[Dict[str, Union[str, int]]]] = collections.defaultdict(list)
        non_inline_comments = []

        for comment in comments_to_post:
            gerrit_file_position = pullreq_index.\
                convert_github_line_position(
                    util.GithubReviewPosition(
                        comment['path'], comment['position'])
                )

            if gerrit_file_position is None:
                non_inline_comments.append(
                    "* %s (%s): %s" % (
                        comment['path'], comment['position'],
                        comment['body']
                    )
                )
            else:
                inline_comments[comment['path']].append(
                    {
                        "line": gerrit_file_position.line_number,
                        "message": util.format_github_comment_for_gerrit(
                            github_user, comment['body']),
                        "side": "REVISION"
                        if not gerrit_file_position.is_parent else "PARENT"
                    }
                )

        if event.event == "pull_request_review" and review['body']:
            message = review['body'] + "\n\n"
        else:
            message = ""
        if non_inline_comments:
            message += "\n\n" + "\n".join(non_inline_comments)

        message = util.format_github_comment_for_gerrit(
            github_user, message)

        review = {
            "message": message,
            "comments": inline_comments
        }

        thing.gerrit_api.set_review(
            existing_gerrit["id"], gerrit_revision_sha, review)

