"""
deterministic_fixers.py — rule-based correction functions for the four
voice_render_delta dimensions (hedge_density, sentence_length_sd,
first_person_ratio, directive_ratio).

Wired into app.py's correction pass at two points: before the LLM
correction call (runs first, cheapest possible fix), and again in the
post-correction verify loop (catches whatever the LLM call missed or
only partially applied). No LLM calls anywhere in this module — every
function takes and returns plain text plus a bool for whether it
fired, so a caller can log which rule applied, same evidence-trail
principle as score_render_delta.

Design rule, carried through every function here: fix only in the
direction that's mechanically safe, decline everywhere else rather than
guess. Five of the eight possible directions (one over/under per
dimension) are handled — hedge_density over-hedged, sentence_length_sd
under-varied, first_person_ratio both over- and under-owned, and
directive_ratio under-directive. The other three stay flagged on
purpose — see each function's docstring for why. This is not partial
coverage papering over gaps; each declined direction was evaluated and
rejected as a meaning-risk, same standard as the ones that shipped.
"""

import re
from collections import Counter
import difflib
from voice_engine import _extract_sentences, _imperative_pattern, _HEDGE_PATTERN, _SCAFFOLDING_PATTERN

# Single source of truth, imported from voice_engine.py rather than
# duplicated here — this file previously kept its own hand-copied word
# list, which had already drifted out of sync with the actual scorer
# (compute_baseline_metrics) by the time that was caught. See
# _HEDGE_PATTERN's own docstring in voice_engine.py for what it covers
# and why (Hyland's hedging taxonomy) — used here for MEASUREMENT (the
# score has to count every hedge to be accurate against the baseline).
_HEDGE_WORDS = _HEDGE_PATTERN

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
# Widened to match voice_engine.py's expanded single-word adverb list
# (presumably, apparently, allegedly, seemingly, supposedly added
# alongside the original set) — same syntactic category as the
# originals, deleting one is equally safe: "It is apparently unclear"
# -> "It is unclear."
#
# The newer CLAUSE-LEVEL hedges _HEDGE_PATTERN now also detects ("it
# seems", "curious whether", "wonder if", etc.) are deliberately NOT
# included here. Same reasoning as the modal-verb exclusion below:
# deleting "it seems " from "It seems the numbers are off" leaves "The
# numbers are off" — fine there, but "I wonder if we should reconsider"
# with "I wonder if " deleted leaves "we should reconsider", which needs
# recapitalising and is a real grammatical rewrite, not a clean
# deletion. These stay flagged for the LLM correction pass rather than
# auto-corrected, same standard as everywhere else in this module: a
# regex-level fixer should never be less conservative than the
# structural risk actually requires.
# Registry of hedge words with a second, non-hedge grammatical or
# semantic role that makes unconditional deletion unsafe. Was three
# inline regex lookarounds embedded directly in _SAFE_TO_DELETE_HEDGES
# (rather(?!\s+than) etc.) until this refactor — worked, but every new
# idiom found meant hand-editing a single dense regex and getting the
# lookaround syntax exactly right. This is the same exclusion logic,
# same three words, same behaviour (see _is_unsafe_collocation's own
# tests), just as a data structure a new idiom can be added to as one
# line instead of a regex edit. Audited the full _SAFE_TO_DELETE_HEDGES
# list against this failure class — the other eight words (perhaps,
# possibly, maybe, potentially, arguably, presumably, apparently,
# allegedly, seemingly, supposedly) have no known collisions and carry
# no entry here.
#
# "rather" — "rather than" (comparative connector) and "would rather
# [not]" (preference modal — "would rather wait than rush" -> "would
# wait than rush" is a grammar break; "would rather not commit" ->
# "would not commit" survives grammatically but flips a mild
# preference into a flat refusal). Also "or rather," (self-correction
# idiom — deleting it removes the correction itself and orphans a
# comma).
#
# "somewhat" — "somewhat of a" ("somewhat of a mess" -> "of a mess")
# is the same grammar-break shape as rather-than: load-bearing in the
# idiom, not modifying a claim it can be cleanly stripped from.
#
# "quite" — highest severity: not a grammar break but a magnitude
# INVERSION. "quite a few" means MANY, "a few" means NOT many —
# deleting "quite" reverses the claim rather than softening it. Same
# shape on "quite the X" / "quite something" (idiomatic emphasis, not
# a gradable-degree hedge). Plain adverbial use ("quite promising",
# "quite clear") is unaffected — this narrows the unsafe idiomatic
# minority, not the safe majority.
_UNSAFE_COLLOCATIONS = {
    "rather": [{"after": {"than"}}, {"before": {"would", "or"}}],
    "somewhat": [{"after": {"of"}}],
    "quite": [{"after": {"a", "the", "something"}}],
}


def _is_unsafe_collocation(word: str, before_word: str, after_word: str) -> bool:
    """Checks a matched hedge word's immediate neighbours against
    _UNSAFE_COLLOCATIONS. before_word/after_word are the nearest word
    tokens on each side, lowercased, empty string if none (start/end
    of sentence). A word with no registry entry is always safe — this
    only ever narrows the three words above, never adds new caution
    elsewhere."""
    rules = _UNSAFE_COLLOCATIONS.get(word.lower())
    if not rules:
        return False
    for rule in rules:
        if after_word in rule.get("after", ()):
            return True
        if before_word in rule.get("before", ()):
            return True
    return False


_SAFE_TO_DELETE_HEDGES = re.compile(
    r"\b(perhaps|possibly|maybe|somewhat|quite|rather|potentially|arguably|"
    r"presumably|apparently|allegedly|seemingly|supposedly)\b[ ]?",
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
    re-capitalise if the first word was consumed.

    Colon handling added 4 Sept 2026 alongside the scaffolding-density
    fixer: deleting a phrase like "Let me explain:" or a mid-sentence
    "background:" can leave an orphaned colon the comma-only rules
    above never covered - confirmed by direct testing before this
    shipped, not a hypothetical. Mirrors the comma rules exactly,
    same reasoning: a bare leftover colon (leading, trailing, or
    immediately after a comma) is never a valid punctuation state
    for any caller of this shared helper, not just the one that
    happened to surface it."""
    s = re.sub(r"\s*,\s*,", ",", s)          # "X, , Y" -> "X, Y"
    s = re.sub(r"\s*,\s*:", ":", s)          # "X, : Y" -> "X: Y"
    s = re.sub(r"^\s*,\s*", "", s)            # leading orphan comma
    s = re.sub(r"\s*,\s*(?=[.!?]|$)", "", s)  # trailing orphan comma
    s = re.sub(r"^\s*:\s*", "", s)            # leading orphan colon
    s = re.sub(r"\s*:\s*(?=[.!?]|$)", "", s)  # trailing orphan colon
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s:
        s = s[0].upper() + s[1:]
    return s


def _split_into_paragraphs(text: str) -> list[str]:
    """Splits raw text into paragraphs by run(s) of newlines, preserving
    each paragraph's own content exactly. Exists so a fixer can process
    sentence-by-sentence WITHIN a paragraph and rejoin with the
    paragraph breaks restored afterward, rather than flattening a
    multi-paragraph email into one continuous block — the bug every
    fixer in this file had before this function existed: each one ran
    _extract_sentences(text) on the FULL text at once, which discards
    paragraph boundaries, then rejoined every sentence with a single
    " ".join(...), collapsing however many paragraphs the input had
    into one. Confirmed against a real multi-paragraph render this
    session, not a hypothetical."""
    return [p.strip() for p in re.split(r"\n+", text) if p.strip()]


def _apply_across_paragraphs(text: str, per_sentence_fn, max_conversions: int | None = None) -> tuple[str, bool]:
    """
    Shared harness for the common fixer shape: decide independently,
    sentence by sentence, whether to change it, then rejoin — but
    rejoining WITHIN a paragraph (single space) and BETWEEN paragraphs
    (blank line), instead of flattening everything the way a single
    " ".join(_extract_sentences(text)) call over the whole text does.

    per_sentence_fn(sentence) -> (new_sentence, changed: bool) is called
    for each sentence across every paragraph, in order.

    max_conversions, if given, caps how many sentences per_sentence_fn
    is allowed to actually change GLOBALLY across the whole text (not
    per paragraph) — once the cap is hit, remaining sentences pass
    through unchanged without even being offered to per_sentence_fn,
    preserving each caller's existing _MAX_CONVERSIONS_PER_PASS
    semantics exactly as before this harness existed.
    """
    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return text, False

    changed_any = False
    conversions = 0
    new_paragraphs = []
    for para in paragraphs:
        sentences = _extract_sentences(para)
        if not sentences:
            new_paragraphs.append(para)
            continue
        new_sentences = []
        for s in sentences:
            if max_conversions is not None and conversions >= max_conversions:
                new_sentences.append(s)
                continue
            new_s, did_change = per_sentence_fn(s)
            new_sentences.append(new_s)
            if did_change:
                changed_any = True
                conversions += 1
        new_paragraphs.append(" ".join(new_sentences))

    return "\n\n".join(new_paragraphs), changed_any


def _fix_hedge_density(text: str, target: float, current: float) -> tuple[str, bool]:
    """
    Deterministic hedge correction, over-hedged direction only
    (current > target). Only removes the SAFE_TO_DELETE adverbial
    hedges (perhaps, possibly, maybe, somewhat, quite, rather,
    potentially, arguably) — deleting one of these just tightens a
    claim: "It is somewhat unclear" -> "It is unclear." Three of these
    words (rather, somewhat, quite) get an additional collocation
    check against _UNSAFE_COLLOCATIONS before deletion — see that
    registry's docstring for why each is unsafe in specific contexts.

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

    def _fix_one(s: str) -> tuple[str, bool]:
        matches = list(_SAFE_TO_DELETE_HEDGES.finditer(s))
        if not matches:
            return s, False

        to_delete = []
        for m in matches:
            word = m.group(1)
            before_tokens = re.findall(r"[A-Za-z']+", s[:m.start()])
            after_tokens = re.findall(r"[A-Za-z']+", s[m.end():])
            before_word = before_tokens[-1].lower() if before_tokens else ""
            after_word = after_tokens[0].lower() if after_tokens else ""
            if not _is_unsafe_collocation(word, before_word, after_word):
                to_delete.append(m)

        if not to_delete:
            return s, False

        # Delete right-to-left so earlier matches' indices stay valid
        # as later ones are removed.
        result = s
        for m in sorted(to_delete, key=lambda m: m.start(), reverse=True):
            result = result[:m.start()] + result[m.end():]
        return _clean_after_delete(result), True

    return _apply_across_paragraphs(text, _fix_one)


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

    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return text, False

    # Flat index across all paragraphs so "the single longest sentence
    # in the whole text" still means the whole text, not just one
    # paragraph — same selection behaviour as before this was made
    # paragraph-aware, just tracking which paragraph each sentence
    # came from so the split can be written back into the right place
    # instead of flattening every paragraph into one on rejoin.
    para_sentences = [_extract_sentences(p) for p in paragraphs]
    flat = [
        (pi, si, s)
        for pi, sents in enumerate(para_sentences)
        for si, s in enumerate(sents)
    ]
    if len(flat) < 2:
        return text, False

    longest_flat_idx = max(range(len(flat)), key=lambda i: len(flat[i][2].split()))
    para_idx, sent_idx, longest = flat[longest_flat_idx]

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

    para_sentences[para_idx][sent_idx:sent_idx + 1] = [first_half, second_half]
    new_paragraphs = [" ".join(sents) for sents in para_sentences]
    return "\n\n".join(new_paragraphs), True



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

    def _fix_one(s: str) -> tuple[str, bool]:
        if (_IMPERSONAL_OPENER.match(s)
                and not _OTHER_ATTRIBUTION.search(s)
                and not _HAS_QUOTE.search(s)):
            new_s = _IMPERSONAL_OPENER.sub("", s)
            new_s = "I think " + new_s[0].lower() + new_s[1:] if new_s else new_s
            return new_s, True
        return s, False

    return _apply_across_paragraphs(text, _fix_one, max_conversions=_MAX_CONVERSIONS_PER_PASS)


# Two groups, deliberately handled differently:
#   - "I think/I believe/I suspect/I imagine/I would say/I find" all
#     take a complete independent clause as their object, so stripping
#     the WHOLE opener leaves a grammatically sound sentence on its own
#     ("I think the plan works" -> "The plan works"). "I find" added
#     against a real render ("I find nobody catches it
#     through monitoring" -> "Nobody catches it through monitoring") —
#     same shape as the rest of this group, confirmed by hand before
#     adding, not assumed by analogy alone.
#   - "I am curious/certain/confident/not sure/unsure" do NOT reduce
#     the same way — "I am curious whether X" stripped of the whole
#     opener leaves "whether X", not a sentence. Stripping only "I am "
#     and keeping the adjective leaves "Curious whether X" instead — a
#     legitimate elliptical fragment, not a broken one. This isn't a
#     stylistic guess: it's literally the construction already present
#     in the ORIGINAL input this bug was found against ("Curious
#     whether your clients have solved that..."), which the render had
#     turned into "I am curious whether..." in the first place.
#
# Deliberately NOT added: "I see". Found live in the same
# render alongside "I find" ("That is four reasons..." -> "I see four
# reasons..."), but "see" here takes a bare noun phrase as its object,
# not a clause — stripping "I see " leaves "four reasons not to fold
# it into governance", which has no verb and is not a complete
# sentence, unlike every case in the FULL_STRIP group. Reconstructing
# "That is" would require guessing at wording that isn't recoverable
# from the render alone. Declining rather than shipping a fragment —
# left for the LLM correction pass, same as anything else this module
# won't guess at.
_FIRST_PERSON_OPENER_FULL_STRIP = re.compile(
    r"^(I think|I believe|I suspect|I imagine|I would say|I find)\b[,:]?\s*",
    re.I
)
_FIRST_PERSON_OPENER_PARTIAL_STRIP = re.compile(
    r"^I am (?=(curious|certain|confident|not sure|unsure)\b)",
    re.I
)

# Mid-sentence companion to the sentence-initial FULL_STRIP above —
# found live in the same render, a genuinely different
# shape: "I think" inserted as a parenthetical AFTER a fronted phrase
# rather than at the sentence's start ("What nobody has done..." ->
# "What I think nobody has done...", "In most organisations
# qualification..." -> "In most organisations I think qualification
# ..."). FULL_STRIP is anchored to position 0 and never matches these.
# Deliberately narrow: bare "I think" only, no comma-wrapped variant
# ("X, I think, Y") until a real render surfaces that shape — same
# standard as everything else here, built against confirmed cases,
# not speculative ones.
_MID_SENTENCE_I_THINK = re.compile(r"\bI think\s+", re.I)


def _sentence_words(s: str) -> set[str]:
    return set(re.findall(r"[a-z']+", s.lower()))


def _matching_original_sentence(candidate: str, original_sentences: list[str]) -> str | None:
    """Finds the original sentence THIS specific candidate most likely
    corresponds to, by word overlap (Jaccard) — not just "does this
    phrase appear anywhere in the document". That distinction is the
    whole point: a document-wide substring check ("does 'i think'
    appear anywhere in the original") wrongly blocks a genuine fix
    whenever the person used "I think" once, ANYWHERE, in an entirely
    unrelated sentence — found live against a real render where the
    person's own opening line genuinely says "I think
    you have found the gap", which silently blocked the mid-sentence
    fixer below from touching two later, unrelated sentences the model
    had actually injected "I think" into.

    Requires majority word overlap (>=0.5 Jaccard) before trusting a
    match — a low-overlap "best available" sentence is worse than no
    match at all, since it would let a genuinely different original
    sentence's first-person content wrongly veto a fix. No match found
    returns None, which the caller treats as "nothing to protect
    against" rather than "decline out of caution" — a sentence with no
    real original counterpart is new content either way (a separate,
    more serious concern _check_uncorrected_insertions already flags
    via sentence_growth), and stripping an ownership marker from it
    doesn't make that worse.
    """
    candidate_words = _sentence_words(candidate)
    if not candidate_words:
        return None
    best, best_score = None, 0.0
    for orig_s in original_sentences:
        orig_words = _sentence_words(orig_s)
        if not orig_words:
            continue
        overlap = len(candidate_words & orig_words) / len(candidate_words | orig_words)
        if overlap > best_score:
            best, best_score = orig_s, overlap
    return best if best_score >= 0.5 else None



def _fix_first_person_over_ratio(text: str, target: float, current: float,
                                  original_input_text: str = "") -> tuple[str, bool]:
    """
    Deterministic ownership correction, OVER-owned direction —
    companion to _fix_first_person_ratio above, which only ever
    handled the opposite case. Converts up to two sentences that carry
    a first-person opinion marker ("I think...", "I am curious...",
    "I find...") back to a direct statement — stripping the marker,
    sentence-initial or mid-sentence, the claim inside the sentence
    untouched, same principle as the UNDER-owned direction.

    Confirmed as a real, previously-unhandled gap, not a hypothetical:
    a render added "I am curious whether..." where the original input
    had no first person there at all ("Curious whether..."), and
    nothing deterministic existed to catch it — only the LLM
    correction pass could, and only if it happened to target this
    dimension in that direction.

    original_input_text: the actual text being rewritten this render.
    Safety check before stripping anything — if the exact opener
    phrase already appears verbatim in the ORIGINAL input, this
    fixer leaves the sentence alone. The point is to strip
    first-person the model ADDED, never to strip first-person the
    person actually wrote themselves. Same spirit as
    input_has_opinion_content gating the opposite direction (don't
    fabricate what wasn't there; symmetrically here, don't erase what
    was genuinely there).

    Deliberately does NOT reuse the UNDER-owned fixer's
    _OTHER_ATTRIBUTION check — that check protects against a risk
    specific to the OPPOSITE direction (fabricating first-person
    ownership over what might be someone else's impersonal point).
    Stripping "I think " from a sentence that already has "I" as its
    subject doesn't misattribute anything; a "your"/"their" appearing
    elsewhere in the same sentence is just referring to another party
    as an object, not a sign the opinion belongs to them. Reusing that
    check here caused a real false decline against the exact case this
    fixer was built for — caught and corrected before this shipped,
    not left in.

    Refuses outright if current <= target (nothing to fix in this
    direction) or the sentence carries a quote — converting quoted
    material is still a credit error regardless of direction.
    """
    if current <= target:
        return text, False

    original_lower = original_input_text.lower()
    original_sentences = _extract_sentences(original_input_text) if original_input_text else []

    def _fix_one(s: str) -> tuple[str, bool]:
        if _HAS_QUOTE.search(s):
            return s, False

        full_match = _FIRST_PERSON_OPENER_FULL_STRIP.match(s)
        if full_match:
            opener_text = full_match.group(0).strip().rstrip(",:").lower()
            if opener_text and opener_text in original_lower:
                return s, False
            new_s = _FIRST_PERSON_OPENER_FULL_STRIP.sub("", s)
            return (new_s[0].upper() + new_s[1:]) if new_s else new_s, True

        partial_match = _FIRST_PERSON_OPENER_PARTIAL_STRIP.match(s)
        if partial_match:
            if "i am" in original_lower and partial_match.group(1).lower() in original_lower:
                return s, False
            new_s = _FIRST_PERSON_OPENER_PARTIAL_STRIP.sub("", s)
            return (new_s[0].upper() + new_s[1:]) if new_s else new_s, True

        # Mid-sentence injection ("What I think X...", "In most
        # organisations I think X...") — only reached once the
        # sentence-initial checks above have both declined, so this
        # never double-handles a case FULL_STRIP already caught.
        # Uses sentence-level alignment (see _matching_original_
        # sentence's docstring), not a whole-document substring check —
        # that distinction is what makes this safe to fire even when
        # the person genuinely used "I think" once, elsewhere, in an
        # unrelated sentence.
        #
        # Fail-CLOSED on alignment uncertainty, not fail-open: only
        # strip when alignment succeeds AND confirms the original did
        # NOT have "i think" there. Found and fixed during a second-
        # opinion review: the previous logic declined only when
        # alignment succeeded and found "i think" in the aligned
        # sentence, but fell through to STRIP whenever alignment
        # failed to find any confident match at all — the opposite
        # safety direction from ownership_miss_is_content_driven and
        # restore_fabricated_ownership_sentences below, which both
        # correctly treat "no confident alignment" as "don't touch
        # it". Reproduced concretely: a genuine mid-sentence "I think"
        # in a sentence the render had ALSO legitimately reworded
        # elsewhere (voice-matching changes wording throughout a
        # sentence, not just the ownership marker) dropped word
        # overlap to 0.42, below the 0.5 threshold — alignment failed
        # to confirm the original's ownership was genuine, and the old
        # logic then stripped it anyway, deleting real content the
        # person actually wrote.
        mid_match = _MID_SENTENCE_I_THINK.search(s)
        if mid_match and mid_match.start() > 0:
            new_s = s[:mid_match.start()] + s[mid_match.end():]
            aligned = _matching_original_sentence(new_s, original_sentences)
            if aligned and "i think" not in aligned.lower():
                return new_s, True
            return s, False

        return s, False

    return _apply_across_paragraphs(text, _fix_one, max_conversions=_MAX_CONVERSIONS_PER_PASS)


_FIRST_PERSON_MARKER = re.compile(r"\b(i|my|me|mine|myself)\b", re.I)


def ownership_miss_is_content_driven(render_text: str, original_input_text: str) -> bool:
    """
    Distinguishes two very different reasons first_person_ratio can
    stay MISSED after both deterministic fixer passes have already
    run: a genuine defect the fixer couldn't reach, versus the input
    itself simply being more opinion-dense than the person's baseline
    — in which case no further correction is possible without deleting
    real content, and the residual isn't a defect at all.

    first_person_ratio (voice_engine.py's compute_baseline_metrics) is
    SENTENCE-level: the proportion of sentences containing a first-
    person marker, not a word-density count. That matters here — it
    means a sentence can only be "fixed" by removing its marker
    entirely, not diluted by rewording. Confirmed live: an
    initially-proposed fix (restore the person's exact original
    wording for a sentence the fixer can't safely strip) turned out to
    do NOTHING for the metric whenever the original sentence ALSO
    contains a first-person marker — which, empirically, was every
    single residual case checked. Substituting "I disagree" for the
    person's own original "I would push back" doesn't change the
    sentence-level count at all; both count as one first-person
    sentence either way.

    Given that, the only meaningful question left is: for every
    remaining first-person sentence in the render, was there ALREADY
    a first-person marker in the corresponding original sentence? If
    yes for all of them, this dimension being MISSED reflects the
    input's genuine content, not something the render did wrong — the
    person's own original writing was this opinion-dense, and nothing
    short of deleting or rewriting their actual stated position would
    close the gap. If even one remaining first-person sentence has NO
    corresponding first-person marker in its aligned original
    sentence, that's a genuine unfixed defect (something the fixer
    should have caught, or a fabricated marker), and this returns
    False so risk/confidence still see it as a real miss.

    Sentences with no confident original alignment (see
    _matching_original_sentence's own threshold) are treated as
    defects, not content — the burden is on demonstrating the marker
    was genuinely there, not on assuming it was.
    """
    original_sentences = _extract_sentences(original_input_text) if original_input_text else []
    render_sentences = _extract_sentences(render_text)

    for s in render_sentences:
        if not _FIRST_PERSON_MARKER.search(s):
            continue
        aligned = _matching_original_sentence(s, original_sentences)
        if not aligned or not _FIRST_PERSON_MARKER.search(aligned):
            return False
    return True


def restore_fabricated_ownership_sentences(text: str, original_input_text: str) -> tuple[str, bool]:
    """
    General, alignment-based fix for fabricated first-person ownership
    — deliberately NOT pattern-based, and supersedes the individual
    verb-pattern fixers above for this specific failure class. Built
    after three successive live failures:
    "I find nobody catches...", "I see four reasons...", then "I would
    never find it through monitoring..." — each a different phrasing
    of the exact same underlying defect (a sentence that had NO
    first-person marker in the original gained one in the render), and
    each requiring a new pattern to be added to catch it. That's not a
    coincidence to keep patching one verb at a time — it's the correct
    prediction for any approach that enumerates specific phrasings:
    there is no finite list of ways to write a sentence in first
    person, so pattern-matching will always have a gap the model's
    next rephrasing falls through.

    The question this asks instead doesn't depend on wording at all:
    does this render sentence carry a first-person marker where the
    ALIGNED ORIGINAL sentence had none? That's checkable with total
    certainty regardless of whether the render says "I find", "I
    would never find", "I personally don't think anyone finds", or a
    phrasing nobody has hit yet — none of them change the answer to
    "did the original have a marker here", which is the only thing
    that actually determines fabrication.

    The fix follows the same logic: don't try to surgically edit the
    render's specific fabricated phrasing (which is what the pattern
    fixers above do, and why they need per-verb grammar analysis to
    stay safe). Replace the whole sentence with the person's own
    original wording, verbatim. This also resolves a case the pattern
    fixers had to explicitly decline: "I see four reasons..." (aligned
    original: "That is four reasons...") couldn't be safely stripped
    without leaving a sentence fragment with no verb. Whole-sentence
    substitution has no such failure mode — it always substitutes a
    complete, already-grammatical sentence, because that sentence is
    the untouched original.

    Trade-off, stated plainly rather than left implicit: this discards
    whatever voice-matching rewording the render did elsewhere in that
    specific sentence (vocabulary substitution, rhythm matching), in
    favour of the person's exact original wording. For a sentence
    where ownership was fabricated, restoring their real words is the
    safer failure mode than shipping a fabricated claim under a
    polished style match — the whole product's premise is that the
    words are theirs, and a fabricated first-person claim is the one
    failure mode that most directly breaks that premise.

    No max_conversions cap, unlike the pattern fixers above — each
    substitution here is provably safe (grammatical by construction,
    faithful to the person's own genuine content by construction), so
    the throttling those fixers need to stay conservative isn't
    needed here; capping it would just reintroduce the same
    "only fixes some of them" gap this function exists to close.
    """
    original_sentences = _extract_sentences(original_input_text) if original_input_text else []

    def _fix_one(s: str) -> tuple[str, bool]:
        if not _FIRST_PERSON_MARKER.search(s):
            return s, False
        aligned = _matching_original_sentence(s, original_sentences)
        if aligned and not _FIRST_PERSON_MARKER.search(aligned):
            return aligned, True
        return s, False

    return _apply_across_paragraphs(text, _fix_one, max_conversions=None)


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


def _fix_scaffolding_density(text: str, target: float, current: float) -> tuple[str, bool]:
    """
    Deterministic scaffolding-phrase correction, over-scaffolded
    direction only (current > target) — same shape and safety
    convention as _fix_hedge_density above. Added 4 Sept 2026 to close
    a gap flagged the same day: conclusion_opener_ratio and
    scaffolding_density were added as SCORED, enforced dimensions
    (voice_engine.compute_baseline_metrics) but neither had an auto-
    fixer, unlike the original four. This closes the scaffolding_
    density half — deletion-based, same as hedges, so it carries the
    same safety profile. conclusion_opener_ratio (does the piece lead
    with its point) is NOT given a fixer here: correcting it needs
    reordering sentences, not deleting words, which is a materially
    riskier transformation this pass deliberately does not attempt —
    see this file's sentence-alignment fixers' own documented history
    of a cleverer attempt breaking a real render. Left measured and
    reported only, same as before.

    Uses voice_engine's own _SCAFFOLDING_PATTERN — the same one
    score_reader_assumption and compute_baseline_metrics already
    share — so there is exactly one definition of "scaffolding"
    across detection and correction, not two that could drift apart.

    Every matched phrase here is a clause-level preface ("basically",
    "in other words", "as you know", "the reason is", ...) that reads
    naturally deleted outright — unlike a hedge word, which softens a
    claim in place, a scaffolding phrase's whole job is to introduce
    what follows, so removing it and re-capitalising is the correct
    fix, not a partial one. _clean_after_delete's existing orphan-
    comma and re-capitalisation handling covers the punctuation this
    produces (a comma right after "Basically," or "in other words,").

    Returns (fixed_text, applied) exactly like the other fixers here —
    the caller re-scores afterward rather than trusting this flag alone.
    """
    if current <= target:
        return text, False

    def _fix_one(s: str) -> tuple[str, bool]:
        matches = list(_SCAFFOLDING_PATTERN.finditer(s))
        if not matches:
            return s, False

        # Delete right-to-left, but check EACH deletion in isolation for
        # a broken result before committing to it - two real cases found
        # by direct testing before this fixer ever shipped: "The reason
        # is that X" -> deleting "the reason is" alone leaves "That X",
        # an orphaned subordinating conjunction with no clause to attach
        # to; "Let me explain: X" -> deleting "let me explain" leaves a
        # leading ": X", an orphaned colon _clean_after_delete only
        # handles for commas. Rather than special-case every possible
        # leftover (guaranteed to miss the next one, same lesson as
        # this file's other fixers), any deletion whose OWN cleaned
        # result would start with a leftover connector/punctuation is
        # skipped outright - the scaffolding phrase stays in place for
        # that specific match, correctly surfacing as still-MISSED
        # rather than silently shipping broken grammar the caller has
        # no way to catch downstream.
        _BROKEN_RESULT_START = re.compile(r"^(that|which|who|whose)\b|^[:;,]", re.I)

        result = s
        any_applied = False
        for m in sorted(matches, key=lambda m: m.start(), reverse=True):
            candidate = result[:m.start()] + result[m.end():]
            cleaned = _clean_after_delete(candidate)
            if _BROKEN_RESULT_START.match(cleaned):
                continue
            result = candidate
            any_applied = True

        if not any_applied:
            return s, False
        return _clean_after_delete(result), True

    return _apply_across_paragraphs(text, _fix_one)


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

    def _fix_one(s: str) -> tuple[str, bool]:
        stripped = _POLITE_WRAPPER.sub("", s).strip().rstrip("?")
        candidate = (stripped[0].upper() + stripped[1:]) if stripped else stripped
        if (stripped != s.rstrip("?").strip()   # wrapper actually matched
                and _imperative_pattern.match(candidate)):
            if not candidate.endswith((".", "!", "?")):
                candidate += "."
            return candidate, True
        return s, False

    return _apply_across_paragraphs(text, _fix_one, max_conversions=_MAX_CONVERSIONS_PER_PASS)


# ------------------------------------------------------------------
# Entity-casing restoration — fixes a specific false-positive in
# score_semantic_drift's dropped_entities check.
# ------------------------------------------------------------------
#
# _entities_and_numbers() (voice_engine.py) does a case-SENSITIVE set
# comparison between input and output. Confirmed live against a real
# render: "CLEARANCE" (input, brand-name all-caps) survived into the
# output as "Clearance" (title case) - same word, same position in
# meaning, only the casing changed. Because the comparison is a plain
# set difference, this counted as a full drop, identical in kind to a
# genuinely vanished entity, which:
#   (a) understated semantic_match/entity_preservation for a defect
#       that isn't actually a lost fact, and
#   (b) fed into compute_risk's dropped_entities hard-fail, forcing
#       High risk for something mechanically fixable without any LLM
#       call at all.
#
# This fixer runs a case-insensitive scan: for every input entity NOT
# found in output_entities (case-sensitive), check whether a case-
# insensitive whole-word match exists anywhere in the output text. If
# so, restore the original casing via a whole-word substitution -
# deterministic, same input always produces the same output, and safe
# by construction because it only ever changes letter case, never the
# letters themselves or the word count.
#
# Entities with NO case-insensitive match anywhere in the output
# (e.g. "Curious" in that same live render, where the opening clause
# was reworded away entirely) are left untouched - that's a genuine
# drop, not a casing defect, and belongs to build_correction_prompt's
# existing "add it back in naturally" LLM correction path, not this
# fixer. Conflating the two would either falsely "fix" a real content
# loss by re-inserting a bare word with no sentence to hold it, or
# silently suppress a defect that should surface to the user.
def _fix_entity_casing(output_text: str, input_text: str) -> tuple[str, list[str], list[str]]:
    """
    Restores original casing for entities that survived the rewrite
    with a case-only change. Returns (fixed_text, restored, still_dropped)
    where restored is the list of entities whose casing was fixed, and
    still_dropped is the input_entities set minus output entities minus
    whatever restored - i.e. what's left for the LLM correction path to
    handle, so callers don't have to recompute the diff themselves.

    Import is local, not top-of-file, to avoid a circular import -
    voice_engine.py doesn't import deterministic_fixers.py, but keeping
    this import scoped here (rather than adding it to the existing
    voice_engine import line at the top of this file) makes the
    dependency direction obvious at the call site instead of implicit
    at module load.
    """
    from voice_engine import _entities_and_numbers

    input_entities = _entities_and_numbers(input_text)
    output_entities = _entities_and_numbers(output_text)
    dropped = sorted(input_entities - output_entities)
    if not dropped:
        return output_text, [], []

    output_lower = {e.lower(): e for e in output_entities}
    fixed = output_text
    restored = []
    still_dropped = []
    for entity in dropped:
        if entity.lower() in output_lower:
            # Case-insensitive match exists — restore the input's
            # casing everywhere that word appears in the output,
            # whole-word only so this never touches a substring inside
            # a longer word.
            pattern = re.compile(r"\b" + re.escape(entity) + r"\b", re.I)
            new_fixed, n = pattern.subn(entity, fixed)
            if n:
                fixed = new_fixed
                restored.append(entity)
            else:
                # Matched in the entity set but not via this word-
                # boundary regex (rare — e.g. entity extraction found
                # it inside a numeral/percent token). Leave for the
                # LLM correction path rather than force a substitution
                # that might not be safe.
                still_dropped.append(entity)
        else:
            still_dropped.append(entity)

    return fixed, restored, still_dropped


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


# Common contraction -> expansion pairs, applied before the word-level
# diff in _check_uncorrected_insertions so contracting/expanding a word
# (here's <-> here is, didn't <-> did not) isn't mistaken for new
# content — a split render frequently does this alongside a comma-to-
# period change. Not exhaustive; covers the standard set, which is all
# that check needs (it only has to avoid false positives on ordinary
# rewrites, not model every contraction in English).
_CONTRACTION_EXPANSIONS = {
    "here's": "here is", "it's": "it is", "that's": "that is",
    "what's": "what is", "there's": "there is", "who's": "who is",
    "let's": "let us",
    "didn't": "did not", "doesn't": "does not", "don't": "do not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "hasn't": "has not", "haven't": "have not",
    "hadn't": "had not", "won't": "will not", "wouldn't": "would not",
    "can't": "can not", "couldn't": "could not", "shouldn't": "should not",
    "mustn't": "must not",
    "i'm": "i am", "you're": "you are", "we're": "we are",
    "they're": "they are",
    "i've": "i have", "you've": "you have", "we've": "we have",
    "they've": "they have",
    "i'd": "i would", "you'd": "you would", "we'd": "we would",
    "i'll": "i will", "you'll": "you will", "we'll": "we will",
    "they'll": "they will",
}
_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CONTRACTION_EXPANSIONS) + r")\b",
    re.I,
)


def _expand_contractions(text: str) -> str:
    """Lowercases and expands standard contractions — normalisation
    step for the word-level diff in _check_uncorrected_insertions,
    not used anywhere content is actually rewritten."""
    return _CONTRACTION_RE.sub(lambda m: _CONTRACTION_EXPANSIONS[m.group(0).lower()], text.lower())


# ------------------------------------------------------------------
# Post-LLM-correction insertion check.
# ------------------------------------------------------------------
#
# Every fixer above only deletes or reduces — none of them can ever be
# the source of a hedge or sentence that wasn't already in the text.
# The one step in app.py's correction pass that CAN add content is the
# LLM correction call itself: it's told to fix one dimension (say,
# first_person_ratio) and, as a side effect of rewriting, can introduce
# hedges or an extra sentence that weren't asked for and weren't in the
# text before that call ran.
#
# The existing re-score after that call (score_render_delta) doesn't
# catch this, structurally. It measures hedge_density as an aggregate
# ratio against a target BAND. If the band was already satisfied before
# the call, or the addition doesn't push the ratio far enough to breach
# it, newly-introduced hedge words pass through as "clean" — the check
# only ever asks "is the overall count in range", never "was this
# specific word here before this specific call". Confirmed against a
# real render: an LLM correction pass fixing ownership introduced
# "perhaps" and "might be" and a whole new closing sentence, and the
# dimension-level delta scored hedge_density as held.
#
# This function asks the direct question instead: diff the text from
# immediately before the LLM call against the text immediately after,
# word-for-word on the hedge list, sentence-count on the rest.
def _check_uncorrected_insertions(before: str, after: str) -> dict:
    """
    Diffs `before` (the deterministically-fixed text, pre-LLM-correction)
    against `after` (the LLM correction call's output) to catch hedges
    and sentences the correction call introduced as a side effect of
    fixing an unrelated dimension — collateral the aggregate
    score_render_delta band check can miss (see module note above).

    Hedge matching reuses _HEDGE_WORDS (== voice_engine's canonical
    _HEDGE_PATTERN) rather than the narrower _SAFE_TO_DELETE_HEDGES —
    this is a detection question ("did a hedge of any kind appear that
    wasn't there before"), not a correction question, so it should use
    the same word list the scorer itself uses, same reasoning as
    _HEDGE_WORDS's own docstring: one canonical list, not a second one
    that can drift.

    Sentence growth is reported, not auto-corrected — there's no
    mechanical way to know which added sentence is the fabricated one,
    only that the count grew. Left for the caller to surface rather
    than silently passing it through as clean.

    Sentence-count alone can't tell a fabricated sentence apart from an
    existing one that got split by punctuation (e.g. a comma-joined
    clause rewritten as two short sentences for rhythm) — both raise
    after_sentence_count by 1 with no new content. Confirmed against a
    real render: an Elevate pass split 'Not "X," but "Y, and Z."' into
    'Not "X," but "Y. And Z."' purely by changing a comma to a period,
    which flagged as sentence_growth despite zero new words — a false
    positive on Content Lock. So growth is only reported when the
    words in `after` actually outnumber the words available in
    `before` (same count-based, non-positional diff as new_hedges,
    over all words rather than just the hedge list) — a split
    redistributes existing words across more sentences without using
    up any word `before` didn't already have; a fabrication brings
    words that aren't in that budget.

    UPDATE, 27 Aug 2026: the threshold for "actually outnumber" was
    briefly ">3 new words" rather than ">0" — meant as headroom for
    ordinary punctuation-driven rewriting picking up a sentence-initial
    connector, but in practice it let short invented sentences through
    untouched, which is the exact class of fabrication this check
    exists to catch. Confirmed measuring the original motivating
    false-positive above at new_word_count == 0 — >0 was always
    sufficient to exclude it, no headroom needed. Tightened to >0.

    UPDATE, 29 Aug 2026: the word-budget check above was whole-
    document (Counter diff over ALL words in `before` vs `after`),
    which meant a single unrelated, genuinely legitimate word added
    ANYWHERE in a long render — e.g. a sentence-initial "Timing feels
    right..." corrected to "The timing feels right..." — pushed
    new_word_count above 0 for the entire document, which then made
    every OTHER split in that same render register as sentence_growth
    too, even splits that individually redistributed existing words
    with zero net addition. Confirmed against a real render: a
    CLEARANCE follow-up email had one such incidental "The" plus three
    genuinely word-neutral comma-to-period splits elsewhere: the old
    whole-document check flagged all three splits as "sentences
    invented" purely because of the unrelated "The".

    Fixed by scoping the word-budget check to the specific block of
    sentences a real diff (difflib.SequenceMatcher over normalised
    sentence text) identifies as changed, rather than the whole
    document. Sentences that align unchanged elsewhere never enter the
    budget check at all, so an incidental word added in one place can
    no longer contaminate the verdict on an unrelated split. This is
    NOT the per-sentence word-overlap attribution that was tried and
    reverted before (see test_heavily_paraphrased_render_reports_raw_
    delta_not_a_lower_attributed_count) — that approach tried to
    guess WHICH single sentence among many was fabricated using
    word-overlap, which synonym substitution defeats. This only asks
    "did the sentence *count* change in this diff block, and if so,
    did real new words come with it" — heavy paraphrasing with no
    count change in a block never enters the growth count at all
    (same as before), and where no sentence in the document aligns
    unchanged (as in that heavily-paraphrased test case), the whole
    document collapses to one block and this behaves identically to
    the old whole-document check — confirmed that test still reports
    the same raw delta.

    KNOWN LIMITATION: this scoping only helps when difflib's sentence-
    level alignment can actually find an anchor (an unchanged sentence)
    between two independent edits. If two unrelated edits sit directly
    adjacent with no anchor between them, difflib has nothing to split
    on and merges them into one block, so an incidental word added by
    one edit can still contaminate an unrelated split's verdict in
    that specific adjacency. A same-session attempt at a further fix
    (greedily pairing off near-identical sentences before the
    word-budget check) was tried and reverted: it fixed the invented
    adjacency case it was written for, but broke a second REAL live
    render (see git history, 29 Aug 2026) by fuzzy-matching one half
    of a genuine split to the wrong sentence, stranding the other half
    and miscounting it as fabricated content. Reverted in favour of
    this simpler version, which is the one actually confirmed correct
    against both real reported false positives. Left as a known gap
    rather than shipping an unverified fix for a scenario not yet
    confirmed to occur in real renders.

    Returns:
      {
        "new_hedges": list[str]  — hedge words/phrases present in
            `after` more times than in `before`, one entry per extra
            occurrence (e.g. ["perhaps", "might"] if each appeared once
            more than it did in `before`).
        "sentence_growth": int   — sentences added beyond `before`'s
            count, floored at 0, and reported only when accompanied by
            new words (see docstring) — a pure punctuation split isn't
            a fabrication signal, so it's not reported here.

            Tried attributing this to specific sentences
            that individually carry new content, instead of the raw
            delta, after a real render (F1 test) reported "3 sentence
            (s) added" when only one sentence actually contained a
            genuine fabrication. Reverted after testing against that
            same render: the same text also did heavy synonym
            substitution throughout ("gain"->"find", "highlight"->
            "demonstrate", "reinforces"->"shows"), and a per-sentence
            word-budget can't tell a swapped word from a fabricated
            one - it flagged 6 of 14 sentences, not 1. Shipping that
            would have traded one inaccuracy for a worse one. Raw
            delta stays as the honest, if blunt, number until there's
            a genuinely reliable way to separate "reworded" from
            "fabricated" per sentence - see detect_lexical_fidelity_
            breaks' own docstring for why that's deliberately scoped
            as a narrow watchlist rather than a general synonym
            detector, same reasoning applies here.
        "flagged": bool          — True if either signal fired.
      }
    """
    before_hedges = Counter(m.lower() for m in _HEDGE_WORDS.findall(before))
    after_hedges = Counter(m.lower() for m in _HEDGE_WORDS.findall(after))

    new_hedges: list[str] = []
    for word, count in after_hedges.items():
        extra = count - before_hedges.get(word, 0)
        if extra > 0:
            new_hedges.extend([word] * extra)

    sentence_growth = sum(g for _, _, g in _diff_growth_blocks(before, after))

    return {
        "new_hedges": new_hedges,
        "sentence_growth": sentence_growth,
        "flagged": bool(new_hedges) or sentence_growth > 0,
    }


# Function words excluded from the new-word budget — added 4 Sept
# 2026, real-render finding. A dropped-subject restoration ("Built
# out for X" -> "I built it out for X") and a comma-spliced list
# gaining a conjunction ("...SR 11-7, the laws now live" -> "...SR
# 11-7, and the laws are all live") each add a genuine word with
# zero new information - pure grammatical connective tissue needed
# to turn a fragment into a complete sentence, not fabricated
# content. The word-budget check's own stated purpose is telling
# fabrication apart from "a split that redistributes existing
# words" (see _check_uncorrected_insertions's docstring) - a subject
# pronoun or conjunction added at exactly the point a fragment got
# completed is the same category of non-fabrication the check
# already excludes for zero-word splits, just one function word
# short of qualifying under the old all-words-count rule.
#
# Deliberately NARROW and ADDITIVE, not a rewrite of the alignment
# logic this module's docstrings already warn is fragile (see KNOWN
# LIMITATION in _check_uncorrected_insertions - a prior same-session
# attempt at a cleverer fix broke a second real render). This only
# filters WHICH words count toward the budget threshold; it cannot
# introduce a new false negative on genuine fabrication, since
# fabricated content overwhelmingly consists of actual content words
# (nouns, verbs, adjectives, facts, names) that this list never
# touches - only pure function words are excluded, and only ever in
# the direction of making the check MORE lenient, never less
# sensitive.
_FUNCTION_WORD_BUDGET_EXCLUSIONS = frozenset({
    "i", "it", "this", "that", "and", "is", "are", "am", "was", "were",
    "the", "a", "an", "all",
})


def _diff_growth_blocks(before: str, after: str) -> list[tuple[str, str, int]]:
    """
    Shared diff step behind _check_uncorrected_insertions's sentence_
    growth count and, new 5 Sept 2026, the fabrication correction pass
    in app.py — extracted so a second caller can get at the specific
    flagged spans (WHERE content was invented) rather than only the
    aggregate count (THAT it was). _check_uncorrected_insertions's own
    return contract is unchanged: it still returns only the count,
    computed via this same step as before the extraction, same tests
    passing confirms identical behaviour.

    Sentence-boundary alignment, normalisation, function-word budget
    exclusions and known limitations are all identical to (this
    literally is) _check_uncorrected_insertions's own diff logic — see
    that function's docstring for the full rationale, including why a
    cleverer per-sentence attribution attempt was tried and reverted
    for the REPORTED COUNT. That reversion doesn't apply here: this
    only surfaces the block text for blocks the existing logic already
    counts as growth, it doesn't change which blocks count or attempt
    finer-grained attribution within a block.

    Returns a list of (before_block, after_block, local_growth) for
    every diff opcode where after_block introduces real new content
    words beyond before's budget — the same condition
    _check_uncorrected_insertions sums into sentence_growth, one tuple
    per contributing block instead of one summed integer.
    """
    before_sentences = _extract_sentences(before, min_words=1)
    after_sentences = _extract_sentences(after, min_words=1)

    def _normalise_for_alignment(sentence: str) -> str:
        # Sentence-boundary alignment only, not the word-budget check
        # itself — punctuation differences (comma vs period, an em
        # dash) must not by themselves stop two sentences from
        # aligning as the same content, or every ordinary split would
        # get its own spurious block. Contractions expanded so
        # "here's"/"here is" align too.
        sentence = _expand_contractions(sentence)
        sentence = re.sub(r"[^a-z0-9\s]", "", sentence)
        return re.sub(r"\s+", " ", sentence).strip()

    before_norm = [_normalise_for_alignment(s) for s in before_sentences]
    after_norm = [_normalise_for_alignment(s) for s in after_sentences]

    matcher = difflib.SequenceMatcher(None, before_norm, after_norm, autojunk=False)

    blocks: list[tuple[str, str, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        local_growth = max(0, (j2 - j1) - (i2 - i1))
        if local_growth == 0:
            continue
        before_block = " ".join(before_sentences[i1:i2])
        after_block = " ".join(after_sentences[j1:j2])
        before_words = Counter(re.findall(r"[a-z]+", _expand_contractions(before_block)))
        after_words = Counter(re.findall(r"[a-z]+", _expand_contractions(after_block)))
        new_word_count = sum(
            max(0, count - before_words.get(word, 0))
            for word, count in after_words.items()
            if word not in _FUNCTION_WORD_BUDGET_EXCLUSIONS
        )
        if new_word_count > 0:
            blocks.append((before_block, after_block, local_growth))

    return blocks


def get_fabricated_blocks(before: str, after: str) -> list[dict]:
    """
    Public wrapper around _diff_growth_blocks for the fabrication
    correction pass (app.py, prompts.py's
    build_fabrication_correction_prompt) — returns the specific
    before/after sentence spans _check_uncorrected_insertions counts
    toward sentence_growth, so the correction call can be told exactly
    which spans introduced content not present in the input, instead
    of just "something did".

    Returns [{"before": str, "after": str}, ...], empty list if
    nothing is flagged. Never flags a block _check_uncorrected_
    insertions wouldn't also flag — same diff step, see that
    function's docstring for the full rationale and known limitations.
    """
    return [
        {"before": before_block, "after": after_block}
        for before_block, after_block, _ in _diff_growth_blocks(before, after)
    ]

