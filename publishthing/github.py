import hmac
import json
import re
import time
from typing import Any
from typing import Dict
from typing import Iterator
from typing import Optional
from typing import Tuple

import requests

from . import publishthing  # noqa
from . import wsgi  # noqa
from .util import Hooks

GithubJsonRec = Dict[str, Any]


class GithubRepo:
    _last_push_time = 0.0
    _rate_limit = None

    def __init__(self, thing: "publishthing.PublishThing", repo: str) -> None:
        self.thing = thing
        self.repo = repo
        self.url = "https://github.com/%s" % repo
        self.access_token = thing.opts["github_access_token"]
        self.concurrency = thing.opts.get("github_api_concurrency", 1)
        self.session = requests.Session()
        self.session.hooks["response"].append(self._update_rate_limit)

    def _update_rate_limit(self, resp: Any, *args: Any, **kw: Any) -> None:
        if "X-RateLimit-Limit" not in resp.headers:
            return

        now = time.time()
        if self._rate_limit is None or now - self._rate_limit["last"] > 60:
            self._rate_limit = {
                "limit": int(resp.headers["X-RateLimit-Limit"]),
                "remaining": int(resp.headers["X-RateLimit-Remaining"]),
                "reset": int(resp.headers["X-RateLimit-Reset"]),
                "last": time.time(),
            }

            if self._rate_limit["remaining"] <= 100:
                self.thing.warning(
                    "WARNING!  Only {} API calls left for the next {} "
                    "seconds; going to wait that many seconds...".format(
                        self._rate_limit["remaining"],
                        self._rate_limit["reset"] - self._rate_limit["last"],
                    )
                )
                seconds = self._rate_limit["reset"] - self._rate_limit["last"]
                while seconds > 0:
                    self.thing.message(
                        "Sleeping....{} seconds remaining".format(seconds)
                    )
                    time.sleep(30)
                    seconds -= 30
                self.thing.message("OK done sleeping, let's hope it reset")
                # return so the next API call will come back here and
                # update rate limit again
                return

            self._rate_limit["rate_per_sec"] = self._rate_limit[
                "remaining"
            ] / (self._rate_limit["reset"] - self._rate_limit["last"])

            self.thing.message(
                "Refreshed github rate limit.  {} requests out "
                "of {} remaining, until {} seconds from now.   Will run "
                "API calls at {} requests per second".format(
                    self._rate_limit["remaining"],
                    self._rate_limit["limit"],
                    self._rate_limit["reset"] - self._rate_limit["last"],
                    self._rate_limit["rate_per_sec"],
                )
            )

    def _wait_for_api(self) -> None:
        if self._rate_limit is None:
            return
        now = time.time()
        if self._last_push_time:
            time_passed = now - self._last_push_time
            delay = 1 / self._rate_limit["rate_per_sec"]
            sleep_for = delay - time_passed
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._last_push_time = time.time()

    def _api_get(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> requests.Response:
        self._wait_for_api()
        _headers = {"Authorization": "token %s" % self.access_token}
        if headers:
            _headers.update(headers)
        resp = self.session.get(url, headers=_headers)
        if resp.status_code != 200:
            raise Exception(
                "Got response %s for %s: %s"
                % (resp.status_code, url, resp.content)
            )

        return resp

    def _api_post(self, url: str, rec: GithubJsonRec) -> requests.Response:
        self._wait_for_api()
        resp = self.session.post(
            url,
            headers={"Authorization": "token %s" % self.access_token},
            json=rec,
        )
        if resp.status_code > 299:
            raise Exception(
                "Got response %s for %s: %s"
                % (resp.status_code, url, resp.content)
            )

        return resp

    def _api_patch(self, url: str, rec: GithubJsonRec) -> requests.Response:
        self._wait_for_api()
        resp = self.session.patch(
            url,
            headers={"Authorization": "token %s" % self.access_token},
            json=rec,
        )
        if resp.status_code > 299:
            raise Exception(
                "Got response %s for %s: %s"
                % (resp.status_code, url, resp.content)
            )

        return resp

    def create_pr_review(
        self,
        issue_number: str,
        body: str,
        sha: Optional[str] = None,
        event: Optional[str] = None,
    ) -> None:

        url = "https://api.github.com/repos/%s/pulls/%s/reviews" % (
            self.repo,
            issue_number,
        )
        rec = {"body": body}
        if event:
            rec["event"] = event
        if sha:
            rec["commit_id"] = sha
        self._api_post(url, rec=rec)

    def get_pull_request(self, issue_number: str) -> GithubJsonRec:

        url = "https://api.github.com/repos/%s/pulls/%s" % (
            self.repo,
            issue_number,
        )
        return self._api_get(url).json()

    def get_pull_request_comments(
        self, issue_number: str
    ) -> Iterator[GithubJsonRec]:

        url = (
            "https://api.github.com/repos/%s/pulls/%s/comments"
            "?direction=asc&per_page=100" % (self.repo, issue_number)
        )
        return self._yield_with_links(url)

    def get_review_comments(
        self, issue_number: str, review_id: int
    ) -> Iterator[GithubJsonRec]:

        url = (
            "https://api.github.com/repos/%s/pulls/%s/reviews/%s/comments"
            "?direction=asc&per_page=100"
            % (self.repo, issue_number, review_id)
        )
        return self._yield_with_links(url)

    def get_pull_request_diff(self, issue_number: str) -> str:

        url = "https://api.github.com/repos/%s/pulls/%s" % (
            self.repo,
            issue_number,
        )
        return self._api_get(
            url, headers={"Accept": "application/vnd.github.v3.diff"}
        ).text

    def publish_review(
        self, issue_number: str, github_review: GithubJsonRec
    ) -> None:
        url = "https://api.github.com/repos/%s/pulls/%s/reviews" % (
            self.repo,
            issue_number,
        )
        self._api_post(url, rec=github_review)

    def publish_issue_comment(self, issue_number: str, message: str) -> None:
        url = "https://api.github.com/repos/%s/issues/%s/comments" % (
            self.repo,
            issue_number,
        )
        self._api_post(url, rec={"body": message})

    def publish_pr_review_comment(
        self, pullreq_number: str, comment_rec: GithubJsonRec
    ) -> None:

        url = "https://api.github.com/repos/%s/pulls/%s/comments" % (
            self.repo,
            pullreq_number,
        )
        self._api_post(url, rec=comment_rec)

    def create_status(
        self,
        sha: str,
        state: str,
        description: str,
        context: str,
        target_url: Optional[str] = None,
    ) -> None:

        url = "https://api.github.com/repos/%s/statuses/%s" % (self.repo, sha)
        body = {"state": state, "description": description, "context": context}
        if target_url:
            body["target_url"] = target_url
        self._api_post(url, rec=body)

    def publish_pr_comment_w_status_change(
        self,
        issue_number: str,
        sha: str,
        message: str,
        state: str,
        context: str,
        target_url: Optional[str] = None,
        long_message: Optional[str] = None,
    ) -> None:
        """a combination of publish_issue_comment and create_status.

        Since the github display for "status" alone is a little too low key
        for folks to notice gerrit links and such.

        """
        if long_message:
            comment_message = long_message
        elif target_url:
            comment_message = "%s: %s" % (message, target_url)
        else:
            comment_message = message

        self.publish_issue_comment(issue_number, comment_message)
        self.create_status(sha, state, message, context, target_url=target_url)

    def set_pull_request_status(
        self, issue_number: str, closed: bool = True
    ) -> None:
        url = "https://api.github.com/repos/%s/pulls/%s" % (
            self.repo,
            issue_number,
        )
        self._api_patch(url, rec={"state": "closed" if closed else "open"})

    def _yield_with_links(self, url: Optional[str]) -> Iterator[GithubJsonRec]:
        while url is not None:
            resp = self._api_get(url)
            next_ = resp.links.get("next")
            if next_:
                url = next_["url"]
            else:
                url = None
            for rec in resp.json():
                yield rec

    def get_comments_since(
        self, last_received: Optional[str]
    ) -> Iterator[GithubJsonRec]:
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
            match = re.match(r".*/issues/(\d+)$", comment["issue_url"])
            if match:
                issue_num = int(match.group(1))
                comment["issue_number"] = issue_num
            yield comment

        self.thing.message("received %s comments total" % idx)

    def get_issues_since(
        self, last_received: Optional[str]
    ) -> Iterator[GithubJsonRec]:
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

        self.thing.message("received %s issues total" % idx)

    def get_issue_events(self, issue_number: str) -> Iterator[GithubJsonRec]:
        url = "https://api.github.com/repos/" "%s/issues/%s/events" % (
            self.repo,
            issue_number,
        )
        return self._yield_with_links(url)

    def get_attachment(self, url: str) -> bytes:
        resp = self.session.get(url)
        if resp.status_code != 200:
            raise Exception("Got response %s for %s" % (resp.status_code, url))

        return resp.content

    def find_attachments(
        self, json: GithubJsonRec
    ) -> Iterator[Tuple[str, str]]:
        return self._scan_attachments(json["body"])

    def _scan_attachments(self, body: str) -> Iterator[Tuple[str, str]]:
        # not sure if the repo stays constant in the bodies if the
        # repo is moved to a different owner/name
        for m in re.finditer(
            r"https://github.com/.+?/.+?/files/\d+/(\S*\w)", body
        ):
            yield m.group(1), m.group(0)

        # this is the custom system we use in zzzeek's version of
        # bitbucket-issue-migration
        for m in re.finditer(
            r"\.\./wiki/imported_issue_attachments/(\d+)/(\S*\w)", body
        ):
            int_num = m.group(1)
            filename = m.group(2)
            abs_ = (
                "https://github.com/%s/wiki/"
                "imported_issue_attachments/%s/%s"
                % (self.repo, int_num, filename)
            )
            yield filename, abs_


class GithubEvent:
    def __init__(self, json_data: Any, event: str, delivery: str) -> None:
        self.json_data = json_data
        self.event = event
        self.delivery = delivery

    @property
    def repo_name(self) -> str:
        return self.json_data["repository"]["full_name"].strip("/")


class GithubWebhook(Hooks):
    def __init__(self, thing: "publishthing.PublishThing") -> None:
        self.thing = thing
        self.secret = thing.opts["github_webhook_secret"]
        super(GithubWebhook, self).__init__()

        @self.event("ping")
        def return_ping(event: GithubEvent, request: wsgi.WsgiRequest) -> None:
            request.add_text("OK!")

    def __call__(
        self, environ: wsgi.WsgiEnviron, start_response: wsgi.WsgiStartResponse
    ) -> wsgi.WsgiResponse:
        request = self.thing.wsgi_request(environ, start_response)

        return_ = self._enforce_secret(self.secret, request)
        if return_ is not None:
            # this would be the error response
            return return_

        payload = request.params.get("payload", None)
        if payload is None:
            payload = request.body.decode("utf-8")

        request.debug("webhook", "message received....")
        try:
            json_data = json.loads(payload)
        except ValueError:
            request.debug("webhook", "couldn't parse payload")
            return request.respond(400, "couldn't parse payload")
        else:
            event = GithubEvent(
                json_data,
                request.headers["x-github-event"],
                request.headers["x-github-delivery"],
            )
            self._run_hooks(event.event, event, request)

            return request.respond(200)

    def _enforce_secret(
        self, secret: str, request: wsgi.WsgiRequest
    ) -> Optional[wsgi.WsgiResponse]:

        header_signature = request.headers.get("X-Hub-Signature")
        if header_signature is None:
            return request.respond(403, "Signed header required")

        sha_name, signature = header_signature.split("=")
        if sha_name != "sha1":
            return request.respond(501, "invalid signature")

        mac = hmac.new(
            secret.encode("ascii"), msg=request.body, digestmod="sha1"
        )

        if not hmac.compare_digest(mac.hexdigest(), signature):
            return request.respond(403, "invalid signature")

        return None
