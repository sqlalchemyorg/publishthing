import argparse
import multiprocessing
import os
import json
import requests
import time
import re
import sys

WORKERS = 10


class GitHub:
    _last_push_time = 0
    _rate_limit = None

    def __init__(self, repo, access_token, concurrency=1):
        self.repo = repo
        self.concurrency = concurrency
        self.url = "https://github.com/%s" % repo
        self.access_token = access_token
        self.session = requests.Session()
        self.session.hooks["response"].append(self._update_rate_limit)

    def _update_rate_limit(self, resp, *args, **kw):
        if 'X-RateLimit-Limit' not in resp.headers:
            return

        now = time.time()
        if self._rate_limit is None or now - self._rate_limit['last'] > 60:
            self._rate_limit = {
                "limit": int(resp.headers['X-RateLimit-Limit']),
                "remaining": int(resp.headers['X-RateLimit-Remaining']),
                "reset": int(resp.headers["X-RateLimit-Reset"]),
                "last": time.time(),
            }

            if self._rate_limit["remaining"] <= 100:
                print(
                    "WARNING!  Only {} API calls left for the next {} "
                    "seconds; going to wait that many seconds...".format(
                        self._rate_limit["remaining"],
                        self._rate_limit["reset"] - self._rate_limit["last"]
                    )
                )
                seconds = self._rate_limit["reset"] - self._rate_limit["last"]
                while seconds > 0:
                    print("Sleeping....{} seconds remaining".format(seconds))
                    time.sleep(30)
                    seconds -= 30
                print("OK done sleeping, let's hope it reset")
                # return so the next API call will come back here and
                # update rate limit again
                return

            self._rate_limit['rate_per_sec'] = (
                self._rate_limit['remaining'] /
                (self._rate_limit['reset'] - self._rate_limit['last'])
            )

            print(
                "Refreshed github rate limit.  {} requests out "
                "of {} remaining, until {} seconds from now.   Will run "
                "API calls at {} requests per second".format(
                    self._rate_limit['remaining'],
                    self._rate_limit['limit'],
                    self._rate_limit["reset"] - self._rate_limit["last"],
                    self._rate_limit["rate_per_sec"]
                ))

    def _wait_for_api(self):
        if self._rate_limit is None:
            return
        now = time.time()
        if self._last_push_time:
            time_passed = now - self._last_push_time
            delay = (1 / self._rate_limit['rate_per_sec'])
            sleep_for = delay - time_passed
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._last_push_time = time.time()

    def _api_get(self, url):
        self._wait_for_api()
        resp = self.session.get(
            url,
            headers={
                "Authorization": "token %s" % self.access_token
            }
        )
        if resp.status_code != 200:
            raise Exception(
                "Got response %s for %s: %s" %
                (resp.status_code, url, resp.content))

        return resp

    def _yield_with_links(self, url):
        while url is not None:
            resp = self._api_get(url)
            next_ = resp.links.get('next')
            if next_:
                url = next_['url']
            else:
                url = None
            for rec in resp.json():
                yield rec

    def get_comments_since(self, last_received):
        # get issues in updated_at order ascending, so we can
        # continue updating our "updated_at" value

        url = (
            "https://api.github.com/repos/%s/issues/comments?"
            "state=all&sort=updated&direction=asc&per_page=100" % self.repo
        )
        if last_received:
            url = "%s&since=%s" % (url, last_received)

        idx = 1
        for idx, comment in enumerate(self._yield_with_links(url), idx):
            if idx % 100 == 0:
                print("received %s comments" % idx)
            issue_num = int(
                re.match(r'.*/issues/(\d+)$', comment['issue_url']).group(1)
            )
            comment['issue_number'] = issue_num
            yield comment

        print("received %s comments total" % idx)

    def get_issues_since(self, last_received):
        # get issues in updated_at order ascending, so we can
        # continue updating our "updated_at" value

        url = (
            "https://api.github.com/repos/%s/issues?"
            "state=all&sort=updated&direction=asc&per_page=100" % self.repo
        )
        if last_received:
            url = "%s&since=%s" % (url, last_received)

        idx = 1
        for idx, issue in enumerate(self._yield_with_links(url), idx):
            if idx % 100 == 0:
                print("received %s issues" % idx)
            yield issue

        print("received %s issues total" % idx)

    def get_issue_events(self, issue_number):
        url = (
            "https://api.github.com/repos/"
            "%s/issues/%s/events" % (self.repo, issue_number)
        )
        return self._yield_with_links(url)

    def get_attachment(self, url):
        resp = self.session.get(url)
        if resp.status_code != 200:
            raise Exception("Got response %s for %s" % (resp.status_code, url))

        return resp.content

    def find_attachments(self, json):
        return self._scan_attachments(json['body'])

    def _scan_attachments(self, body):
        # not sure if the repo stays constant in the bodies if the
        # repo is moved to a different owner/name
        for m in re.finditer(
                r'https://github.com/.+?/.+?/files/\d+/(\S*\w)', body):
            yield m.group(1), m.group(0)
        for m in re.finditer(
                r'\.\./wiki/imported_issue_attachments/(\d+)/(\S*\w)', body):
            int_num = m.group(1)
            filename = m.group(2)
            abs_ = (
                "https://github.com/%s/wiki/"
                "imported_issue_attachments/%s/%s" % (
                    self.repo, int_num, filename)
            )
            yield filename, abs_


def _ensure_directory(path):
    dirname = os.path.dirname(path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)


def _write_file(path, content, mode):
    _ensure_directory(path)
    print("Writing %s bytes to %s" % (len(content), path))
    with open(path, mode) as file_:
        file_.write(content)


def _write_json_file(path, json_data):
    _ensure_directory(path)
    print("Writing json to %s" % (path, ))
    with open(path, "w") as file_:
        json.dump(json_data, file_, indent=4)


def run_jobs(iterator, jobs, completed_callback):
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


def run_sync(gh, destination):
    destination = os.path.abspath(destination)
    if not os.path.exists(destination):
        os.makedirs(destination)

    last_received_file = os.path.join(destination, "last_received.txt")
    if not os.path.exists(last_received_file):
        last_received = None
    else:
        with open(last_received_file, "r") as file_:
            content = file_.read().strip()
            url, last_received = content.split("\n")
            if url != gh.url:
                print(
                    "Persisted url %s does not match the "
                    "URL we're getting right now: %s, exiting" % (
                        url, gh.url
                    ))
                sys.exit(-1)

    highest_timestamp = None

    pool = multiprocessing.Pool(WORKERS)

    jobs = []

    def completed_callback(name):
        def do_completed(idx, is_done):
            print(
                "Completed %s %s, most recent updated at: %s" %
                (idx, name, highest_timestamp))
            _write_file(
                last_received_file,
                "%s\n%s" % (gh.url, highest_timestamp),
                "w"
            )
        return do_completed

    for issue in run_jobs(
        gh.get_issues_since(last_received), jobs,
        completed_callback("issues")
    ):
        if highest_timestamp is None or \
                issue["updated_at"] > highest_timestamp:
            highest_timestamp = issue["updated_at"]

        issue_dest = os.path.join(
            destination, "issues",
            str(issue["number"] // 100), str(issue["number"])
        )

        attachments = []

        attachments.extend(gh.find_attachments(issue))

        events = list(gh.get_issue_events(issue["number"]))
        _write_json_file(os.path.join(issue_dest, "events.json"), events)

        jobs.append(
            pool.apply_async(
                _fetch_attachments,
                (gh, issue_dest, attachments, )
            )
        )

        _write_json_file(os.path.join(issue_dest, "issue.json"), issue)

    for comment in run_jobs(
        gh.get_comments_since(last_received), jobs,
        completed_callback("comments")
    ):
        if highest_timestamp is None or \
                comment["updated_at"] > highest_timestamp:
            highest_timestamp = comment["updated_at"]

        issue_dest = os.path.join(
            destination, "issues",
            str(comment["issue_number"] // 100), str(comment["issue_number"])
        )

        attachments = []

        attachments.extend(gh.find_attachments(comment))

        jobs.append(
            pool.apply_async(
                _fetch_attachments,
                (gh, issue_dest, attachments, )
            )
        )

        _write_json_file(os.path.join(
            issue_dest, "comment_%s_%s.json" % (
                comment['created_at'],
                comment['id']
            )
        ), comment)


def _fetch_attachments(gh, issue_dest, attachments):
    for filename, url in attachments:
        attachment_path = os.path.join(
            issue_dest, "attachments", filename)
        content = gh.get_attachment(url)
        _write_file(attachment_path, content, "wb")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "repo", type=str, help="user/reponame string on github")
    parser.add_argument("dest", type=str, help="directory in which to sync")
    parser.add_argument(
        "--access-token", type=str, help="oauth access token")

    opts = parser.parse_args(argv)
    gh = GitHub(
        opts.repo,
        access_token=opts.access_token,
        concurrency=WORKERS
    )

    run_sync(gh, opts.dest)

