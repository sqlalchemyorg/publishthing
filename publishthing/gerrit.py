import json
import os
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import argparse
import requests
import urllib.parse

import re

from . import git
from . import publishthing  # noqa
from configparser import ConfigParser

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


class GerritGit:
    def __init__(self, git: git.GitRepo,
                 git_identity: str, git_email: str,
                 git_remote_username: str, git_remote_password: str) -> None:
        self.git = git
        self.git._assert_not_bare()
        self.gerritconfig = ConfigParser(interpolation=None)
        with self.git.checkout_shell() as gr_shell:
            self.gerritconfig.read_file(gr_shell.open(".gitreview"))

        self._setup_repo_for_gerrit(
            git_identity, git_email, git_remote_username, git_remote_password
        )

    def _setup_repo_for_gerrit(
        self, git_identity: str, git_email: str,
            git_remote_username: str, git_remote_password: str) -> None:

        with self.git.checkout_shell() as gr_shell:
            username = gr_shell.output_shell_cmd(
                "git", "config", "user.name", none_for_error=True)
            useremail = gr_shell.output_shell_cmd(
                "git", "config", "user.email", none_for_error=True)
            if username != git_identity or useremail != git_email:
                self.git.set_identity(git_identity, git_email)

        # set up for gerrit.  we want to use https w/ username/password and
        # git review doesn't do that
        # set up the gerrit remote based on https, not ssh
        gerrit_host = self.gerritconfig['gerrit']['host']
        gerrit_project = self.gerritconfig['gerrit']['project']

        with self.git.checkout_shell() as gr_shell:
            url = "https://%s:%s@%s/%s" % (
                git_remote_username,
                urllib.parse.quote_plus(git_remote_password),
                gerrit_host,
                gerrit_project
            )
            remote = gr_shell.output_shell_cmd(
                "git", "config", "remote.gerrit.url", none_for_error=True)
            if remote is None:
                self.git.remote_add("gerrit", url)
            elif remote != url:
                self.git.remote_set_url("gerrit", url)

    def commit(
            self, commit_msg: str,
            author: Optional[str]=None, amend: bool=False) -> None:

        change_id_match = re.search("Change-Id: .*", commit_msg)
        self.git.commit(commit_msg, author=author, amend=amend)

        # manually generate a change_id because apache under selinux can't
        # run gerrit's commit-msg hook
        if not change_id_match:
            change_id = self._create_change_id(commit_msg)
            commit_msg += "\nChange-Id: I%s" % change_id
            self.git.commit(commit_msg, author=author, amend=True)

    def review(self) -> str:
        with self.git.checkout_shell() as gr_shell:
            branch = self.gerritconfig['gerrit']['defaultbranch']
            gerrit_host = self.gerritconfig['gerrit']['host']
            output = gr_shell.output_shell_cmd(
                "git", "push", "gerrit", "HEAD:refs/for/%s" % branch,
                include_stderr=True)

            # pull the gerrit review link from the git review message
            gerrit_link = re.search(
                r'https://%s\S+' % gerrit_host, output, re.S)
            if gerrit_link:
                return gerrit_link.group(0)
            else:
                raise Exception("Could not locate PR link: %s" % output)

    def _create_change_id(self, change_msg: str) -> str:
        with self.git.checkout_shell().shell_in(".git") as subshell:
            payload = []
            payload.append(
                "tree %s" % subshell.output_shell_cmd("git", "write-tree"))
            parent = subshell.output_shell_cmd(
                "git", "rev-parse", "HEAD^0").strip()
            if parent:
                payload.append("parent %s" % parent)
            payload.append(
                "author %s" %
                subshell.output_shell_cmd("git", "var", "GIT_AUTHOR_IDENT"))
            payload.append(
                "committer %s" %
                subshell.output_shell_cmd("git", "var", "GIT_COMMITTER_IDENT"))
            payload.append("\n%s" % change_msg)

            change_id = subshell.output_shell_cmd_stdin(
                "\n".join(payload),
                "git", "hash-object", "-t", "commit", "--stdin")
            return change_id


class GerritHook(Hooks):
    def __init__(self, thing: "publishthing.PublishThing") -> None:
        self.thing = thing
        self.approval_categories = thing.opts.get(
            'gerrit_approval_categories', ())
        super(GerritHook, self).__init__()

    def main(self, argv: Optional[List[str]] = None) -> None:
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
