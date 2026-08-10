import re
from typing import Callable
from typing import Iterable
from typing import List
from typing import NamedTuple
from typing import Optional

from ... import github

DEFAULT_LABEL = "open for pull requests"

# applied to the issue once a pull request has claimed it, and removed
# from that same issue.  the swap is what keeps a second pull request --
# increasingly, a bot-written one -- from landing on top of work already
# under review.  note this label is also applied by hand for a review
# happening purely in gerrit with no github pull request at all.
DEFAULT_REVIEW_LABEL = "code review in progress"

# put on an *issue* to say that no outside pull request is wanted for it
# at all, whatever else the issue is labeled.
DEFAULT_DENY_LABEL = "NO pull requests please"

# put on a *pull request* by a maintainer to wave it through regardless
# of anything else.  the escape hatch for the case the rules don't fit:
# a doc fix worth taking, a contributor worth making an exception for.
# note this can't pre-empt the first close, since the label can't exist
# before the pull request does; the flow is that the gate closes it, a
# maintainer labels it, and reopening it then passes.
DEFAULT_APPROVED_LABEL = "approved for development"

# reasons a pull request was allowed through
ALLOW_APPROVED = "approved"
ALLOW_MAINTAINER = "maintainer"
ALLOW_QUALIFIED_ISSUE = "qualified_issue"
ALLOW_EXISTING_CLAIM = "existing_claim"

# reasons a pull request was closed, in increasing order of specificity;
# when several referenced issues each fail, the most specific reason is
# the one reported, since it gives the contributor the most actionable
# instruction.
CLOSE_NO_ISSUE = "no_issue"
CLOSE_ISSUE_CLOSED = "issue_closed"
CLOSE_ISSUE_UNLABELED = "issue_unlabeled"
CLOSE_ISSUE_IN_REVIEW = "issue_in_review"
CLOSE_ISSUE_DENIED = "issue_denied"

_CLOSE_SPECIFICITY = {
    CLOSE_NO_ISSUE: 0,
    CLOSE_ISSUE_CLOSED: 1,
    CLOSE_ISSUE_UNLABELED: 2,
    CLOSE_ISSUE_IN_REVIEW: 3,
    CLOSE_ISSUE_DENIED: 4,
}

_FENCED_CODE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# a "#123" that is not part of a word, a path, or a URL fragment such as
# "somefile.py#L123".  tracebacks and log output pasted into a PR body
# are full of things that look like issue references, which is why the
# body is stripped of code blocks before this ever runs.
_HASH_REFERENCE = re.compile(r"(?<![\w/#])#(\d+)\b")


class GateResult(NamedTuple):
    """The decision for a single pull request.

    ``action`` is "allow" or "close"; ``reason`` is one of the ALLOW_ /
    CLOSE_ constants above, and ``issue`` is the issue number the
    decision was based on, if there was one.

    """

    action: str
    reason: str
    issue: Optional[int]


def pr_is_opened_or_reopened(event: github.GithubEvent) -> bool:
    """Gate only on open and reopen.

    Deliberately not "synchronize" or "edited": those fire on every push
    and every description tweak, which would re-run the gate and
    re-comment for the life of the pull request.  Reopen is included so
    that a contributor whose issue has since been labeled can simply
    reopen and be re-evaluated.

    """

    return bool(event.json_data["action"] in ("opened", "reopened"))


def strip_noncontent(text: str) -> str:
    """Remove regions of markdown that shouldn't be scanned for issue
    references.

    Fenced code blocks and inline code routinely contain "#123" in
    tracebacks, SQL comments and shell output.  HTML comments are how
    the pull request template itself carries instructions, which may
    include an example issue reference.

    """

    text = _HTML_COMMENT.sub(" ", text)
    text = _FENCED_CODE.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    return text


def find_issue_references(
    title: Optional[str], body: Optional[str], repo: str
) -> List[int]:
    """Return issue numbers referenced by a pull request, in the order
    they appear, without duplicates.

    Recognizes "#123", "Fixes: #123" and friends (the keyword adds
    nothing our side, "#123" alone is the part we key on), the fully
    qualified "owner/repo#123" form, and issue URLs.  Only references to
    ``repo`` itself count; a link to some other project's issue tracker
    says nothing about whether this project authorized the work.

    """

    text = strip_noncontent("%s\n\n%s" % (title or "", body or ""))

    quoted_repo = re.escape(repo)
    patterns = [
        _HASH_REFERENCE,
        re.compile(r"\b%s#(\d+)\b" % quoted_repo, re.I),
        re.compile(
            r"https?://(?:www\.)?github\.com/%s/issues/(\d+)" % quoted_repo,
            re.I,
        ),
    ]

    found = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            found.append((match.start(), int(match.group(1))))
    found.sort()

    seen = set()
    numbers = []
    for _, number in found:
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    return numbers


def user_has_write_permission(
    gh_repo: github.GithubRepo, username: str
) -> bool:
    """True if the user can push to the repo.

    These are the people who apply the label in the first place, so
    gating them would be circular.  A non-collaborator produces a 404,
    which ``get_user_permission`` turns into None.

    """

    permission = gh_repo.get_user_permission(username)
    return bool(
        permission and permission.get("permission") in ("admin", "write")
    )


def evaluate_pr(
    gh_repo: github.GithubRepo,
    repo: str,
    sender: str,
    title: Optional[str],
    body: Optional[str],
    label: str = DEFAULT_LABEL,
    review_label: str = DEFAULT_REVIEW_LABEL,
    deny_label: str = DEFAULT_DENY_LABEL,
    approved_label: str = DEFAULT_APPROVED_LABEL,
    pr_labels: Optional[Iterable[str]] = None,
    exempt_maintainers: bool = True,
    holds_claim: Optional[Callable[[int], bool]] = None,
) -> GateResult:
    """Decide whether a pull request is authorized.

    A pull request is allowed through if it references at least one
    issue in this same repo that is open and carries ``label``.  The
    first qualifying issue wins, so a pull request citing several issues
    passes if any one of them was authorized.

    An issue carrying ``review_label`` instead has already been claimed
    by some pull request, and is closed to any other.  ``holds_claim``
    is called with an issue number to ask whether *this* pull request is
    the one that claimed it; that's what lets an accepted pull request
    survive being closed and reopened, since by then the labels on its
    issue have already been swapped.

    An issue carrying ``deny_label`` wants no outside pull request at
    all, and one reference to such an issue closes the pull request
    whatever else it references.

    ``pr_labels`` are the labels on the pull request itself.
    ``approved_label`` among them is a maintainer's manual override and
    beats every other consideration here.

    """

    if approved_label.lower() in {name.lower() for name in pr_labels or ()}:
        return GateResult("allow", ALLOW_APPROVED, None)

    if exempt_maintainers and user_has_write_permission(gh_repo, sender):
        return GateResult("allow", ALLOW_MAINTAINER, None)

    references = find_issue_references(title, body, repo)
    if not references:
        return GateResult("close", CLOSE_NO_ISSUE, None)

    label_key = label.lower()
    review_label_key = review_label.lower()
    deny_label_key = deny_label.lower()
    best: Optional[GateResult] = None

    for number in references:
        issue = gh_repo.get_issue(str(number))

        # a number that resolves to nothing, or to a pull request rather
        # than an issue, tells us nothing either way; keep looking.
        if issue is None or issue.get("pull_request"):
            continue

        if issue.get("state") != "open":
            best = _more_specific(
                best, GateResult("close", CLOSE_ISSUE_CLOSED, number)
            )
            continue

        names = {rec["name"].lower() for rec in issue.get("labels") or ()}

        if deny_label_key in names:
            # a deliberate "we are doing this one ourselves".  don't let
            # another reference in the same pull request talk us out of
            # it, and don't tell the contributor to go ask for the label.
            return GateResult("close", CLOSE_ISSUE_DENIED, number)

        if label_key in names:
            return GateResult("allow", ALLOW_QUALIFIED_ISSUE, number)

        if review_label_key in names:
            # already claimed.  the one pull request allowed past this is
            # the one that did the claiming, coming back around after a
            # close and reopen.
            if holds_claim is not None and holds_claim(number):
                return GateResult("allow", ALLOW_EXISTING_CLAIM, number)

            best = _more_specific(
                best, GateResult("close", CLOSE_ISSUE_IN_REVIEW, number)
            )
            continue

        best = _more_specific(
            best, GateResult("close", CLOSE_ISSUE_UNLABELED, number)
        )

    # every reference either failed or didn't resolve to a real issue
    return best or GateResult("close", CLOSE_NO_ISSUE, None)


def _more_specific(
    current: Optional[GateResult], new: GateResult
) -> GateResult:
    if current is None:
        return new
    elif _CLOSE_SPECIFICITY[new.reason] > _CLOSE_SPECIFICITY[current.reason]:
        return new
    else:
        return current
