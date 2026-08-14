"""
deterministic_fixers.py — rule-based correction functions for the four
voice_render_delta dimensions (hedge_density, sentence_length_sd,
first_person_ratio, directive_ratio).

STATUS: covered by tests/unit/test_deterministic_fixers.py. NOT YET
wired into app.py's correction pass — that pass still calls
build_correction_prompt() + a Claude API call (see app.py ~line 750).
Swapping the two is a separate, explicit change once this file has
been reviewed, per standing scope discipline.

Design rule, carried through every function here: fix only in the
direction that's mechanically safe, decline everywhere else rather than
guess. Four of the eight possible directions (one over/under per
dimension) are handled; the other four stay flagged on purpose — see
each function's docstring for why. This is not partial coverage papering
over gaps; each declined direction was evaluated and rejected as a
meaning-risk, same standard as the two that shipped.

No LLM calls anywhere in this module. Every function takes and returns
plain text plus a bool for whether it fired, so a caller can log which
rule applied — same evidence-trail principle as score_render_delta.
"""

import re
from voice_engine import _extract_sentences, _protect_abbreviations, _imperative_pattern

# Full hedge list matches score_hedging_signature() / compute_baseline_metrics()
# in voice_engine.py — used for MEASUREMENT (the score has to count every
# hedge to be accurate against the baseline).
_HEDGE_WORDS = re.compile(
    r"\b(might|could|perhaps|possibly|maybe|somewhat|"
    r"quite|rather|potentially|arguably)\b", re.I
)

# Narrower list for CORRECTION. Pure adverbial hedges only — words that
# modify a claim without carrying the sentence's grammar. Deleting one
# just tightens the claim: "It is somewhat unclear" -> "It is unclear."
# "Perhaps this works" -> "This works."
#
# might/could/would/should are EXCLUDED even though they're hedges for
# measurement purposes. They're modal auxiliary verbs — the verb the
# sentence's grammar depends on ("This might work" — delete "might" and
# there is no verb left: "This work"). Removing one correctly means
# promoting the main verb to carry tense/agreement on its own ("might
# work" -> "works", "could be" -> "is"), which is a real grammatical
# rewrite, not a deletion. That's out of scope for a regex-level fixer.
# These stay flagged rather than auto-corrected until there's a
# POS-tagged version (see spaCy note in the module docstring below).
_SAFE_TO_DELETE_HEDGES = re.compile(
    r"\b(perhaps|possibly|maybe|somewhat|quite|rather|potentially|arguably)\b[ ]?",
    re.I
)
_MODAL_HEDGES = re.compile(r"\b(might|could)\b", re.I)

# Absolute/superlative claims a hedge must never be attached to — lifted
# directly from the correction-prompt instruction in prompts.py so both
# the LLM path (if kept as fallback) and this deterministic path enforce
# the identical rule rather than two versions drifting apart.
_ABSOLUTE_CLAIM = re.compile(
    r"\b(unmatched|the best|guaranteed|always|never|only|"
    r"the only|nothing beats|nothing else)\b", re.I
)

# "which" and "while" deliberately excluded from the safe splitters.
# "which" is usually the relative-pronoun SUBJECT of the second clause
# ("...the region, which surprised everyone..." — "which" = "that fact").
# Cut it and the second clause loses its subject: "Surprised everyone..."
# "while" is ambiguous between a time-relation and a contrast-relation
# reading, and picking wrong changes what the sentence means. and/but/
# so/because are safe: each already starts a grammatically complete
# clause with its own subject and verb, so splitting there just makes
# two sentences out of what was already two clauses.
_COORD_SPLIT = re.compile(r",\s+(and|but|so|because)\s+", re.I)


def _clean_after_delete(s: str) -> str:
    """Shared cleanup after removing a word from inside a sentence:
    collapse double spaces, drop a now-orphaned leading/trailing comma,
    drop " ," left when the deleted word had a comma right after it,
    re-capitalise if the first word was consumed."""
    s = re.sub(r"\s*,\s*,", ",", s)          # "X, , Y" -> "X, Y"
    s = re.sub(r"^\s*,\s*", "", s)            # leading orphan comma
    s = re.sub(r"\s*,\s*(?=[.!?]|$)", "", s)  # trailing orphan comma
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s:
        s = s[0].upper() + s[1:]
    return s


def _fix_hedge_density(text: str, target: float, current: float) -> tuple[str, bool]:
    """
    Deterministic hedge correction, over-hedged direction only
    (current > target). Only removes the SAFE_TO_DELETE adverbial
    hedges (perhaps, possibly, maybe, somewhat, quite, rather,
    potentially, arguably) — deleting one of these just tightens a
    claim: "It is somewhat unclear" -> "It is unclear."

    "might"/"could" are measured as hedges (see _HEDGE_WORDS, used by
    the scorer) but are deliberately NOT auto-deleted here — they're
    modal auxiliaries the sentence's grammar depends on. If the text
    still measures over target after the safe deletions, that residual
    is real signal that a modal-verb rewrite is needed, not a bug in
    this function; the caller should still flag it rather than treat
    the dimension as resolved. See applied_fully in the return value.

    Returns (fixed_text, applied) where applied is True if at least one
    safe deletion was made — the caller re-scores afterwards to see the
    real resulting hedge_density rather than trusting this flag alone.
    """
    if current <= target:
        return text, False

    sentences = _extract_sentences(text)
    changed_any = False
    fixed_sentences = []
    for s in sentences:
        if _SAFE_TO_DELETE_HEDGES.search(s):
            new_s = _SAFE_TO_DELETE_HEDGES.sub("", s)
            new_s = _clean_after_delete(new_s)
            fixed_sentences.append(new_s)
            changed_any = True
        else:
            fixed_sentences.append(s)

    return " ".join(fixed_sentences), changed_any


def _fix_sentence_length_sd(text: str, target: float, current: float) -> tuple[str, bool]:
    """
    Deterministic sentence-rhythm correction.

    Only handles the too-uniform case (current < target, not enough
    variance) by splitting the single longest sentence at a coordinating
    boundary (", and" / ", but" / ", so" / ", because" / ", which" /
    ", while"). That's mechanical: it's a real clause boundary already
    present in the writer's own sentence, not an invented one, so meaning
    survives.

    Too-varied (current > target) is NOT auto-applied. Merging two short
    sentences into one needs a connector choice (and/but/so/because) that
    changes the logical relationship between them — that's a judgement
    call, not a mechanical split, so it's left flagged rather than guessed.
    """
    if current >= target:
        return text, False

    sentences = _extract_sentences(text)
    if len(sentences) < 2:
        return text, False

    longest_idx = max(range(len(sentences)), key=lambda i: len(sentences[i].split()))
    longest = sentences[longest_idx]

    match = _COORD_SPLIT.search(longest)
    if not match:
        return text, False

    split_point = match.start()
    connector = match.group(1)
    first_half = longest[:split_point].strip()
    if not first_half.endswith((".", "!", "?")):
        first_half += "."

    # and/but/so/because all already start a full clause with its own
    # subject+verb (that's what made them safe to split on at all — see
    # the _COORD_SPLIT comment) so the second half stands alone as a
    # sentence once its connector is capitalised. "So"/"Because" read
    # naturally as sentence-openers; "And"/"But" are stylistically fine
    # too and match the writer's own coordinator choice rather than
    # inventing a different one.
    second_half = longest[match.end():].strip()
    second_half = connector.capitalize() + " " + second_half
    if not second_half.endswith((".", "!", "?")):
        second_half += "."

    sentences[longest_idx:longest_idx + 1] = [first_half, second_half]
    return " ".join(sentences), True



# ------------------------------------------------------------------
# Ownership (first_person_ratio) — under-owned direction only.
# ------------------------------------------------------------------
#
# Same asymmetry as hedging: only ONE direction is mechanical enough to
# auto-apply. Converting impersonal framing the writer used for their
# OWN claim into first person is a fixed template swap — the claim
# itself doesn't change, only who's speaking it becomes explicit.
#
# The forbidden direction (per prompts.py's existing correction-prompt
# comment) is reassigning credit for someone ELSE's point — "your point"
# becoming "my point". That's not a voice fix, it's a factual error.
# So this fixer refuses to touch any sentence carrying an attribution
# marker for another party, full stop, rather than trying to be clever
# about which ones are safe.
_IMPERSONAL_OPENER = re.compile(
    r"^(it is worth noting that|it should be noted that|it is worth noting|"
    r"there is a case that|it seems that|it appears that)\s+",
    re.I
)
_OTHER_ATTRIBUTION = re.compile(
    r"\b(your|their|his|her|someone else|according to|as \w+ (?:said|noted|put it))\b",
    re.I
)
_HAS_QUOTE = re.compile(r"[\"\u2018\u2019\u201c\u201d]")

# Bound how much any single pass rewrites, mirroring the existing LLM
# correction prompt's own instruction ("1-2 EXISTING suggestions") —
# a hard-coded fixer shouldn't be less conservative than the prompt it's
# replacing.
_MAX_CONVERSIONS_PER_PASS = 2


def _fix_first_person_ratio(text: str, target: float, current: float,
                             input_has_opinion_content: bool) -> tuple[str, bool]:
    """
    Deterministic ownership correction, under-owned direction only
    (current < target). Converts up to two sentences from a fixed set
    of impersonal openers ("It is worth noting that...", "It seems
    that...") into first person ("I think..."). The claim inside the
    sentence is untouched — only the impersonal frame around it is
    swapped for a first-person one already implied by the writer's own
    baseline.

    Refuses outright — regardless of target/current — if:
      - input_has_opinion_content is False (mirrors the existing gate
        in build_correction_prompt(): nothing of the writer's own to
        convert, so don't fabricate ownership that wasn't there), or
      - the sentence contains an attribution marker for another party
        (your/their/his/her/according to/as X said) or a quotation —
        those are someone else's point, converting them is a credit
        error, not a style fix, so this function will never touch them.
    """
    if current >= target or not input_has_opinion_content:
        return text, False

    sentences = _extract_sentences(text)
    converted = 0
    fixed_sentences = []
    for s in sentences:
        if (converted < _MAX_CONVERSIONS_PER_PASS
                and _IMPERSONAL_OPENER.match(s)
                and not _OTHER_ATTRIBUTION.search(s)
                and not _HAS_QUOTE.search(s)):
            new_s = _IMPERSONAL_OPENER.sub("", s)
            new_s = "I think " + new_s[0].lower() + new_s[1:] if new_s else new_s
            fixed_sentences.append(new_s)
            converted += 1
        else:
            fixed_sentences.append(s)

    return " ".join(fixed_sentences), converted > 0


# ------------------------------------------------------------------
# Directness (directive_ratio) — under-directive direction only.
# ------------------------------------------------------------------
#
# Same shape again: strip a polite/modal wrapper off a request that's
# ALREADY an instruction underneath ("Could you fix this?" -> "Fix
# this."). The action isn't invented — it's the same action, minus the
# politeness wrapper. Mechanical because nothing about WHAT to do is
# being decided by this function, only whether to keep the "please".
#
# The forbidden case (also already documented, in build_correction_
# prompt's docstring): fabricating a new call-to-action that wasn't in
# the input at all. This fixer can't do that structurally — it only
# ever strips a wrapper off text that was already there. If stripping
# the wrapper doesn't leave a recognised imperative verb underneath
# (reusing voice_engine's own _IMPERATIVE_VERBS list), it declines
# rather than guess.
_POLITE_WRAPPER = re.compile(
    r"^(please\s+|could you (?:please\s+)?|can you (?:please\s+)?|"
    r"would you (?:please\s+)?|you (?:should|could|might want to|may want to)\s+)",
    re.I
)


def _fix_directive_ratio(text: str, target: float, current: float,
                          input_has_directive_content: bool) -> tuple[str, bool]:
    """
    Deterministic directness correction, under-directive direction only
    (current < target). Strips a fixed set of polite/modal wrappers off
    up to two sentences, revealing the imperative already underneath —
    "Could you check this?" -> "Check this." Declines per-sentence if
    the text remaining after stripping doesn't start with a recognised
    imperative verb (voice_engine._imperative_pattern), which stops it
    from ever turning a non-request sentence into a broken fragment.

    Refuses outright if input_has_directive_content is False, mirroring
    the same gate build_correction_prompt() already uses — nothing
    actionable in the input to convert, so don't invent a directive.
    """
    if current >= target or not input_has_directive_content:
        return text, False

    sentences = _extract_sentences(text)
    converted = 0
    fixed_sentences = []
    for s in sentences:
        stripped = _POLITE_WRAPPER.sub("", s).strip().rstrip("?")
        candidate = (stripped[0].upper() + stripped[1:]) if stripped else stripped
        if (converted < _MAX_CONVERSIONS_PER_PASS
                and stripped != s.rstrip("?").strip()   # wrapper actually matched
                and _imperative_pattern.match(candidate)):
            if not candidate.endswith((".", "!", "?")):
                candidate += "."
            fixed_sentences.append(candidate)
            converted += 1
        else:
            fixed_sentences.append(s)

    return " ".join(fixed_sentences), converted > 0


# ------------------------------------------------------------------
# Modal-hedge removal (might/could) — the residual left after
# _fix_hedge_density's adverbial-only pass.
# ------------------------------------------------------------------
#
# No established library does "strip modal, promote verb" as a general
# tool — checked. It's normally hand-rolled precisely because subject-
# verb agreement needs to know the subject's person/number, which a
# regex only has narrow, safe visibility into. So this stays deliberately
# narrow: only fires when the subject immediately before the modal is a
# closed-set pronoun (I/we/you/they/he/she/it/this/that) — never a noun
# phrase, where agreement (singular/plural, collective nouns like "the
# team") gets genuinely ambiguous without a real parser.
#
# Negated modals ("might not", "could not") are skipped outright — "It
# might not work" and "It doesn't work" are different claims (possibility
# of failure vs. certainty of failure), so promoting through a negation
# is a meaning change, not a style fix. Left flagged, same as noun-phrase
# subjects.
_PRONOUN_SUBJECT_BE = re.compile(
    r"\b(I|we|you|they|he|she|it|this|that)\s+(might|could)\s+(?!not\b)be\b",
    re.I
)
_PRONOUN_SUBJECT_VERB = re.compile(
    r"\b(I|we|you|they|he|she|it|this|that)\s+(might|could)\s+(?!not\b)(?!be\b)([a-zA-Z]+)\b",
    re.I
)
_THIRD_SINGULAR = {"he", "she", "it", "this", "that"}
_IRREGULAR_VERBS = {"have": ("have", "has")}  # go/do fall out correctly
                                                # from the regular -es rule
                                                # below (go->goes, do->does)
                                                # so only "have" needs a
                                                # manual exception.


def _promote_verb(base_verb: str, subject_lower: str) -> str:
    """Third-person-singular conjugation for the narrow pronoun set
    above only. Not a general English conjugator — deliberately."""
    is_singular = subject_lower in _THIRD_SINGULAR
    lower = base_verb.lower()
    if lower == "be":
        return "is" if is_singular else "are"
    if lower in _IRREGULAR_VERBS:
        plain, singular = _IRREGULAR_VERBS[lower]
        return singular if is_singular else plain
    if not is_singular:
        return base_verb
    if re.search(r"(s|sh|ch|x|z|o)$", base_verb, re.I):
        return base_verb + "es"
    if re.search(r"[^aeiou]y$", base_verb, re.I):
        return base_verb[:-1] + "ies"
    return base_verb + "s"


def _fix_modal_hedge(text: str, target: float, current: float) -> tuple[str, bool]:
    """
    Deterministic modal-hedge correction, over-hedged direction only
    (current > target) — same direction and same caller contract as
    _fix_hedge_density(), meant to run as a second pass on whatever that
    function left behind (it deliberately skips might/could).

    Only converts "<pronoun> (might|could) [be] <verb>" where the
    subject is one of a closed set of pronouns. Declines on: negated
    modals ("might not"), noun-phrase subjects ("the team could..."),
    and any verb it doesn't have a confident conjugation rule for.
    Bounded to _MAX_CONVERSIONS_PER_PASS per call, same as the other
    under/over correction fixers, so one pass never rewrites a whole
    paragraph at once.
    """
    if current <= target:
        return text, False

    converted = 0
    result = text

    def _replace_be(m: re.Match) -> str:
        # "be" case: only promote the modal+be into is/are. Everything
        # that follows (the complement — "useful", "the answer", any
        # length noun phrase) is OUTSIDE the match and untouched, so
        # there's no risk of mis-capturing a multi-word complement —
        # unlike the earlier version of this function, which tried to
        # grab and reconjugate the first following word and broke on
        # anything longer than one word.
        nonlocal converted
        if converted >= _MAX_CONVERSIONS_PER_PASS:
            return m.group(0)
        subject, _modal = m.groups()
        promoted = _promote_verb("be", subject.lower())
        converted += 1
        return f"{subject} {promoted}"

    def _replace_verb(m: re.Match) -> str:
        nonlocal converted
        if converted >= _MAX_CONVERSIONS_PER_PASS:
            return m.group(0)
        subject, _modal, verb = m.groups()
        promoted = _promote_verb(verb, subject.lower())
        converted += 1
        return f"{subject} {promoted}"

    result = _PRONOUN_SUBJECT_BE.sub(_replace_be, result)
    result = _PRONOUN_SUBJECT_VERB.sub(_replace_verb, result)
    return result, converted > 0

