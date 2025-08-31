import argparse
from datetime import datetime
import multiprocessing
import multiprocessing.pool
import os
from typing import Callable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple

from .. import github
from .. import publishthing

WORKERS = 10

JobList = List["multiprocessing.pool.AsyncResult[None]"]


def run_jobs(
    iterator: Iterator[github.GithubJsonRec],
    jobs: JobList,
    completed_callback: Callable[[int, bool], None],
) -> Iterator[github.GithubJsonRec]:
    idx = 0
    for idx, item in enumerate(iterator):
        yield item

        if idx % 50 == 0:
            while jobs:
                print("Waiting for jobs...%s jobs left" % len(jobs))
                job = jobs.pop(0)
                job.wait()
            completed_callback(idx, False)
    while jobs:
        print("Waiting for jobs...%s jobs left" % len(jobs))
        job = jobs.pop(0)
        job.wait()
    completed_callback(idx, True)


def run_sync(gh: github.GithubRepo, destination: str) -> None:
    with gh.thing.shell_in(destination, create=True) as workdir:

        last_received_filename = "last_received.txt"

        if not workdir.file_exists(last_received_filename):
            last_received = None
        else:
            with workdir.open(last_received_filename, "r") as file_:
                content = file_.read().strip()
                url, last_received = content.split("\n")
                if url != gh.url:
                    gh.thing.cmd_error(
                        "Persisted url %s does not match the "
                        "URL we're getting right now: %s, exiting"
                        % (url, gh.url)
                    )

                try:
                    datetime.fromisoformat(last_received)
                except ValueError:
                    last_received = None

        highest_timestamp = None

        pool = multiprocessing.Pool(WORKERS)

        jobs: JobList = []

        def completed_callback(name: str) -> Callable[[int, bool], None]:
            def do_completed(idx: int, is_done: bool) -> None:
                gh.thing.message(
                    "Completed %s %s, most recent updated at: %s",
                    idx,
                    name,
                    highest_timestamp,
                )
                assert highest_timestamp
                workdir.write_file(
                    last_received_filename,
                    "%s\n%s" % (gh.url, highest_timestamp),
                )

            return do_completed

        for issue in run_jobs(
            gh.get_issues_since(last_received),
            jobs,
            completed_callback("issues"),
        ):
            if (
                highest_timestamp is None
                or issue["updated_at"] > highest_timestamp
            ):
                highest_timestamp = issue["updated_at"]

            issue_dest = os.path.join(
                "./issues", str(issue["number"] // 100), str(issue["number"])
            )

            attachments: List[Tuple[str, str]] = []

            attachments.extend(gh.find_attachments(issue))

            events = list(gh.get_issue_events(issue["number"]))
            with workdir.shell_in(issue_dest, create=True) as sub:
                jobs.append(
                    pool.apply_async(
                        _fetch_attachments, (gh, sub.path, attachments)
                    )
                )

                sub.write_json_file("events.json", events)

                sub.write_json_file("issue.json", issue)

        for comment in run_jobs(
            gh.get_comments_since(last_received),
            jobs,
            completed_callback("comments"),
        ):
            if (
                highest_timestamp is None
                or comment["updated_at"] > highest_timestamp
            ):
                highest_timestamp = comment["updated_at"]

            issue_dest = os.path.join(
                "./issues", str(issue["number"] // 100), str(issue["number"])
            )

            with workdir.shell_in(issue_dest, create=True) as sub:
                attachments = []

                attachments.extend(gh.find_attachments(comment))

                jobs.append(
                    pool.apply_async(
                        _fetch_attachments, (gh, sub.path, attachments)
                    )
                )

                sub.write_json_file(
                    "comment_%s_%s.json"
                    % (comment["created_at"], comment["id"]),
                    comment,
                )


def _fetch_attachments(
    gh: github.GithubRepo, path: str, attachments: List[str]
) -> None:
    for filename, url in attachments:
        with gh.thing.shell_in(path).shell_in(
            "attachments", create=True
        ) as sub:
            content = gh.get_attachment(url)
            sub.write_file(filename, content, binary=True)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "repo", type=str, help="user/reponame string on github"
    )
    parser.add_argument("dest", type=str, help="directory in which to sync")
    parser.add_argument("--access-token", type=str, help="oauth access token")

    opts = parser.parse_args(argv)
    thing = publishthing.PublishThing(
        github_access_token=opts.access_token, github_api_concurrency=WORKERS
    )
    gh = thing.github_repo(opts.repo)

    run_sync(gh, opts.dest)
