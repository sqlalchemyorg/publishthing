import json
import os
import sys
from typing import Any
from typing import Dict
from typing import List

import argparse
import requests

from . import publishthing  # noqa

from .util import Hooks

GerritJsonRec = Dict[str, Any]


class GerritApi:
    def __init__(self, thing: "publishthing.PublishThing") -> None:
        self.service_url = thing.opts['gerrit_api_url']
        self.api_username = thing.opts['gerrit_api_username']
        self.api_password = thing.opts['gerrit_api_password']

    def get_patchset_commit(self, change: str, patchset: int) -> GerritJsonRec:
        return self._gerrit_api_call(
            "changes/%s/revisions/%s/commit" % (change, patchset))

    def _gerrit_api_call(self, path: str) -> GerritJsonRec:
        url = "%s/a/%s" % (self.service_url, path)
        resp = requests.get(url, auth=(self.api_username, self.api_password))

        if resp.status_code > 299:
            raise Exception(
                "Got response %s for %s: %s" %
                (resp.status_code, url, resp.content))

        # some kind of CSRF thing they do.
        body = resp.text.lstrip(")]}'")

        return json.loads(body)


class GerritHook(Hooks):
    def __init__(self, thing: "publishthing.PublishThing") -> None:
        self.thing = thing
        self.approval_categories = thing.opts.get(
            'gerrit_approval_categories', ())
        super(GerritHook, self).__init__()

    def main(self, argv: List[str]=None) -> None:
        # hooks are at: https://gerrit.googlesource.com/plugins/hooks/+/refs/
        # heads/master/src/main/resources/Documentation/hooks.md#patchset_created
        parser = argparse.ArgumentParser()
        parser.add_argument("--abandoner", type=str)
        parser.add_argument("--abandoner-username", type=str)
        parser.add_argument("--author", type=str)
        parser.add_argument("--author-username", type=str)
        parser.add_argument("--branch", type=str)
        parser.add_argument("--change-owner", type=str)
        parser.add_argument("--change-owner-username", type=str)
        parser.add_argument("--changer", type=str)
        parser.add_argument("--changer-username", type=str)
        parser.add_argument("--change", type=str)
        parser.add_argument("--change-url", type=str)
        parser.add_argument("--comment", type=str)
        parser.add_argument("--commit", type=str)
        parser.add_argument("--kind", type=str)
        parser.add_argument("--newrev", type=str)
        parser.add_argument("--new-topic", type=str)
        parser.add_argument("--oldrev", type=str)
        parser.add_argument("--old-topic", type=str)
        parser.add_argument("--patchset", type=int)
        parser.add_argument("--project", type=str)
        parser.add_argument("--reason", type=str)
        parser.add_argument("--refname", type=str)
        parser.add_argument("--reviewer", type=str)
        parser.add_argument("--reviewer-username", type=str)
        parser.add_argument("--submitter", type=str)
        parser.add_argument("--submitter-username", type=str)
        parser.add_argument("--topic", type=str)
        parser.add_argument("--uploader", type=str)
        parser.add_argument("--uploader-username", type=str)

        for cat in {'Code-Review', 'Verified'}.union(self.approval_categories):
            parser.add_argument("--%s" % cat, type=int)
            parser.add_argument("--%s-oldValue" % cat, type=int)

        opts = parser.parse_args(argv)
        hook = os.path.basename(sys.argv[0])
        self.thing.debug("gerrithook", "event received: %s  (%s)", hook, opts)
        self._run_hooks(hook, opts)
