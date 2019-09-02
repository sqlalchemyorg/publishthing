import argparse
import re
from typing import Callable
from typing import List
from typing import Optional

from changelog import mdwriter
from .. import github
from .. import publishthing

RE_release = re.compile(
    r"^((\d)\.(\d))(?:\.(\d+))?((?:a|b|c|rc)\d)?$", re.I | re.X
)


def run_sync(
    gh: github.GithubRepo,
    tag_template: str,
    receive_changelog_entries: Callable,
) -> None:

    # read rst file
    releases = {}

    def receive_sections(num, text):
        releases[num] = text

    receive_changelog_entries(receive_sections)

    # get all tags on github
    # GET /repos/:owner/:repo/git/refs/tags

    raw_tags = gh.get_git_tags()
    tags_by_release = {}
    for raw_tag in raw_tags:
        match = re.match(r"refs/tags/(.+)$", raw_tag["ref"])
        if match is None:
            raise ValueError(
                "Can't match tag entry on ref: %s" % raw_tag["ref"]
            )
        else:
            tag = match.group(1)
        tags_by_release[tag] = raw_tag

    # get existing releases from gh API
    for release, release_text in releases.items():
        print("\nRelease record %s" % release)
        release_match = RE_release.match(release)
        if release_match is None:
            raise ValueError(
                "Can't parse changelog version string: %s" % release
            )

        formatted_release = "v%s" % release

        # for the moment we look only at a, b, c, rc which are
        # all prerelease symbols
        is_prerelease = bool(release_match.group(5))

        if tag_template == "rel_":
            tag = "rel_" + "_".join(
                g for g in release_match.group(2, 3, 4) if g
            )
            if release_match.group(5):
                tag += release_match.group(5)
        elif tag_template == "v.":
            tag = "v" + ".".join(g for g in release_match.group(2, 3, 4) if g)
            if release_match.group(5):
                tag += release_match.group(5)
        else:
            raise ValueError("unknown tag format %s" % tag_template)

        try:
            git_tag_rec = tags_by_release[tag]
        except KeyError:
            print("Tag %s not in git repo, skipping" % tag)
            continue
        else:
            print(
                "Tag %s found in git repo, sha %s"
                % (tag, git_tag_rec["object"]["sha"])
            )

        entry_exists = gh.get_release_by_tag(tag)

        if entry_exists:
            print(
                "Existing release entry %s for release %s found"
                % (entry_exists["id"], formatted_release)
            )
        else:
            print(
                "No existing release entry for release %s" % formatted_release
            )

        release_rec = {
            "tag_name": tag,
            "target_commitish": "master",
            "name": formatted_release,
            "body": release_text,
            "draft": False,
            "prerelease": is_prerelease,
        }

        if entry_exists:
            gh.edit_release(entry_exists["id"], release_rec)
            print(
                "updated release id %s for release %s, total chars %d"
                % (entry_exists["id"], formatted_release, len(release_text))
            )
        else:
            gh.create_release(release_rec)
            print(
                "created release %s, total chars %d"
                % (formatted_release, len(release_text))
            )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "repo", type=str, help="user/reponame string on github"
    )
    parser.add_argument("filename", help="target changelog filename")
    parser.add_argument(
        "tag_template",
        help="how to link a changelog version to a tag, "
        "either 'v.' or 'rel_' style",
    )
    parser.add_argument("-c", "--config", help="path to conf.py")
    parser.add_argument(
        "-v",
        "--version",
        type=str,
        help="render changelog only for version given",
    )
    parser.add_argument("--access-token", type=str, help="oauth access token")

    opts = parser.parse_args(argv)
    thing = publishthing.PublishThing(github_access_token=opts.access_token)
    gh = thing.github_repo(opts.repo)

    def receive_changelog_entries(receive_sections):
        mdwriter.stream_changelog_sections(
            opts.filename, opts.config, receive_sections, opts.version
        )

    run_sync(gh, opts.tag_template, receive_changelog_entries)
