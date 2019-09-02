"""
Work with Github push events to update a local repo in response
to a push.

e.g.::

    ### myfile.wsgi ###

    from publishthing.apps import mirror_repos
    import publishthing

    thing = publishthing.PublishThing(
        github_webhook_secret="abcdefg"
    )

    mapping = {
        "sqlalchemy/testgerrit": {
            "local_repo": "/home/classic/tmp/testgerrit.git",
            "remote": "origin",
            "push_to": ['bitbucket', 'zzzeek_github'],
        },
    }

    mirror_repos.mirror_repos(thing, mapping)

    application = thing.github_webhook

then set up a URL to point to the .wsgi file:

http://mysite.com/mirror_repos

then set up the pull event in Github.

local_repo refers to a place where you've created a clone of the repo
using ``git clone --mirror``.  The "origin" should be where we get the
push from.  push_to is then a list of remotes to push to.  These remotes
have to also be in the local mirror checkout using "git remote add".

"""
import os
from typing import Any
from typing import Dict

from .. import github
from .. import publishthing
from .. import wsgi


def mirror_repos(
    thing: publishthing.PublishThing, mapping: Dict[str, Dict[str, Any]]
) -> None:
    @thing.github_webhook.event("push")  # type: ignore
    def receive_push(
        event: github.GithubEvent, request: wsgi.WsgiRequest
    ) -> None:

        repository_message = event.json_data["repository"]
        repo = repository_message["full_name"]

        repo = repo.strip("/")

        thing.debug("mirror_repos", "Repo: %s", repo)
        if repo in mapping:
            entry = mapping[repo]

            path = os.path.dirname(entry["local_repo"])
            local_name = os.path.basename(entry["local_repo"])

            with thing.shell_in(path) as shell:
                git = shell.git_repo(local_name, bare=True)

                git.update_remote(entry["remote"])
                request.add_text("repository: %s", repo)
                request.add_text("updated remote %s", entry["remote"])
                if "push_to" in entry:
                    for remote in entry["push_to"]:
                        git.push(remote, mirror=True)
                        request.add_text("pushed remote %s", remote)
