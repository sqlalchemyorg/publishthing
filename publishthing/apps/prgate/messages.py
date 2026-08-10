"""The comments the gate leaves on a pull request it closes.

These land on people who are often first-time contributors and who have
already done the work, so they aim to be procedural rather than
punitive: say what the rule is, make clear the change itself hasn't been
judged, and give the exact steps to get it reconsidered.

Note that github renders a single newline inside a comment as a line
break, so every paragraph and list item below is one long line; wrapping
them for the sake of the source file would show up as ragged wrapping in
the rendered comment.

"""

from typing import Optional

from . import util

# hidden marker so the gate can recognize its own previous comment on a
# pull request and not repeat itself if github redelivers a webhook.
MARKER_PREFIX = "<!-- prgate"

# hidden marker recording that this pull request is the one that claimed
# an issue.  it has to survive on the pull request itself, because by the
# time the pull request is closed and reopened the issue no longer
# carries the label that let it through the first time.
CLAIM_MARKER_PREFIX = "<!-- prgate-claim"


def marker(reason: str, sha: str) -> str:
    return "%s:%s:%s -->" % (MARKER_PREFIX, reason, sha)


def claim_marker(issue: int) -> str:
    return "%s:%s -->" % (CLAIM_MARKER_PREFIX, issue)


_INTRO = "Hi, and thanks for the pull request!"

_POLICY = (
    "This project accepts pull requests only for issues that a "
    "maintainer has already marked with the **%(label)s** label. That "
    "way we can settle on an approach before anyone spends time "
    "writing code."
)

_STEPS_NO_ISSUE = (
    "This pull request doesn't reference an issue, so I'm closing it "
    "automatically. To move it forward:\n"
    "\n"
    "1. Open an issue describing the problem or the feature, including "
    "a complete, runnable example.\n"
    "2. Wait for a maintainer to add the **%(label)s** label to it.\n"
    "3. Reopen this pull request, with the issue number in the "
    "description, as `Fixes: #%(example)s`.\n"
)

_STEPS_UNLABELED = (
    "This pull request references issue #%(issue)s, which hasn't been "
    "marked **%(label)s**, so the change isn't authorized yet and I'm "
    "closing it automatically. Once a maintainer adds the label to "
    "#%(issue)s, reopen this pull request and it will stay open. If "
    "#%(issue)s needs more detail before that can happen, a complete "
    "runnable example is usually the missing piece, and adding one to "
    "the issue is the fastest way to get there."
)

_STEPS_CLOSED = (
    "This pull request references issue #%(issue)s, which is closed, so "
    "the change isn't authorized against an open issue and I'm closing "
    "it automatically. If the problem is still present, please say so "
    "on #%(issue)s, or open a new issue with a complete, runnable "
    "example. Once a maintainer marks the open issue **%(label)s**, "
    "reopen this pull request."
)

_STEPS_IN_REVIEW = (
    "This pull request references issue #%(issue)s, which is already "
    "marked **%(review_label)s** -- someone is working on it, either in "
    "another pull request or directly in gerrit -- so I'm closing this "
    "one automatically to keep the two from landing on top of each "
    "other. If you think your approach is better than the one in "
    "review, the place to make that case is on #%(issue)s."
)

_STEPS_DENIED = (
    "This pull request references issue #%(issue)s, which is marked "
    "**%(deny_label)s** -- that one is being handled by the maintainers "
    "directly, so it isn't open to outside pull requests and asking for "
    "it to be opened up won't change that. If you'd like to contribute "
    "something else, look for an issue marked **%(label)s**."
)

_NOT_A_JUDGMENT = (
    "This is automatic and procedural. It isn't a judgment on your "
    "change, and nothing you've written here is lost."
)

_SIGNOFF = "Thanks for your interest in the project!"

_ACCEPTED = (
    "Thanks! Issue #%(issue)s is now marked **%(review_label)s** and no "
    "longer **%(label)s**, so this pull request holds the review for it "
    "and another one won't land on top of your work. If this pull "
    "request is abandoned, a maintainer can put **%(label)s** back on "
    "#%(issue)s to reopen it to others."
)


def accepted_message(issue: int, label: str, review_label: str) -> str:
    """Assemble the comment left on a pull request that claims an issue.

    Besides telling the contributor why the labels on the issue just
    changed, this comment is where the claim itself is recorded; see
    ``claim_marker``.

    """

    return "\n\n".join(
        [
            claim_marker(issue),
            _ACCEPTED
            % {
                "issue": issue,
                "label": label,
                "review_label": review_label,
            },
        ]
    )


def close_message(
    result: util.GateResult,
    label: str,
    sha: str,
    review_label: str = util.DEFAULT_REVIEW_LABEL,
    deny_label: str = util.DEFAULT_DENY_LABEL,
    policy_url: Optional[str] = None,
) -> str:
    """Assemble the comment for a pull request being closed."""

    subs = {
        "label": label,
        "review_label": review_label,
        "deny_label": deny_label,
        "issue": result.issue,
        # deliberately not a number: text copied out of this comment
        # into a pull request body must not read as a real reference to
        # whatever issue happens to have that number.
        "example": "<issue number>",
    }

    if result.reason == util.CLOSE_ISSUE_UNLABELED:
        steps = _STEPS_UNLABELED
    elif result.reason == util.CLOSE_ISSUE_CLOSED:
        steps = _STEPS_CLOSED
    elif result.reason == util.CLOSE_ISSUE_IN_REVIEW:
        steps = _STEPS_IN_REVIEW
    elif result.reason == util.CLOSE_ISSUE_DENIED:
        steps = _STEPS_DENIED
    else:
        steps = _STEPS_NO_ISSUE

    paragraphs = [
        marker(result.reason, sha),
        _INTRO,
        _POLICY % subs,
        (steps % subs).strip(),
        _NOT_A_JUDGMENT,
    ]

    if policy_url:
        paragraphs.append(
            "The full contribution guidelines are at %s" % policy_url
        )

    paragraphs.append(_SIGNOFF)

    return "\n\n".join(paragraphs)
