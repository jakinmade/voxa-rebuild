"""
Voxa — Deterministic Output Guardrail (canonical, packages/ ecosystem)

Single source of truth for the FastAPI/packages side of the codebase.
Every caller in voxa-rendering and voxa-api imports from here — there is
no second copy anywhere under packages/.

Why this file exists: an audit (August 2026) found FOUR independent
copies of this guardrail sweep across the repo, at four different levels
of completeness:

  - app.py (root, live Streamlit product)        — 12 of 12 steps, verified
  - voxa_rendering/cleaner.py                     — 2 of 12 steps
  - voxa_api/recalibrate.py                       — ~4 of 12 steps, stale
                                                      (14-entry construction
                                                      list vs the current 46)
  - voxa_api/rewrite.py                           — 0 of 12 steps, raw
                                                      LLM output returned
                                                      untouched

Any consumer hitting the FastAPI layer instead of the Streamlit app was
getting materially worse output with zero warning, because nothing
verified it. This file, plus updated callers in voxa_rendering and
voxa_api, closes that gap for the packages/ ecosystem.

DELIBERATE, DOCUMENTED DUPLICATION — read before editing either side:
Root-level app.py/prompts.py/voice_engine.py (the live Railway product)
do NOT depend on packages/ at all — zero `from voxa_` imports anywhere
in app.py, confirmed by direct check. Converting the live deployment to
depend on packages/ is a separate, higher-risk decision (new install
dependency, requirements.txt change, deployment retest) that was
explicitly scoped OUT of this fix to avoid risking a working live
product for an internal-only benefit. See project history, August 2026,
"Option A vs Option B" decision.

The result: this file is a faithful, verbatim port of prompts.py's
_regex_sweep (and its full dependency closure) and voice_engine.py's
score_ai_tells, as they stood at time of porting. It is the canonical
copy for packages/, but it is NOT automatically kept in sync with root.
If you fix a bug in one, mirror it in the other, or the four-way drift
this file was built to close will silently become a two-way drift
instead. Grep both files for "DELIBERATE, DOCUMENTED DUPLICATION" to
find the matching note on the other side.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------
# Register classification — shared by score_ai_tells and the analytical
# construction pass inside sweep(). Ported from voice_engine.py.
# ---------------------------------------------------------------------

_AI_TELL_PHRASES = re.compile(
    r"\b(it is (important|worth|essential|crucial|critical|key) to (note|recognise|recognize|understand)|"
    r"in (today's|the current|our) (landscape|world|environment|era)|"
    r"it goes without saying|needless to say|with that (said|in mind)|"
    r"as (we|you) (know|can see|may know)|"
    r"this (underscores|highlights|demonstrates|illustrates|showcases)|"
    r"leverag(e|ing)|synerg(y|ies)|holistic(ally)?|paradigm|robust(ly)?|"
    r"cutting.edge|game.changing|transformative|groundbreaking|"
    r"furthermore|moreover|nevertheless|notwithstanding|"
    r"in conclusion|to summarise|to summarize|in summary|"
    r"moving forward|circle back|touch base|pain points|"
    r"seamless(ly)?|delve into|tapestry|testament to|boasts|elevate|"
    r"unlock the potential|game.changer|unparalleled|paramount|"
    # Mirrors voice_engine.py's fix, 18 Aug 2026 — narrowed from
    # "curious (whether|if|how)" to "curious whether" only. "Curious
    # if you got a chance to run it" is ordinary, extremely common
    # human email phrasing; confirmed live against a real render where
    # a person's own preserved original wording ("curious if") got
    # flagged as an AI tell despite matching their unedited input. The
    # tell is the fabricated check-in construction, not the word
    # "curious" paired with any conjunction. This file previously had
    # the broader, pre-fix version — exactly the two-way drift this
    # module's own docstring warns against; mirrored here now.
    r"curious whether|does (this|that) (land|resonate)|"
    r"(lands|resonates?) (for|with) you)\b",
    re.I
)

_ANALYTICAL_TELL_PHRASES = re.compile(
    r"\b(drift(s|ed|ing)?|"
    # Negative lookbehinds mirror voice_engine.py's three rounds of
    # false-positive fixes on "surface" as a genuine noun ("the
    # agent's surface", "its surface", "the surface is legible",
    # "Agent Surface" as a coined compound term) rather than the
    # AI-essay habit of pressing the noun into service as a verb
    # ("issues surface", "concerns surfaced") this pattern exists to
    # catch. This file previously had none of these exclusions —
    # exactly the two-way drift this module's own docstring warns
    # against; mirrored here now, in full, not partially.
    r"(?<!'s )(?<!its )(?<!the )(?<!this )(?<!that )(?<!any )(?<!our )"
    r"(?<!her )(?<!his )(?<!each )(?<!a )(?<!an )(?<!no )(?<!Agent )"
    r"surfac(e|es|ed|ing)(?!\s+area)|land(s|ed|ing)? on|"
    r"unpack(s|ed|ing)?|gestur(e|es|ed|ing) (at|toward|towards)|"
    r"sit(s)? with|push back (on|against)|"
    r"worth noting|to be fair|on reflection|"
    r"the (real|deeper) (question|issue|tension) (is|here)|"
    r"i suspect|i would push back|closer to .* than to)\b",
    re.I
)

_FRAGMENT_EMPHASIS_PATTERN = re.compile(
    r"[.!?]\s+(Not\s+\w+|Different\s+\w+|No\s+\w+(?:\s+\w+){0,3})[.!?]",
    re.I
)

_SPACED_HYPHEN_DASH_PATTERN = re.compile(r"(?<=\S)\s-\s(?=\S)")

# Sentence extraction + ungrammatical-fragment detection, mirrored
# from voice_engine.py (27 Aug 2026 fix — DELIBERATE, DOCUMENTED
# DUPLICATION, see this file's header). A declarative sentence whose
# first word is a bare auxiliary/copula verb ("Is exactly what
# CLEARANCE is built to catch.") has no subject of its own — a real
# render produced exactly this by splitting at an appositive comma
# instead of a coordinating-conjunction one. Excluded from flagging:
# a bare-auxiliary opener ending in '?' ("Is this the right call?")
# is ordinary, grammatical English, not a severed fragment.
_DOTTED_ABBREV = re.compile(r'\b(?:[A-Za-z]\.){2,}')
_WORD_ABBREV = re.compile(
    r'\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Rev|Hon|vs|etc|approx|dept|no|vol|pp|'
    r'Ltd|Inc|Co|Corp|Ave|Blvd|Rd)\.',
    re.I
)


def _protect_abbreviations(text: str) -> str:
    """Swaps periods inside known abbreviations for a placeholder so
    they don't get read as sentence boundaries. Ported verbatim from
    voice_engine.py."""
    text = _DOTTED_ABBREV.sub(lambda m: m.group(0).replace('.', '\u0000'), text)
    text = _WORD_ABBREV.sub(lambda m: m.group(0).replace('.', '\u0000'), text)
    return text


def _extract_sentences(text: str) -> list[str]:
    """Split into sentences. Returns non-empty sentences only. Ported
    verbatim from voice_engine.py."""
    paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    normalised = ' '.join(
        p if p.endswith(('.', '!', '?', ':')) else p + '.'
        for p in paragraphs
    )
    protected = _protect_abbreviations(normalised)
    sentences = re.split(r"(?<=[.!?])\s+", protected.strip())
    sentences = [s.replace('\u0000', '.') for s in sentences]
    return [s.strip() for s in sentences if s.strip() and len(s.split()) >= 2]


_BARE_AUX_SENTENCE_OPENER = re.compile(
    r"^(?:Is|Are|Was|Were|Am|Has|Have|Had|Do|Does|Did|Will|Would|Could|"
    r"Should|Can|Must|Might|May)\b"
)


def _detect_sentence_fragments(text: str) -> list[str]:
    """Flags sentences that open on a bare auxiliary/copula verb and
    end in '.' or '!'. Ported verbatim from voice_engine.py."""
    return [
        s for s in _extract_sentences(text)
        if s.endswith((".", "!")) and _BARE_AUX_SENTENCE_OPENER.match(s)
    ]


# Plausibility shields — first-person lexical-verb hedges that attribute a
# claim to the writer's own judgement rather than stating it as fact:
# "I think", "I see it as", "in my view". Documented category, not an ad
# hoc list — Prince, Frader & Bosk (1982) coined "plausibility shield" for
# exactly this; Hyland's hedging taxonomy (1998) groups the same verbs
# under "lexical verb hedges" alongside modals and epistemic adverbs.
_PLAUSIBILITY_SHIELD_DROP = re.compile(
    r"^(I think|I believe|I would argue|I would say|I'd say|My view is|"
    r"My take is|My sense is|It seems to me)\b,?\s*(that\s+)?",
    re.IGNORECASE,
)
_PLAUSIBILITY_SHIELD_MIDSENTENCE = re.compile(
    r"(,\s*)?\b(in my view|in my opinion|as I see it|as I view it)\b"
    r"(\s*,\s*|\s+|(?=[.!?]|$))",
    re.IGNORECASE,
)
_PLAUSIBILITY_SHIELD_REPLACE = re.compile(
    r"^(I see it as|I see this as|I view it as|I view this as|I take it as)\b\s*",
    re.IGNORECASE,
)
_PLAUSIBILITY_SHIELD_PHRASES = re.compile(
    r"\b(I think that|I believe that|I would argue that|I would say that|"
    r"I'd say that|my view is that|my take is that|my sense is that|"
    r"it seems to me that|in my view|in my opinion|as I see it|as I view it|"
    r"I see it as|I see this as|I view it as|I view this as|I take it as)\b",
    re.IGNORECASE,
)


def _classify_register(text: str) -> str:
    """
    Buckets input text as 'corporate', 'analytical', or 'mixed' so
    score_ai_tells and the rewrite sweep know which tell-vocabulary
    to apply. Heuristic, not ML. Ported verbatim from voice_engine.py.
    """
    if not text or not text.strip():
        return "mixed"

    corporate_hits = len(_AI_TELL_PHRASES.findall(text))
    analytical_hits = len(_ANALYTICAL_TELL_PHRASES.findall(text))
    analytical_hits += len(_FRAGMENT_EMPHASIS_PATTERN.findall(text))

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    word_counts = [len(s.split()) for s in sentences] if sentences else []
    if len(word_counts) >= 3:
        mean = sum(word_counts) / len(word_counts)
        variance = sum((c - mean) ** 2 for c in word_counts) / len(word_counts)
        stdev = variance ** 0.5
    else:
        stdev = 0.0

    structural_leans_analytical = stdev >= 6.0

    if corporate_hits == 0 and analytical_hits == 0:
        return "mixed" if structural_leans_analytical else "corporate"
    if analytical_hits > corporate_hits:
        return "analytical"
    if corporate_hits > analytical_hits:
        return "corporate"
    return "mixed"


def score_ai_tells(text: str, original_input_text: str = "") -> dict:
    """
    Measured verification that the output doesn't read as AI-written —
    run AFTER sweep(), not instead of it. Confirms the guardrail actually
    worked rather than trusting that it did.

    original_input_text: mirrors voice_engine.py's exemption fix, 18
    Aug 2026 — added AFTER this file's initial port, and previously
    absent here entirely, meaning every flagged phrase was checked
    with no way to distinguish "the model fabricated this" from "this
    is the person's own genuine writing", exactly the two-way drift
    this module's own docstring warns against. Any matched phrase that
    appears verbatim (case-insensitive) in original_input_text is
    excluded from flagging. Default "" exempts nothing, so any caller
    not yet passing this argument keeps identical behaviour to before
    this parameter existed. Em dash / spaced-hyphen checks are never
    exempted this way — those enforce house style regardless of the
    person's own usual habits, not an AI-detection heuristic. See
    voice_engine.py's score_ai_tells for the full rationale (the
    "curious whether"/"i suspect"/"i would push back" false-positive
    class this was built to close).
    """
    original_lower = original_input_text.lower()

    def _matches_excluding_genuine(pattern: "re.Pattern") -> list:
        kept = []
        for m in pattern.finditer(text):
            if m.group(0).lower() in original_lower:
                continue
            kept.append(m.group(1) if m.lastindex else m.group(0))
        return kept

    em_dash_hits = len(re.findall(r"[\u2012\u2013\u2014\u2015]", text))
    spaced_hyphen_hits = len(_SPACED_HYPHEN_DASH_PATTERN.findall(text))
    phrase_hits = _matches_excluding_genuine(_AI_TELL_PHRASES)
    shield_hits = _matches_excluding_genuine(_PLAUSIBILITY_SHIELD_PHRASES)
    fragment_hits = _detect_sentence_fragments(text)

    register = _classify_register(text)
    analytical_hits = []
    if register in ("analytical", "mixed"):
        analytical_hits = _matches_excluding_genuine(_ANALYTICAL_TELL_PHRASES)
        analytical_hits += _matches_excluding_genuine(_FRAGMENT_EMPHASIS_PATTERN)

    all_hits = phrase_hits + analytical_hits + shield_hits

    flagged = []
    if em_dash_hits:
        flagged.append(f"{em_dash_hits} em dash(es) survived the sweep")
    if spaced_hyphen_hits:
        flagged.append(
            f"{spaced_hyphen_hits} spaced hyphen(s) used as a dash substitute "
            f"(the sweep converts em dashes to ' - ' — that's still the tell)"
        )
    if fragment_hits:
        flagged.append(
            f"{len(fragment_hits)} sentence fragment(s) — sentence starts on a "
            f"bare auxiliary verb with no subject of its own, a sign it was "
            f"severed from the sentence before it: {'; '.join(fragment_hits[:3])}"
        )
    # unique_phrases computed once here, not re-derived from `flagged`
    # below — mirrors voice_engine.py's flagged_phrases field, 18 Aug
    # 2026, added the same session this parity gap was found.
    unique_phrases = sorted(set(p if isinstance(p, str) else p[0] for p in all_hits)) if all_hits else []
    if unique_phrases:
        flagged.append(f"AI-typical phrasing found: {', '.join(unique_phrases[:5])}")

    clean = (
        em_dash_hits == 0 and spaced_hyphen_hits == 0
        and len(all_hits) == 0 and len(fragment_hits) == 0
    )

    return {
        "clean": clean,
        "em_dash_count": em_dash_hits,
        "spaced_hyphen_count": spaced_hyphen_hits,
        "phrase_hit_count": len(all_hits),
        "fragment_count": len(fragment_hits),
        "flagged_fragments": fragment_hits,
        "flagged": flagged,
        "flagged_phrases": unique_phrases,
        "register": register,
    }


def _strip_plausibility_shields(text: str) -> str:
    """
    Removes first-person plausibility shields per sentence, not just at
    the start of the text. Ported verbatim from prompts.py.
    """
    parts = re.split(r'(?<=[.!?])(\s+)', text)

    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if not sentence:
            continue

        fixed = _PLAUSIBILITY_SHIELD_DROP.sub('', sentence)

        def _midsentence_repl(m):
            return '' if m.start() == 0 else ' '
        fixed = _PLAUSIBILITY_SHIELD_MIDSENTENCE.sub(_midsentence_repl, fixed)

        def _replace_repl(m):
            return 'It is '
        fixed = _PLAUSIBILITY_SHIELD_REPLACE.sub(_replace_repl, fixed)

        if fixed != sentence:
            fixed = re.sub(r'  +', ' ', fixed).strip()
            fixed = re.sub(r'\s+([,.!?])', r'\1', fixed)
        if fixed and fixed != sentence:
            fixed = fixed[0].upper() + fixed[1:]
        parts[i] = fixed

    return ''.join(parts)


# ---------------------------------------------------------------------
# Dash splitting — ported verbatim from prompts.py.
# ---------------------------------------------------------------------

_DASH_VARIANTS_PATTERN = re.compile(
    r"&#8212;|&#8211;|[\u2010\u2012\u2013\u2014\u2015—–‒]"
)
_DASH_DEPENDENT_STARTERS = {
    "and", "but", "or", "which", "who", "whose", "that", "because",
    "since", "while", "although", "though", "yet", "so", "if", "when",
    "where", "as", "unless", "until",
}


def _split_dashes_deterministic(text: str) -> str:
    matches = list(_DASH_VARIANTS_PATTERN.finditer(text))
    if not matches:
        return text

    result = []
    last_end = 0
    capitalize_next = False

    for m in matches:
        start, end = m.start(), m.end()
        segment = text[last_end:start].rstrip()
        if capitalize_next and segment:
            segment = segment[0].upper() + segment[1:]
        result.append(segment)
        capitalize_next = False

        global_before = text[:start].rstrip()
        prev_boundary = max(
            global_before.rfind("."), global_before.rfind("!"), global_before.rfind("?")
        )
        clause_before = global_before[prev_boundary + 1:].strip()

        global_after = text[end:].lstrip()
        next_boundary_match = re.search(r"[.!?]", global_after)
        clause_after = global_after[:next_boundary_match.start()] if next_boundary_match else global_after

        words_before = clause_before.split()
        words_after = clause_after.split()
        first_word_after = words_after[0].lower().strip(",\"'") if words_after else ""

        independent = (
            len(words_before) >= 4
            and len(words_after) >= 4
            and first_word_after not in _DASH_DEPENDENT_STARTERS
        )

        if independent:
            result.append(". ")
            capitalize_next = True
        else:
            result.append(", ")
        last_end = end

    tail = text[last_end:]
    if capitalize_next:
        tail = tail.lstrip()
        if tail:
            tail = tail[0].upper() + tail[1:]
    result.append(tail)

    output = "".join(result)
    output = re.sub(r" +", " ", output)
    output = re.sub(r" ([,.!?])", r"\1", output)
    output = re.sub(r",\s*,", ",", output)
    return output


# ---------------------------------------------------------------------
# Absolute-claim hedge stripping — ported verbatim from prompts.py.
# ---------------------------------------------------------------------

_ABSOLUTE_CLAIM_PATTERN = re.compile(
    r"\b(unmatched|unparalleled|unrivalled|unrivaled|incomparable|second to none|"
    r"without equal|beyond compare|guaranteed|the best|the greatest|"
    r"nothing (?:compares|rivals)|hard to match|difficult to match|"
    r"always|never|only)\b",
    re.I,
)
_HEDGE_WORD_PATTERN = re.compile(
    r"\b(might|could|perhaps|possibly|maybe|potentially|somewhat|quite|rather)\b",
    re.I,
)
_TRAILING_QUALIFIER_PATTERN = re.compile(
    r",\s*(?:though|although)\b(?:(?!\.|!|\?).)*?\b"
    r"(?:might|could|perhaps|possibly|maybe|potentially|somewhat|quite|rather)\b[^.!?]*",
    re.I,
)


def _strip_hedges_from_absolute_claims(text: str) -> str:
    """
    A hedge word landing in the same sentence as an absolute/superlative
    claim doesn't soften tone, it changes the claim. Business-rule
    guardrail, not a prompt instruction. Ported verbatim from prompts.py.
    """
    parts = re.split(r'(?<=[.!?])(\s+)', text)

    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if not sentence or not _ABSOLUTE_CLAIM_PATTERN.search(sentence):
            continue

        fixed = sentence
        fixed = _TRAILING_QUALIFIER_PATTERN.sub('.', fixed)
        fixed = re.sub(r'\.{2,}', '.', fixed)

        if _HEDGE_WORD_PATTERN.search(fixed):
            fixed = re.sub(
                r'\b(perhaps|possibly|maybe|potentially|somewhat|quite|rather)\s+',
                '', fixed, flags=re.I,
            )
            fixed = re.sub(r'\bmight be\b', 'is', fixed, flags=re.I)
            fixed = re.sub(r'\bcould be\b', 'is', fixed, flags=re.I)
            fixed = re.sub(r'\bmight have\b', 'has', fixed, flags=re.I)
            fixed = re.sub(r'\bcould have\b', 'has', fixed, flags=re.I)

            def _modal_verb_repl(m):
                verb = m.group(1)
                if verb.lower() in ('be', 'have'):
                    return m.group(0)
                if _looks_plural_subject(fixed[:m.start()]):
                    return verb
                return _conjugate_verb(m)

            fixed = re.sub(
                r'\b(?:might|could)\s+(\w+)\b',
                _modal_verb_repl,
                fixed, flags=re.I,
            )

        fixed = re.sub(r'\s{2,}', ' ', fixed)
        fixed = re.sub(r'\s+([,.!?])', r'\1', fixed)
        parts[i] = fixed

    return ''.join(parts)


_PLURAL_SUBJECT_MARKERS = {
    "they", "we", "you", "these", "those", "some", "many", "several",
    "both", "few", "others",
}
_RELATIVE_PRONOUNS = {"that", "which", "who"}


def _looks_plural_subject(preceding_text: str) -> bool:
    """Cheap plural-subject signal for modal conjugation. Ported verbatim."""
    words = preceding_text.strip().split()
    if not words:
        return False
    last = words[-1].lower().strip(",.;:\"'")
    if last in _RELATIVE_PRONOUNS and len(words) >= 2:
        last = words[-2].lower().strip(",.;:\"'")
    if last in _PLURAL_SUBJECT_MARKERS:
        return True
    if last.endswith("s") and not last.endswith(("ss", "us", "is", "os")):
        return True
    return False


def _conjugate_verb(m):
    verb = m.group(1)
    if re.search(r'(s|x|z|ch|sh)$', verb, re.I):
        return verb + 'es'
    if re.search(r'[^aeiou]y$', verb, re.I):
        return verb[:-1] + 'ies'
    return verb + 's'


# ---------------------------------------------------------------------
# Canonical entry point — replaces cleaner.py's clean_render_output,
# recalibrate.py's local _regex_sweep, and rewrite.py's total absence
# of any cleanup step.
# ---------------------------------------------------------------------

def sweep(text: str, keep_contractions: bool = False) -> str:
    """
    Deterministic guardrail sweep — the single canonical implementation
    for the packages/ ecosystem (voxa-rendering, voxa-api). Ported
    verbatim from prompts.py's _regex_sweep as it stood at time of
    porting (August 2026). No API call. No LLM involvement. Code
    enforces every rule below; same input always produces same output.

    1. Em dashes — split into two sentences or joined with a comma
    2. Contractions expanded — only if keep_contractions is False
    3. Claude literary closers stripped from paragraph endings
    4. Plausibility shields stripped per-sentence, then Claude default
       constructions replaced
    5. Repeated words
    6. Double spaces
    7. Hedges stripped from sentences carrying an absolute/superlative
       claim
    8. Tricolon fragment lists collapsed
    9. Editorial additions stripped
    10. Recapitalise sentence starts
    11. (repeated) Absolute-claim hedge strip as a final pass
    """
    if not text:
        return text

    # 1. Em dashes
    text = _split_dashes_deterministic(text)
    text = re.sub(r"[\u2012\u2013\u2014\u2015]\s*", ", ", text)
    text = re.sub(r'  +', ' ', text)

    # 2. Contractions
    if not keep_contractions:
        contractions = [
            ("aren't", "are not"), ("isn't", "is not"), ("wasn't", "was not"),
            ("weren't", "were not"), ("didn't", "did not"), ("doesn't", "does not"),
            ("don't", "do not"), ("haven't", "have not"), ("hasn't", "has not"),
            ("hadn't", "had not"), ("won't", "will not"), ("wouldn't", "would not"),
            ("couldn't", "could not"), ("shouldn't", "should not"), ("can't", "cannot"),
            ("it's", "it is"), ("that's", "that is"), ("there's", "there is"),
            ("they're", "they are"), ("they've", "they have"), ("they'd", "they would"),
            ("I'm", "I am"), ("I've", "I have"), ("I'd", "I would"), ("I'll", "I will"),
            ("we're", "we are"), ("we've", "we have"), ("we'd", "we would"),
            ("you're", "you are"), ("you've", "you have"), ("you'd", "you would"),
            ("he's", "he is"), ("she's", "she is"), ("who's", "who is"),
            ("what's", "what is"), ("where's", "where is"),
        ]
        for contraction, full in contractions:
            pattern = re.compile(r'\b' + re.escape(contraction) + r'\b', re.IGNORECASE)

            def _repl(m, f=full):
                return f[0].upper() + f[1:] if m.group(0)[0].isupper() else f
            text = pattern.sub(_repl, text)

    # 3. Claude literary closers
    paragraphs = text.strip().split('\n\n')
    if paragraphs:
        last_para = paragraphs[-1].strip()
        last_sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', last_para) if s.strip()]
        if len(last_sents) >= 3:
            abstract_signals = re.compile(
                r'\b(exists|remains|persists|continues|endures|defines|determines|'
                r'until they|until it|between wanting|between knowing|between having|'
                r'more than it|who they|what they|blueprint|the gap|the distance|'
                r'the difference|the question|promises more|delivers on|'
                r'comes with time|does not\.?$|perhaps not|time will tell|'
                r'only time|remains to be seen|that clarity|'
                r'could take (longer|time|months)|might take|takes time|'
                r'than people expect|than expected|some way off|'
                r'whether.*genuinely|still some way|coming or not)\b', re.IGNORECASE
            )
            perhaps_couplet = re.compile(r'Perhaps [^.]+\. Perhaps [^.]+\.?', re.IGNORECASE)
            if perhaps_couplet.search(last_para):
                m = perhaps_couplet.search(last_para)
                if m:
                    stripped_para = last_para[:m.start()].strip()
                    if stripped_para:
                        paragraphs[-1] = stripped_para
                        text = '\n\n'.join(paragraphs)
            stripped = list(last_sents)
            while len(stripped) > 1 and abstract_signals.search(stripped[-1]) and len(stripped[-1].split()) <= 18:
                stripped.pop()
            if len(stripped) < len(last_sents):
                paragraphs[-1] = ' '.join(stripped)
                text = '\n\n'.join(paragraphs)

    # 4. Plausibility shields, then Claude default constructions
    text = _strip_plausibility_shields(text)
    text = re.sub(r'I am genuinely uncertain (about|whether)', 'I am not sure', text, flags=re.IGNORECASE)
    text = re.sub(r'I remain (genuinely |deeply )?uncertain', 'I am not sure', text, flags=re.IGNORECASE)

    claude_constructions = [
        (r'\bWhat stood out most was\b', 'What stood out'),
        (r'\bWhat stood out was\b', 'What stood out'),
        (r'\bWhat emerged most was\b', 'What emerged'),
        (r'\bWhat emerged was\b', 'What emerged'),
        (r'\bIt was proof that\b', 'It showed that'),
        (r'\bIt served as a reminder\b', 'It was a reminder'),
        (r'\bThis serves as\b', 'This is'),
        (r'\bIt is worth noting that\b', 'Note that'),
        (r'\bIt is important to note that\b', 'Note that'),
        (r'\bIt is (important|worth|essential|crucial|critical|key) to (note|recognise|recognize|understand)\b', 'Note that'),
        (r"\bIn (today's|the current|our) (landscape|world|environment|era)\b", 'Now'),
        (r'\bIt goes without saying\b', 'Obviously'),
        (r'\bNeedless to say\b', 'Obviously'),
        (r'\bWith that (said|in mind)\b', 'So'),
        (r'\bAs (we|you) (know|can see|may know)\b,?\s*', ''),
        (r'\bThis (underscores|highlights|demonstrates|illustrates|showcases)\b', 'This shows'),
        (r'\bMoving forward\b', 'Going forward'),
        (r'\bLeverage\b', 'Use'),
        (r'\bLeveraging\b', 'Using'),
        (r'\bCircle back\b', 'Return to'),
        (r'\bTouch base\b', 'Speak'),
        (r'\bPain points\b', 'Problems'),
        (r'\bRobust(ly)?\b', 'Strong'),
        (r'\bSeamless(ly)?\b', 'Smooth'),
        (r'\bHolistic(ally)?\b', 'Full'),
        (r'\bSynerg(y|ies)\b', 'Benefit'),
        (r'\bEcosystem\b', 'Environment'),
        (r'\bFurthermore\b', 'Also'),
        (r'\bMoreover\b', 'Also'),
        (r'\bNevertheless\b', 'Still'),
        (r'\bNotwithstanding\b', 'Still'),
        (r'\bIn conclusion\b', 'So'),
        (r'\bTo (summarise|summarize)\b', 'In short'),
        (r'\bIn summary\b', 'In short'),
        (r'\bParadigm\b', 'Model'),
        (r'\bCutting.edge\b', 'New'),
        (r'\bGame.chang(ing|er)\b', 'Major'),
        (r'\bTransformative\b', 'Major'),
        (r'\bGroundbreaking\b', 'New'),
        (r'\bDelve into\b', 'Look at'),
        (r'\bTapestry\b', 'Mix'),
        (r'\bTestament to\b', 'Proof of'),
        (r'\bBoasts\b', 'Has'),
        (r'\bElevate\b', 'Improve'),
        (r'\bUnlock the potential\b', 'Make the most'),
        (r'\bUnparalleled\b', 'Rare'),
        (r'\bParamount\b', 'Vital'),
        # Mirrored from root prompts.py, 20 Aug 2026 — researched
        # addition (Grammarly/GPTZero/Pangram AI-tell compilations
        # cross-referenced), deliberately excluding generic
        # professional vocabulary (harness, illuminate, bolster,
        # facilitate, streamline, refine, differentiate, revolutionize,
        # innovative, typically, generally/broadly speaking) that
        # VOICOVA's actual target customer would plausibly use in
        # their own genuine voice. See prompts.py for the full
        # rationale — this copy must mirror it exactly per this
        # module's own DELIBERATE, DOCUMENTED DUPLICATION contract.
        (r'\bThat being said\b', 'Still'),
        (r'\bAt its core\b', 'Fundamentally'),
        (r'\bTo put it simply\b', 'In short'),
        (r'\bSimply put\b', 'In short'),
        (r'\bShed light on\b', 'Explain'),
        (r'\bFrom a broader perspective\b', 'More broadly'),
        (r'\bA key takeaway is\b', 'The main point is'),
        (r'\bPivotal\b', 'Key'),
        (r'\bRealm\b', 'Area'),
    ]

    def _replace_case_matched(pattern: str, replacement: str, text: str) -> str:
        """Mirrors root prompts.py's _sub_excluding_genuine case-
        matching fix (20 Aug 2026), minus the original_input_text
        exemption — this module has never had that parameter, so this
        only ports the case-matching half of the fix, not the
        exemption behaviour, to avoid introducing a feature this
        module doesn't otherwise support. Without this, a mid-sentence
        lowercase hit was replaced with the hardcoded-capitalised
        replacement string regardless of position (\"a robust
        solution\" -> \"a Strong solution\"), confirmed as a real,
        pre-existing bug affecting every entry in both lists here."""
        def _repl(m: "re.Match") -> str:
            replaced = m.expand(replacement)
            matched = m.group(0)
            if replaced and matched and matched[0].isalpha():
                if matched[0].isupper():
                    replaced = replaced[0].upper() + replaced[1:]
                else:
                    replaced = replaced[0].lower() + replaced[1:]
            return replaced
        return re.sub(pattern, _repl, text, flags=re.IGNORECASE)

    for pattern, replacement in claude_constructions:
        text = _replace_case_matched(pattern, replacement, text)

    if _classify_register(text) in ("analytical", "mixed"):
        analytical_constructions = [
            (r'\bdrifts?\b', 'changes'),
            (r'\bdrifted\b', 'changed'),
            (r'\bdrifting\b', 'changing'),
            (r'\blands? on\b', 'settles on'),
            (r'\blanded on\b', 'settled on'),
            (r'\bunpacks?\b', 'looks at'),
            (r'\bunpacked\b', 'looked at'),
            (r'\bworth noting\b', 'notable'),
            (r'\bto be fair\b', 'fairly'),
            (r'\bgestures? (at|toward|towards)\b', 'points to'),
            (r'\bgestured (at|toward|towards)\b', 'pointed to'),
            (r'\bgesturing (at|toward|towards)\b', 'pointing to'),
            (r'\bsits? with\b', 'stays with'),
            (r'\bsat with\b', 'stayed with'),
            (r'\bsitting with\b', 'staying with'),
            (r'\bpush back on\b', 'disagree with'),
            (r'\bpush back against\b', 'disagree with'),
            (r'\bpushed back on\b', 'disagreed with'),
            (r'\bpushed back against\b', 'disagreed with'),
            (r'\bon reflection\b', 'thinking it over'),
            (r'\bthe (real|deeper) (question|issue|tension) (is|here)\b', 'what matters'),
            (r'\bI suspect\b', 'I think'),
            (r'\bI would push back\b', 'I disagree'),
            (r'\bcloser to ([\w\s,]+?) than to ([\w\s,]+?)([.,;])', r'more like \1 than \2\3'),
        ]
        for pattern, replacement in analytical_constructions:
            text = _replace_case_matched(pattern, replacement, text)

    # 5. Repeated words
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)

    # 6. Double spaces
    text = re.sub(r'  +', ' ', text)

    # 7. Tricolon fragment lists
    tricolon = re.compile(
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\.\s+"
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\.\s+"
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\."
    )

    def _collapse_tricolon(m):
        return f"{m.group(1)} {m.group(2)}."
    text = tricolon.sub(_collapse_tricolon, text)

    # 8. Editorial additions
    editorial = [
        (r"That framing might be too simple[^.]*\.", ""),
        (r"That (observation|framing|assessment|reading) (might|may|could) be[^.]*\.", ""),
        (r"Whether that (is|was) (fair|accurate|right|correct)[^.]*\.", ""),
    ]
    for pattern, replacement in editorial:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"  +", " ", text).strip()

    # 9. Recapitalise sentence starts
    text = re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)

    # 10. Strip hedges from sentences carrying an absolute/superlative claim
    text = _strip_hedges_from_absolute_claims(text)

    # 11. Orphan/doubled punctuation cleanup — mirrors the fix in
    # prompts.py's sweep (root, live Railway product) - see that
    # function's docstring for the full rationale. Confirmed live:
    # ",." shipping from an upstream LLM grammar stage inserting a
    # name before a salutation comma. ",." -> "." only, never the
    # reverse (".," is a correct abbreviation pattern in "e.g.," /
    # "i.e.," / "etc.," and collapsing it would corrupt those).
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r",\s*,+", ",", text)
    # Redundant terminal punctuation trailing a closing quote — mirrors
    # the fix in prompts.py's sweep (root, live Railway product), added
    # 16 Aug 2026 after a live render shipped 'did not.".' See that
    # function's inline comment for the full rationale.
    text = re.sub(r'([.!?])"[.!?]', r'\1"', text)
    text = re.sub(r" ([,.!?])", r"\1", text)

    return text
