"""
Work with Bitbucket POST calls to update a local repo in response
to a bitbucket event.

See https://confluence.atlassian.com/display/BITBUCKET/POST+Service+Management

e.g.::

### myfile.wsgi ###

from publishthing import dvcs_hooks

mapping = {
    "/bitbucket_username/bitbucket_reponame/":{
        "local_repo": "/path/to/your/repo.git",
        "remote": "origin",
        "push_to": ["github", "some_server"],
        "update_server_info": True
    }
}

application = dvcs_hooks.bitbucket(mapping)

#####

### then set up a URL to point to the .wsgi file:

http://mysite.com/bitbucket_hook

then set up POST in bitbucket to refer to this URL.

When bitbucket posts to that URL, you'll get "cd <path_to_repo>; git remote update"
to keep it up to date.   This assumes the repo is a --mirror repo.

"""

from webob import Request, Response
import json
from .core import update_git_mirror, log, git_push


def bitbucket(mapping):
    def application(environ, start_response):
        req = Request(environ)
        res = Response()
        res.content_type = 'text/plain'

        payload = req.params.get('payload', None)
        if payload is None:
            payload = req.body

        if not payload:
            res.text = u"dvcs_hooks OK"
            return res(environ, start_response)

        try:
            log("message received....")
            message = json.loads(payload)
        except ValueError:
            log("couldn't parse payload")
            res.text = u"couldn't parse payload"
            message = repo = None
        else:
            repository_message = message['repository']
            if 'absolute_url' in repository_message:
                repo = repository_message['absolute_url']
            else:
                repo = repository_message['full_name']

            if not repo.startswith("/"):
                repo = "/" + repo
            if not repo.endswith("/"):
                repo = repo + "/"

            log("repo url: %s", repo)
            if repo in mapping and repository_message['scm'] == 'git':
                entry = mapping[repo]
                update_server_info = entry.get("update_server_info", False)
                update_git_mirror(
                    entry['local_repo'],
                    entry['remote'],
                    update_server_info=update_server_info)
                if 'push_to' in entry:
                    for push_to in entry['push_to']:
                        git_push(entry['local_repo'], push_to)
                res.text = u"pushed repository %s" % repo
            else:
                res.text = u"Can't locate repository %s" % repo
        return res(environ, start_response)
    return application


