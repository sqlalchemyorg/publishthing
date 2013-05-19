"""
Work with Bitbucket POST calls to update a local repo in response
to a bitbucket event.

See https://confluence.atlassian.com/display/BITBUCKET/POST+Service+Management

e.g.::

### myfile.wsgi ###

from publishthing import dvcs_hooks

mapping = {
    "/bitbucket_username/bitbucket_reponame/":(
        "/path/to/your/repo.git",
        "origin"
    )
}

application = dvcs_hooks.bitbucket(mapping)

#####

### then set up a URL to point to the .wsgi file:

http://mysite.com/bitbucket_hook

then set up POST in bitbucket to refer to this URL.

When bitbucket posts to that URL, you'll get "cd <path_to_repo>; git fetch origin"
to keep it up to date.

"""

from webob import Request, Response
import json
import os
from subprocess import check_call


def bitbucket(mapping):
    def application(environ, start_response):
        req = Request(environ)
        res = Response()
        res.content_type = 'text/plain'

        payload = req.params.get('payload', None)
        try:
            message = json.loads(payload)
        except ValueError:
            message = repo = None
        else:
            repo = message['repository']['absolute_url']
            if repo in mapping and message['repository']['scm'] == 'git':
                path, origin = mapping[repo]
                os.chdir(path)
                check_call(["git", "fetch", origin])

        res.body = "OK"
        return res(environ, start_response)
    return application
