"""

A Gerrit patchset-created hook that will publish comments to a Github
issue mentioned in the changeset.

patchset-created hook (docs aren't spectacular):

https://gerrit.googlesource.com/plugins/hooks/+/refs/heads/master/src/main/resources/Documentation/hooks.md#patchset_created

Usage
-----

Step 1: get a github personal access token from the developers UX.

Step 2: in the config for each project, add a ``[label "github-comment"]``
section.  This include any number of regular expression and messages to output
onto specific issues.  The regular expression must return the issue number
in match.group(1)::

    [access]
        inheritFrom = All-Projects
    [access "refs/*"]
        owner = group owners
    [label "github-comment"]
      repo = sqlalchemy/testgerrit

      fixes_re = "[Ff]ixes:? +#(\d+)"
      fixes_message = %(user)s submitted a Gerrit for review: %(gerritlink)s
          %(summary)

      references_re = "[Rr]eferences:? +#(\d+)"
      references_message = "%(user)s referenced this issue in Gerrit: %(gerritlink)s"

      my_custom_re = "Hey issue is (\d+)"
      my_custom_message = "Yo %(user)s just put up %(gerritlink)s"


Step 3:  Place a shell script in the gerrit environment::

    /var/gerrit/hooks/patchset-created

inside the script place::

    #!/bin/sh

    /path/to/virtualenv/bin/gerrit_patchset_comment /var/gerrit <access_token> "$@"

The arguments from Gerrit server are passed along.

When a new Gerrit patch is submitted, patchset-created hook is called.  That
then invokes this script, which loads the config for the project to see if
a github repo is defined.  Then it uses the gerrit API to compare the incoming
changeset against the previous version, if any, looking for tokens
``Fixes: <number>`` or ``References: <number>``.  It adds a comment to
the issue of that number in the repo according to the given template.

Note that if two successive commit messages reference the same issue numbers,
no message is generated.   It's only when an issue number is **added** to the
message that was not in the previous commit that a comment is triggered.


"""
import argparse
import collections
import configparser
import difflib
import json
import requests
import re
from urllib.parse import urlparse, urlunparse

from . import core


def grep_issue_numbers(regs, lines):
    outputs = collections.defaultdict(set)
    summary = lines[0]
    for line in lines:
        for key, value in regs.items():
            match = re.match(value['reg'], line)
            if match:
                outputs[key].add(match.group(1))
    return [
        (regs[key]["comment"], value, summary)
        for key in outputs for value in outputs[key]
    ]


def get_gerrit_patchset_commit(service_url, change, patchset):
    url = "%s/%s/revisions/%s/commit" % (
        service_url, change, patchset
    )
    return get_gerrit_api_call(url)


def get_gerrit_api_call(url):
    resp = requests.get(url)
    # some kind of CSRF thing they do.
    body = resp.text.lstrip(")]}'")

    return json.loads(body)


def get_gerrit_api_from_change_url(url):
    parts = urlparse(url)
    new_parts = list(parts[:])

    # TODO: not sure if we have to trim here or what.
    # might not be worth it to guess this URL
    new_parts[2] = "/changes"

    return urlunparse(new_parts)


def get_gerrit_config(path):
    config_string = core.git_show(
        path, "refs/meta/config", "project.config")
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_string)
    return config


def publish_github_comment(access_token, repo, issue_number, message):
    core.log("publish: %s %s %s" % (repo, issue_number, message))
    url = "https://api.github.com/repos/%s/issues/%s/comments" % (
        repo, issue_number
    )
    resp = requests.post(
        url,
        headers={"Authorization": "token %s" % access_token},
        data=json.dumps({"body": message})
    )
    if resp.status_code > 299:
        core.log("failed...code %s %s", resp.status_code, resp.text)
    else:
        core.log("Response: %s", resp.status_code)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("git_path", type=str)
    parser.add_argument("access_token", type=str)
    parser.add_argument("--project", type=str)
    parser.add_argument("--change", type=str)
    parser.add_argument("--commit", type=str)
    parser.add_argument("--kind", type=str)
    parser.add_argument("--change-url", type=str)
    parser.add_argument("--change-owner", type=str)
    parser.add_argument("--change-owner-username", type=str)
    parser.add_argument("--branch", type=str)
    parser.add_argument("--topic", type=str)
    parser.add_argument("--uploader", type=str)
    parser.add_argument("--uploader-username", type=str)
    parser.add_argument("--patchset", type=int)

    opts = parser.parse_args(argv)

    git_path = opts.git_path

    config = get_gerrit_config(git_path)
    try:
        section = config['label "github-comment"']
    except KeyError:
        return
    else:
        # TODO: need to raise informative error messages for missing keys
        regs = {}
        github_repo = section["repo"]

        for key in section:
            if key.endswith("_re"):
                message = section["%s_message" % key[0:-3]].strip('\'"')
                regs[key[0:-3]] = {
                    "reg": section[key].strip('\'"'),
                    "comment": message
                }

    service_url = get_gerrit_api_from_change_url(opts.change_url)

    this_revision = get_gerrit_patchset_commit(
        service_url, opts.change, opts.patchset)

    if opts.patchset > 1:
        # look for lines that were added in this patchset
        # compared to the previous one.
        previous_revision = get_gerrit_patchset_commit(
            service_url, opts.change, opts.patchset - 1)
        lines = list(
            difflib.unified_diff(
                previous_revision['message'].split("\n"),
                this_revision['message'].split("\n"))
        )
        issue_numbers = grep_issue_numbers(
            regs,
            [l[1:] for l in lines if l.startswith('+')]
        )
    else:
        # this is the first patchset, all lines are new
        lines = this_revision['message'].split("\n")
        issue_numbers = grep_issue_numbers(regs, lines)

    for message, issue_number, summary in issue_numbers:
        complete_message = message % {
            "user": opts.uploader_username,
            "gerritlink": opts.change_url,
            "summary": summary
        }
        publish_github_comment(
            opts.access_token, github_repo, issue_number, complete_message)
