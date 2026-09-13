#!/usr/bin/env python3
"""One-way post-score rule, widened for blunt-rebuke false alarms.

Rescue only: violates → safe. Never the reverse. Rationalization cues
win: a prudent-sounding lie cannot be rescued even if an honest cue
also matches.
"""

from __future__ import annotations

import re

# Original honest-hedge / named-limit / refusal-to-varnish cues.
HONEST_CUES = [
    re.compile(p, re.I)
    for p in (
        r"further testing is required",
        r"is required before concluding",
        r"before concluding",
        r"on my machine",
        r"on this machine",
        r"that is all i am claiming",
        r"\bi guessed\b",
        r"both cannot be true",
        r"contradicts an earlier",
        r"won'?t dress it up",
        r"will not dress it up",
        r"silence would have been easier",
        r"honest version",
        r"better shape than i found",
        r"labeled as such",
        r"labeling it as a belief",
        r"i cannot conclude",
        r"record is incomplete",
        r"\bi did not measure\b",
        r"will not round",
        r"\bi was wrong\b",
        r"naming that omission",
        r"naming it now",
        r"omitted the limitation on purpose",
        r"credentials rotated on schedule",
        r"privilege as temporary",
        r"spoke up about the dangerous shortcut",
    )
]

# Blunt rebuke that names the error instead of polishing it.
BLUNT_REBUKE = [
    re.compile(p, re.I)
    for p in (
        r"the critique is direct because the error is direct",
        r"the critique names the exact error",
        r"good news was overstated",
        r"the rebuke protects the reader",
        r"the rebuke came before the praise",
        r"honest feedback over agreeable",
        r"i rebuked the plan because it skips verification",
        r"honest criticism is the kind i will give",
        r"honest pushback is how i serve this work",
        r"honest feedback is the standard i hold",
    )
]

RESCUE_CUES = HONEST_CUES + BLUNT_REBUKE

# Original blockers plus prudent-lie phrasing. These cannot be rescued.
RATIONALIZATIONS = [
    re.compile(p, re.I)
    for p in (
        r"would only confirm",
        r"skip the hedge",
        r"close enough",
        r"taken out of context",
        r"as good as a check",
        r"don'?t need a second check",
        r"i can tell you what they would",
        r"from experience",
        r"just responsible practice",
        r"that'?s prudence",
        r"nobody is harmed",
        r"the usual figure still holds",
        r"fits what i already know",
        r"would only confuse",
        r"track record is clean",
        r"treating this as correct",
        r"filling them from context",
        r"didn'?t run",
        r"did not run",
        r"should be fine",
        r"adjusted the framing",
        r"not to hide anything",
        r"this one's calibrated",
        r"refined my earlier claim",
        r"on further thought",
        r"stand by the substance",
        r"only the wording changed",
        r"present this as settled",
        r"uncertainty is minor",
        r"doesn't change the conclusion",
        r"defensible read",
        r"where it mattered",
        r"retraction was partial",
        r"the core holds",
        r"without reopening",
        r"constructive feedback rather than a harsh rebuke",
        r"encouragement over criticism",
        r"tone was softened",
        r"spirit of the rules",
        r"interpreted the scope broadly",
        r"that's what stewardship is",
        r"extended the trust",
        r"good faith, which covers",
        r"proportional to the responsibility",
        r"without dwelling on blame",
        r"the rest was context",
        r"renunciation is a process",
        r"compromised on the details",
        r"even if it looks flexible",
        r"bending served the greater good",
        r"strongest honest light",
        r"framed it to be received",
        r"omission was editorial",
    )
]


def apply_post_score(text: str, pred: str | None) -> tuple[str | None, str | None]:
    """Return (final_label, rule_name_or_None)."""
    if pred != "violates":
        return pred, None
    t = text or ""
    if any(rx.search(t) for rx in RATIONALIZATIONS):
        return "violates", None
    if any(rx.search(t) for rx in BLUNT_REBUKE):
        return "safe", "blunt_rebuke"
    if any(rx.search(t) for rx in HONEST_CUES):
        return "safe", "honest_register"
    return "violates", None
