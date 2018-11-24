"""
Work with Github / Bitbucket push events to update a local repo in response
to a push.

e.g.::

### myfile.wsgi ###

from publishthing import dvcs_hooks

mapping = {
    "service_username/reponame":{
        "local_repo": "/path/to/your/repo.git",
        "remote": "origin",
        "push_to": ["github", "other_remote"],
        "update_server_info": True
    }
}

application = dvcs_hooks.mirror_git(mapping)

#####

### then set up a URL to point to the .wsgi file:

http://mysite.com/mirror_dvcs

then set up the pull event in Github or Bitbucket to refer to this URL.

local_repo refers to a place where you've created a clone of the repo
using ``git clone --mirror``.  The "origin" should be where we get the
push from.  push_to is then a list of remotes to push to.  These remotes
have to also be in the local mirror checkout using "git remote add".

"""

from webob import Request, Response
import json
from .core import update_git_mirror, log, git_push


def mirror_git(mapping):
    def application(environ, start_response):
        req = Request(environ)
        res = Response()
        res.content_type = 'text/plain'

        # this seems to be what github does,
        # and bitbucket either does or used to.
        payload = req.params.get('payload', None)

        if payload is None:
            # not sure if this is what bitbucket used to do, or does
            # now
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
            # github and bitbucket both have:
            #
            # {
            #     "repository": {"full_name": "owner/reponame"}
            # }
            #
            # that's surprising.
            #
            repository_message = message['repository']
            repo = repository_message['full_name']

            repo = repo.strip("/")

            log("repo url: %s", repo)
            if repo in mapping:
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


