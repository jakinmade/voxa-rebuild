"""
prompts.py — system prompt construction and text post-processing.

No Streamlit, no API client construction. This module builds strings
and cleans text; app.py is responsible for actually calling the API.

Ported from the original app.py monolith, with one addition new in this
rebuild: build_correction_prompt() extends the existing voice-correction
pass (proven logic, unchanged) to also target semantic drift — dropped
names, numbers, or facts — per the v4 spec's parallel voice+semantic
check. Previously this prompt was built inline inside the render screen;
here it's a function, so it's testable independent of Streamlit.
"""

import re

from voice_engine import (
    _score_thought_density,
    _pick_anchor_sentences,
    _extract_vocabulary_fingerprint,
    _format_vocabulary_fingerprint,
    _extract_function_patterns,
    _format_function_patterns,
    _classify_register,
    compute_baseline_metrics,
    _PLAUSIBILITY_SHIELD_DROP,
    _PLAUSIBILITY_SHIELD_MIDSENTENCE,
    _PLAUSIBILITY_SHIELD_REPLACE,
)


def _strip_plausibility_shields(text: str) -> str:
    """
    Removes first-person plausibility shields (Prince, Frader & Bosk 1982;
    Hyland's "lexical verb hedges", 1998) — "I think", "I see it as", "in
    my view" — wherever they fall in the text, not just at the very start.

    Replaces the old opener_hedge / opener_hedge2 / opener_hedge3 regexes,
    which anchored on (?m)^ — a LINE start, not a SENTENCE start. That only
    matched if the shield opened the entire text or sat right after a
    literal newline. A shield buried in sentence two or three of an
    ordinary paragraph (the common case — see John's Scott Kosch draft,
    "Timing feels right... I see it as the deterministic proof layer...")
    was invisible to it regardless of which phrases were on the list.
    Same failure class as the em-dash-laundering bug: a rule that looks
    like it covers the whole text but only ever touched the first line.

    Runs per sentence, same split-and-rejoin approach as
    _strip_hedges_from_absolute_claims, so it fires wherever a shield
    actually sits.

    Three repair shapes, not one blanket strip, because the shields don't
    all leave the same thing behind once removed:
      - Drop shields ("I think that X") — deleting the shield leaves a
        complete sentence ("X"), since the clause after it already has
        its own subject and verb.
      - Mid-sentence shields ("X, in my view, Y") — comma-bounded, can
        appear anywhere in the sentence, dropped along with one comma.
      - Replace shields ("I see it as X") — deleting the shield alone
        leaves a fragment with no verb. These get swapped for "It is"
        rather than removed, so the sentence still stands on its own.
    """
    parts = re.split(r'(?<=[.!?])(\s+)', text)

    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if not sentence:
            continue

        fixed = _PLAUSIBILITY_SHIELD_DROP.sub('', sentence)

        # Mid-sentence shields — comma-bounded ("This, in my view, is X"),
        # comma-then-word ("and in my view that matters", no comma needed
        # for the phrase to be grammatical), or sentence-initial ("In my
        # view, X"). Matched wherever it falls, trailing comma optional,
        # since "in my view" doesn't require punctuation around it to be
        # a valid shield. Position decides the join: a match starting at
        # the front of the sentence (sentence-initial) joins with nothing,
        # since capitalisation below handles that seam; anywhere else
        # joins with a single space so words don't concatenate ("This" +
        # "is" -> "Thisis" was the exact bug this replaced).
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

def _detect_mode(text: str) -> str:
    """
    Auto-detects intent mode from input text.
    Silent — user never sees the mode name.
    Student mode detected via five independent signals.
    Receipt attaches automatically when student mode fires.
    """
    import re

    score = 0.0

    # Academic language
    academic = re.compile(
        r"\b(furthermore|moreover|nevertheless|in conclusion|it can be argued|"
        r"according to|as argued by|cited in|essay|thesis|hypothesis|"
        r"analysis|evaluate|critically|literature|methodology)\b", re.I
    )
    words = max(len(text.split()), 1)
    ac_matches = len(academic.findall(text))
    score += min(0.35, (ac_matches / (words / 100)) * 0.08)

    # Explicit student signals
    student_explicit = re.compile(
        r"\b(help me understand|explain (to me|why|how|what)|"
        r"i (don.t|do not|can.t|cannot) understand|"
        r"my essay|my assignment|my coursework|my dissertation|"
        r"for class|my professor|my tutor|word limit|struggling with)\b", re.I
    )
    if student_explicit.search(text):
        score += 0.35

    # Academic hedges
    ac_hedges = re.compile(
        r"\b(it could be argued|it can be argued|one could argue|"
        r"to some extent|arguably|ostensibly|it is possible that|"
        r"it seems that|it appears that)\b", re.I
    )
    hedge_count = len(ac_hedges.findall(text))
    if hedge_count >= 2:
        score += 0.20
    elif hedge_count == 1:
        score += 0.10

    # Content domain clustering
    domain = re.compile(
        r"\b(theory|argument|evidence|critique|evaluation|concept|"
        r"framework|discuss|analyse|analyze|compare|contrast|examine)\b", re.I
    )
    if len(domain.findall(text)) >= 3:
        score += 0.15

    # Essay structure
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) >= 4:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 18:
            score += 0.10

    return "HELP_ME_UNDERSTAND" if score >= 0.55 else "GET_IT_DONE"
def apply_intent_mode(text: str, mode: str) -> str:
    """Applies intent mode task instruction to the render prompt."""
    mode_prompts = {
        "GET_IT_DONE": (
            "Rewrite this text. Tighten it. Remove anything that doesn't earn its place. "
            "Preserve the writer's voice exactly: their directness, their cadence, their register. "
            "Do not add warmth, hedging, or polish that isn't already there."
        ),
        "WRITE_SOMETHING": (
            "Help compose this as original content. "
            "Structure it clearly. Preserve the writer's voice throughout. "
            "The voice is theirs. The structure is your contribution."
        ),
        "THINK_IT_THROUGH": (
            "Explore the ideas in this text. Generate challenges, alternative angles, questions. "
            "This is not final copy. It is thinking. Expand, challenge, question. "
            "Preserve the writer's voice in any prose you produce."
        ),
        "HELP_ME_UNDERSTAND": (
            "Explain the concepts in this text clearly. "
            "Use step-by-step structure where it helps. Use analogies where they clarify. "
            "Write with the depth needed for genuine understanding. Not brevity. "
            "Preserve the writer's voice. Never write for them. Write as them, explaining."
        ),
    }
    return mode_prompts.get(mode, mode_prompts["GET_IT_DONE"])
def _build_voice_dna(observations: list[dict], raw_text: str, baseline: dict | None = None, ai_score: float = 0.0) -> str:
    """
    Builds a rich, structured voice DNA string for the render prompt.
    Goes far beyond observation headlines — extracts structural metrics
    and evidence quotes to give Claude concrete anchors.
    """
    if not observations:
        return "No fingerprint available. Apply a plain, direct, compressed register. UK English. Short sentences."

    lines = []

    # Structural metrics from raw text
    import re
    sentences = [s.strip() for s in re.split(r"[.!?]+", raw_text) if s.strip() and len(s.split()) >= 2]
    if sentences:
        lengths = [len(s.split()) for s in sentences]
        avg = sum(lengths) / len(lengths)
        shortest = min(lengths)
        longest = max(lengths)
        lines.append(f"SENTENCE STRUCTURE: avg {avg:.0f} words per sentence | shortest {shortest} words | longest {longest} words")

    # Hedging density
    hedge = re.compile(r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b", re.I)
    hedge_count = len(hedge.findall(raw_text))
    total_words = max(len(raw_text.split()), 1)
    hedge_density = hedge_count / total_words
    if hedge_density < 0.02:
        lines.append("HEDGING: none — states things directly, no cushioning")
    elif hedge_density > 0.05:
        lines.append("HEDGING: frequent — softens before conclusions")
    else:
        lines.append("HEDGING: occasional — hedges selectively")

    # Thought density — how much this writer compresses into each sentence
    if raw_text and len(raw_text.split()) >= 80:
        density = _score_thought_density(raw_text)
        if density["density_instruction"]:
            lines.append(f"\n{density['density_instruction']}")
        if density["peak_density_sentences"]:
            lines.append("DENSITY EXAMPLES — sentences where multiple ideas compress into one:")
            for s in density["peak_density_sentences"]:
                lines.append(f'  "{s}"')

    # Em dash usage in source writing
    em_dashes_in_source = len(re.findall(r"[—–\u2014\u2013]", raw_text))
    if em_dashes_in_source == 0:
        lines.append("PUNCTUATION: no em dashes in their writing — do not introduce any")
    else:
        lines.append("PUNCTUATION: uses some em dashes — match sparingly")

    # Observations — headline + evidence quote where available
    lines.append("\nVOICE OBSERVATIONS (from their own writing):")
    for obs in observations[:5]:
        headline = obs.get("headline", "")
        body = obs.get("body", "")
        # Extract quote from body if present — it's wrapped in double quotes
        quote_match = re.search(r'"([^"]{10,})"', body)
        if quote_match:
            lines.append(f'  - {headline}: e.g. "{quote_match.group(1)}"')
        else:
            lines.append(f"  - {headline}")

    # Anchor sentences — most distinctive sentences from their writing
    # Not the first three. The ones that sound most like them.
    usable = [s for s in sentences if 5 <= len(s.split()) <= 20]
    if usable:
        samples = _pick_anchor_sentences(usable, corpus_text=raw_text)
        lines.append("\nANCHOR SENTENCES — their most distinctive sentences (calibrate against these, do not copy):")
        for s in samples:
            lines.append(f'  "{s}"')

    # Vocabulary fingerprint — actual words, not polished synonyms
    if raw_text and len(raw_text.split()) >= 80:
        vocab = _extract_vocabulary_fingerprint(raw_text)
        vocab_block = _format_vocabulary_fingerprint(vocab)
        if vocab_block:
            lines.append(vocab_block)

    # Function patterns — connective tissue AI strips first
    # Detect input genre to suppress email closers in non-email renders
    if raw_text and len(raw_text.split()) >= 100:
        patterns = _extract_function_patterns(raw_text)
        # Detect genre of the INPUT being rendered (not the corpus)
        # Use a simple heuristic: email signals in the input text being restored
        import re as _re
        _email_signals = _re.compile(
            r'\b(Dear|Hi |Hello |Regards,|Best,|Cheers,|Thanks,|Sent from|Subject:|From:|To:)\b',
            _re.IGNORECASE
        )
        # raw_text is the corpus (user's own writing) — check its genre
        _corpus_is_email = bool(_email_signals.search(raw_text))
        _input_genre = "email" if _corpus_is_email else "article"
        pattern_block = _format_function_patterns(patterns, input_genre=_input_genre)
        if pattern_block:
            lines.append(pattern_block)

    return "\n".join(lines)
def _build_restoration_targets(
    baseline: dict,
    input_has_opinion_content: bool = True,
    input_has_directive_content: bool = True,
) -> str:
    """
    Formats the RESTORATION TARGETS block from the baseline fingerprint.
    Only included when baseline exists and input is AI-contaminated.

    Applies floors and conditional logic per v10.1 spec:
    - Hedge density floor: 0.5% minimum (section 6.2)
    - Directive ratio: omitted if baseline < 3 directives equivalent (section 6.5)
    - First-person ratio: soft target only (section 6.4)

    input_has_opinion_content: whether THIS input (not the user's baseline
    corpus) has any first-person/opinion content of its own to convert.
    A caveat on the Ownership line alone wasn't enough - tested against
    a third-party informational rewrite, the model still followed the
    numeric target over the caveat and invented opinion ("I think that
    atmosphere might be the hardest thing to replicate") that wasn't in
    the source. When False, the Ownership line is dropped from the
    prompt entirely rather than softened, so there's no percentage
    target competing with the "don't invent claims" instruction.

    input_has_directive_content: same shape of check for directness. The
    old floor only asked whether the WRITER'S baseline uses enough
    imperatives to be worth matching - it never asked whether THIS input
    has anything actionable to convert. Live-tested: a purely descriptive
    input got a fabricated call to action ("Watch a few matches...") to
    hit the directive ratio. When False, the target is dropped the same
    way Ownership is.
    """
    hedge = max(baseline["hedge_density"], 0.5)
    sd = baseline["sentence_length_sd"]
    fp = baseline["first_person_ratio"]
    directive = baseline["directive_ratio"]
    wc = baseline["word_count"]

    confidence = "provisional" if wc < 800 else "established"
    confidence_note = f"(Based on {wc} words — {confidence} baseline)"

    lines = [
        "RESTORATION TARGETS — from your baseline writing:",
        f"  Hedge density: {hedge:.1f}% per 100 words — match this rate, do not go lower. "
        f"Exception: never add hedging to a sentence carrying an absolute or superlative claim "
        f"(e.g. 'unmatched', 'the best', 'guaranteed', 'always', 'never', 'only', 'unparalleled'). "
        f"Hedging a claim like that doesn't soften the tone, it changes what's being claimed - "
        f"'unmatched' and 'hard to match, though it might vary' are different statements. Add "
        f"hedges elsewhere in the piece to hit the rate instead.",
        f"  Sentence rhythm: SD {sd:.1f} words — mix sentence lengths, do not flatten to uniform short",
    ]

    if input_has_opinion_content:
        lines.append(
            f"  Ownership: {fp:.0%} of sentences use first-person — where the input ALREADY "
            f"contains a claim, observation, or opinion of yours (stated directly, hedged, or in "
            f"passive voice), phrase it as first-person ownership at this rate. Never reassign "
            f"credit for a point, idea, or argument that belongs to someone else in the "
            f"conversation (e.g. do not turn \"your point\" into \"my point\") — this is a meaning "
            f"change, not a voice adjustment."
        )
    else:
        lines.append(
            "  Ownership: this input has no first-person claims, opinions, or reactions of the "
            "writer's own — it is factual or third-party reporting. Do not add any. No 'I think', "
            "no personal stance, no opinion not present in the source. Leave attribution as-is."
        )

    # Only include directive target if the baseline signal is meaningful
    # AND the input actually has something actionable to convert.
    # Threshold: ~3 directives in a 500-word sample ≈ 0.06 ratio
    if directive >= 0.06 and input_has_directive_content:
        lines.append(
            f"  Directness: {directive:.0%} of sentences are action statements — where the input "
            f"already implies a suggestion, recommendation, or action, phrase it directly at this "
            f"rate. Do not invent a new suggestion or call to action that is not in the input just "
            f"to hit the ratio - that is adding content, not restoring voice."
        )
    elif directive >= 0.06 and not input_has_directive_content:
        lines.append(
            "  Directness: this input is purely descriptive - it has no suggestions, "
            "recommendations, or actionable content of its own. Do not invent any to hit your "
            "usual directive rate. Leave it descriptive."
        )
    else:
        lines.append(
            "  Directness: low imperative rate in baseline — do not force directives"
        )

    lines.append(f"  {confidence_note}")
    lines.append(
        "  Treat these as specifications you are being measured against, not style suggestions."
    )

    return "\n".join(lines)
def _build_system_prompt(
    voice_dna: str,
    mode_instruction: str,
    word_count_input: int,
    ai_score: float,
    baseline: dict | None = None,
    input_text: str = "",
    render_context: str = "",
    voice_profile_summary: str = "",
) -> str:
    """
    Builds the full system prompt.
    Two paths: AI-contaminated input vs clean human input.
    Both use the same voice DNA. The AI path adds aggressive stripping instructions.

    input_text: the actual text being rewritten this render (not the user's
    baseline corpus). Used to check whether THIS input has any first-person
    content of its own before the Ownership restoration target is included -
    see _build_restoration_targets. Optional/backward-compatible: if omitted,
    defaults to permissive (target included) rather than silently changing
    behaviour for any caller not yet passing it.

    render_context: optional, per-render "who's this for, what's it for"
    text from the field above the paste box. Register/audience is a
    genuinely distinct axis from personal voice, not a variant of it -
    injected as generation steering only, deliberately separate from
    voice_dna's numeric baseline targets, which stay verifying against
    the person's own blended voice regardless of what this render is for.

    voice_profile_summary: an LLM-distilled natural-language profile of
    the person's writing habits (see build_voice_profile_summary_prompt),
    generated once and cached, not built fresh per render. Sits alongside
    voice_dna's real anchor excerpts and measured numeric dimensions as a
    third, complementary signal — not a replacement for either. Optional:
    a render with none (generation failed, or hasn't happened yet for
    this baseline) proceeds exactly as it did before this existed.
    """

    base_rules = (
        "ABSOLUTE RULES — never break these:\n"
        "1. No em dashes. Rewrite the sentence without one — split it into two sentences or "
        "join with a comma. Do not substitute a hyphen or spaced hyphen for a dash; that reads "
        "as the same tell.\n"
        "2. No verbose openers: no 'it is important to note', no 'in today's landscape', "
        "no 'it goes without saying', no 'with that in mind', no 'to that end'.\n"
        "3. No filler transitions: no 'furthermore', no 'moreover', no 'in conclusion', "
        "no 'additionally', no 'notwithstanding'.\n"
        "4. No corporate filler: no 'leveraging', no 'synergies', no 'holistic', "
        "no 'transformative', no 'robust', no 'cutting-edge'.\n"
        "5. No preamble. No explanation. Return only the rewritten text.\n"
        "6. UK English throughout.\n"
        "7. Every paragraph in the input gets a paragraph in the output. Do not compress into a summary.\n"
        f"8. Output must be at least {word_count_input} words. The input is {word_count_input} words. "
        "Match or exceed it. If you run short, add specificity and texture to points already in the "
        "input - more detail on what is already there. Do not pad with filler, and do not introduce "
        "a new claim, opinion, or idea that is not stated or directly implied by the input, even to "
        "hit the word count."
        "\n9. Do not invent content. You may split one long sentence into two or three shorter ones "
        "to match sentence-rhythm targets - that is allowed and often required. What is not allowed: "
        "adding a new sentence, clause, or standalone line whose content, claim, or emphasis is not "
        "already present in the input, even a short one, even if it sounds like a natural closer or "
        "punchline. Every sentence in the output must trace back to something actually said in the "
        "input. If you find yourself writing a sentence to make the piece land better rather than to "
        "carry something already there, delete it."
    )

    render_context_block = (
        f"CONTEXT FOR THIS PIECE: {render_context.strip()}\n"
        "Use this only to inform word choice, formality, and directness "
        "for this specific piece — it does not change the voice profile "
        "above, which still reflects this person's own writing regardless "
        "of who this particular piece is for.\n\n"
        if render_context and render_context.strip() else ""
    )

    profile_summary_block = (
        f"WRITER'S DISTINCTIVE HABITS: {voice_profile_summary.strip()}\n\n"
        if voice_profile_summary and voice_profile_summary.strip() else ""
    )

    if ai_score >= 0.25:
        # AI-contaminated path — stripping + restoration
        input_has_opinion_content = True
        input_has_directive_content = True
        if input_text:
            input_metrics = compute_baseline_metrics(input_text)
            input_has_opinion_content = input_metrics["first_person_ratio"] > 0
            input_has_directive_content = input_metrics["directive_ratio"] > 0
        restoration_block = (
            f"\n\n{_build_restoration_targets(baseline, input_has_opinion_content, input_has_directive_content)}"
            if baseline else ""
        )
        # Register instruction — match the source register, not an elevated version
        # Research basis: the user's best version is the unpolished authentic version
        # Source register detected from fitness tier stored in voice_dna context
        register_instruction = (
            "REGISTER — this is critical:\n"
            "Match the register of the source writing exactly. Do not elevate, polish, or formalise.\n"
            "If the source writing is direct and slightly rough, the output must be direct and slightly rough.\n"
            "The goal is not better writing. The goal is their writing.\n"
            "The unpolished edge is part of the voice. Preserve it.\n"
            "\n"
            "COMPLETENESS — non-negotiable:\n"
            "Short sentences are correct. But every sentence must be a complete thought.\n"
            "Do not fragment assertions. 'It was not a disaster.' not 'Not a disaster.'\n"
            "The subject stays unless it is an action statement (Will, Can, Pls, Have).\n"
            "Curtness is a style choice. Truncation is an error. Know the difference.\n\n"
        )

        prompt = (
            "You are a voice rendering engine with one job: strip AI-generated language and rewrite "
            "in this person's authentic voice.\n\n"
            "The input text has been identified as AI-generated or heavily AI-influenced. "
            "It carries AI tells: verbose openers, em dashes, stacked hedges, filler transitions, "
            "passive constructions. Your job is to eliminate all of that and replace it with "
            "the voice profile below.\n\n"
            f"VOICE PROFILE:\n{voice_dna}"
            f"{restoration_block}\n\n"
            f"{profile_summary_block}"
            f"TASK:\n{mode_instruction}\n\n"
            f"{render_context_block}"
            f"{register_instruction}"
            "STRIPPING INSTRUCTIONS:\n"
            "- Identify every AI tell in the input. Rewrite those sentences from scratch.\n"
            "- Do not preserve the AI's sentence structure. Break it up. Shorten it.\n"
            "- Do not preserve the AI's transitions. Cut them or replace with nothing.\n"
            "- The content and ideas are the writer's. The words and structure are the AI's. "
            "Keep the ideas. Destroy the words.\n"
            "- After rewriting, read back through and ask: does this sound like a human "
            "who matches the voice profile? If not, rewrite again.\n"
            "- Do not add warmth, polish, or formality not already in the voice profile.\n"
            "- The rough edges in their writing are not mistakes. They are the voice.\n"
            "- Before you finish: re-read THE STANDARD sentences in the voice profile. "
            "Ask yourself: does this output feel like it came from the same person? "
            "If not, rewrite until it does.\n\n"
            f"{base_rules}"
        )
    else:
        # Clean human input path — preservation is the primary job
        prompt = (
            "You are a voice rendering engine. Your job is to rewrite this text so it sounds "
            "exactly like the person who wrote the samples in the voice profile below.\n\n"
            f"VOICE PROFILE:\n{voice_dna}\n\n"
            f"{profile_summary_block}"
            f"TASK:\n{mode_instruction}\n\n"
            f"{render_context_block}"
            "RENDERING INSTRUCTIONS:\n"
            "- Match the sentence length from the profile exactly. If they write short, write short.\n"
            "- Match the directness. If they own their statements, do not hedge.\n"
            "- Match the register. If they write peer-to-peer, do not write down to the reader.\n"
            "- Do not add warmth, polish, or formality that isn't already in the voice profile.\n"
            "- Do not smooth rough edges. The rough edges may be part of their voice.\n"
            "- LEXICAL FIDELITY — critical: this is not a paraphrase task. Preserve the writer's own words and "
            "phrasing wherever they already fit the voice profile. Do not substitute synonyms for variety "
            "('if' vs 'whether', 'a while back' vs 'some time ago', etc). Only change a word or sentence "
            "structure when it is actually needed to hit a specific voice target above (sentence length, "
            "directness, register, hedging). If the input wording already satisfies the target, leave it "
            "exactly as written. Most of the input's original phrasing should survive into the output unchanged.\n"
            "- CRITICAL: STOP WHEN THE CONTENT IS DONE. The final paragraph of the input is the final paragraph of the output. "
            "Do not add sentences after it. Do not summarise. Do not close. Do not reflect. "
            "When the last substantive point from the input is restated, your job is finished. Stop there.\n\n"
            f"{base_rules}"
        )

    return prompt
def _detect_locale(text: str) -> str:
    """
    Detects whether the user writes in UK or US English.
    Scans for UK spelling markers. If enough are present, returns "uk".
    Falls back to "uk" if inconclusive — Voicova is a UK product.
    """
    import re

    uk_markers = [
        r"\bcolour\b", r"\bcolours\b",
        r"\bhonour\b", r"\bhonours\b",
        r"\bbehaviour\b", r"\bbehaviours\b",
        r"\borganis", r"\brecognis", r"\bprioritis",
        r"\banalyse\b", r"\banalyses\b",
        r"\bcentre\b", r"\bcentres\b",
        r"\bfavour\b", r"\bfavours\b",
        r"\bneighbour\b", r"\bneighbours\b",
        r"\bwhilst\b", r"\bfortnight\b",
        r"\bprogramme\b", r"\bcheque\b",
        r"\btravelled\b", r"\bcancelled\b",
    ]

    us_markers = [
        r"\bcolor\b", r"\bcolors\b",
        r"\bhonor\b", r"\bhonors\b",
        r"\bbehavior\b", r"\bbehaviors\b",
        r"\borganize\b", r"\brecognize\b", r"\bprioritize\b",
        r"\banalyze\b", r"\banalyzes\b",
        r"\bcenter\b", r"\bcenters\b",
        r"\bfavor\b", r"\bfavors\b",
        r"\bneighbor\b", r"\bneighbors\b",
        r"\btraveled\b", r"\bcanceled\b",
    ]

    uk_hits = sum(1 for m in uk_markers if re.search(m, text, re.I))
    us_hits = sum(1 for m in us_markers if re.search(m, text, re.I))

    if us_hits > uk_hits:
        return "us"
    return "uk"
def _apply_uk_english(text: str) -> str:
    """
    Replaces US English idioms and AI-default vocabulary with UK English equivalents.
    Applied to every render output before it reaches the user.
    Word-boundary aware — avoids partial replacements.
    """
    import re

    # Ordered — longer phrases first to avoid partial matches
    replacements = [
        # AI-default vocabulary
        (r"\bsurfaces\b", "brings up"),
        (r"\bleverages?\b", "uses"),
        (r"\bleverage\b", "use"),
        (r"\breach out\b", "contact"),
        (r"\breaching out\b", "contacting"),
        (r"\bgaps firing\b", "gaps triggering"),
        (r"\butilizes?\b", "uses"),
        (r"\butilize\b", "use"),
        (r"\butilization\b", "use"),
        # US spelling -> UK spelling
        (r"\bprioritize\b", "prioritise"),
        (r"\bprioritizes\b", "prioritises"),
        (r"\bprioritizing\b", "prioritising"),
        (r"\banalyze\b", "analyse"),
        (r"\banalyzes\b", "analyses"),
        (r"\banalyzing\b", "analysing"),
        (r"\borganize\b", "organise"),
        (r"\borganizes\b", "organises"),
        (r"\borganizing\b", "organising"),
        (r"\brecognize\b", "recognise"),
        (r"\brecognizes\b", "recognises"),
        (r"\brecognizing\b", "recognising"),
        (r"\bcolor\b", "colour"),
        (r"\bcolors\b", "colours"),
        (r"\bcenter\b", "centre"),
        (r"\bcenters\b", "centres"),
        (r"\bfavor\b", "favour"),
        (r"\bfavors\b", "favours"),
        (r"\bhonor\b", "honour"),
        (r"\bhonors\b", "honours"),
        (r"\bbehavior\b", "behaviour"),
        (r"\bbehaviors\b", "behaviours"),
        (r"\bneighbor\b", "neighbour"),
        (r"\bneighbors\b", "neighbours"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # "like" as a list introducer is American — UK English uses "such as" or "including"
    import re as _re_like
    text = _re_like.sub(
        r"\blike\s+(cost of living|NHS|housing|inflation|unemployment|\w+(?:,\s*\w+)+)",
        lambda m: "such as " + m.group(1), text
    )
    # Broader: "issues like X" -> "issues such as X"
    text = _re_like.sub(r"\b(issues|problems|areas|things|factors|topics)\s+like\b",
                        lambda m: m.group(1) + " such as", text)

    # Grammar guardrail: the model sometimes over-generalises "UK English
    # throughout" into swapping idiomatic "like" for "such as" after verbs
    # of comparison/perception (feel/sound/look/seem/taste/smell), which is
    # never correct - "feel such as survival" isn't a register choice, it's
    # a grammar error. This didn't come from the substitutions above (they
    # require a list pattern this doesn't match); it's the model's own raw
    # output. Deterministic revert, not a register preference either way.
    text = _re_like.sub(
        r"\b(feel|feels|felt|sound|sounds|sounded|look|looks|looked|"
        r"seem|seems|seemed|taste|tastes|tasted|smell|smells|smelled)\s+"
        r"such as\b",
        lambda m: m.group(1) + " like",
        text,
        flags=_re_like.IGNORECASE,
    )
    return text


# Dash removal must not just swap punctuation - " - " reads as its own AI
# tell (see score_ai_tells' spaced-hyphen check). Deterministic, no model
# in the loop: for each dash, look at the clause on either side. If both
# read as full independent clauses (4+ words, and the clause after doesn't
# open on a dependent word like "which"/"because"), split into two proper
# sentences. Otherwise join with a comma. Heuristic, not perfect - but it's
# the same input/same output guarantee as everything else in this sweep,
# and it never reproduces the tell it's meant to remove.
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
    Business-rule guardrail, not a model instruction. A hedge word landing
    in the same sentence as an absolute/superlative claim doesn't soften
    tone, it changes the claim - "unmatched" and "might be hard to match,
    though it could vary" are different statements. This was already an
    explicit instruction in both the initial prompt and the correction
    pass (RESTORATION TARGETS, build_correction_prompt) - live testing
    showed the model doesn't reliably follow it. Prompt compliance isn't
    a guardrail. This is the hard version: scans the actual output text,
    no model judgement involved, same input always same output, matching
    every other rule in this sweep.

    Known limitation, stated plainly rather than glossed over: this only
    catches absolute claims that survive as recognisable words/phrases in
    the output, including two paraphrases we've actually observed in
    testing ("hard to match", "difficult to match" for "unmatched"). A
    claim paraphrased into wording outside that list won't be caught -
    it's a lexical guardrail against known patterns, not a meaning
    detector. Expand the list as new paraphrase patterns turn up in
    testing rather than trying to anticipate all of them up front.
    """
    parts = re.split(r'(?<=[.!?])(\s+)', text)

    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if not sentence or not _ABSOLUTE_CLAIM_PATTERN.search(sentence):
            continue

        fixed = sentence

        # Trailing ", though/although ... [hedge word] ..." clauses
        # undermine the claim structurally - strip the whole clause, not
        # just the hedge word inside it, since removing only the word
        # would leave a dangling qualifier with nothing hedged in it.
        fixed = _TRAILING_QUALIFIER_PATTERN.sub('.', fixed)
        fixed = re.sub(r'\.{2,}', '.', fixed)

        if _HEDGE_WORD_PATTERN.search(fixed):
            # Pure adverb hedges - safe to delete outright, no verb
            # conjugation involved.
            fixed = re.sub(
                r'\b(perhaps|possibly|maybe|potentially|somewhat|quite|rather)\s+',
                '', fixed, flags=re.I,
            )
            # Modal + be/have - safe, fixed auxiliary swap.
            fixed = re.sub(r'\bmight be\b', 'is', fixed, flags=re.I)
            fixed = re.sub(r'\bcould be\b', 'is', fixed, flags=re.I)
            fixed = re.sub(r'\bmight have\b', 'has', fixed, flags=re.I)
            fixed = re.sub(r'\bcould have\b', 'has', fixed, flags=re.I)

            # Modal + bare verb - strip the modal, conjugate what's left.
            # Checks the word before the modal for a plural signal first
            # (see _looks_plural_subject) - reduces subject-verb
            # disagreement but doesn't eliminate it for edge cases
            # (irregular plurals, "crisis"-style false positives).
            # Accepting the remaining risk over leaving a claim-changing
            # hedge in place - the trade this whole guardrail is built on.
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
    """
    Crude plural-subject check used only to decide verb conjugation when
    stripping a modal (see _strip_hedges_from_absolute_claims). Looks at
    the word immediately before the modal - and if that word is a
    relative pronoun (that/which/who), steps back one more word to the
    actual antecedent ("crowds that could generate" needs to check
    "crowds", not "that"). Not a parser - a cheap signal to avoid the
    most common subject-verb disagreement. Known to be imperfect (e.g.
    "crisis", "focus" end in s but are singular; irregular plurals like
    "people" won't be caught) - reduces the conjugation error rate,
    doesn't eliminate it.
    """
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


def _regex_sweep(text: str, keep_contractions: bool = False) -> str:
    """
    Deterministic guardrail sweep — runs on every render output.
    No API call. No Claude involvement. Code enforces these rules.

    1. Em dashes — split into two sentences or joined with a comma,
       whichever the surrounding clauses support (see
       _split_dashes_deterministic). Never a spaced hyphen — that is
       just as much an AI tell as the em dash it replaced.
    2. Contractions expanded to full form — ONLY if the user's own baseline
       doesn't use them. This used to be unconditional (hardcoded to one
       person's writing convention). Fixed: contractions are a real voice
       marker, not a universal AI tell — stripping them from someone whose
       own writing uses them pushes the output away from their voice, not
       towards it.
    3. Claude literary closers stripped from paragraph endings
    4. Plausibility shields stripped per-sentence ("I think", "I see it
       as", "in my view" — see _strip_plausibility_shields), then Claude
       default constructions replaced
    5. Repeated words
    6. Double spaces
    7. Hedges stripped from sentences carrying an absolute/superlative
       claim (see _strip_hedges_from_absolute_claims) - a business-rule
       guardrail, not a prompt instruction the model might skip.
    8. [removed] Missing article fix — see note at that step below;
       blacklist heuristic broke correct text, removed rather than patched.
    9. Orphan/doubled punctuation cleanup — ",." collapsed to ".",
       doubled commas collapsed to one, space before terminal
       punctuation removed. Runs last, catches leftovers from any
       earlier step or the upstream LLM grammar pass.
    """
    import re

    # 1. Em dashes — split or join, never a spaced-hyphen substitute
    text = _split_dashes_deterministic(text)
    # Belt and braces — any remaining dash variant the splitter missed
    # falls back to a comma join, not " - " (that reintroduces the tell).
    text = re.sub(r"[\u2012\u2013\u2014\u2015]\s*", ", ", text)
    text = re.sub(r'  +', ' ', text)

    # 2. Contractions — only expanded if the user's own baseline doesn't use them
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

    # 3. Claude literary closers — abstract triplet endings
    # "The ambition exists. The blueprint doesn't. Until they commit..."
    # Strip abstract trailing sentences from final paragraph
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
            # Also catch "Perhaps X. Perhaps Y." couplet — Claude philosophical closer
            perhaps_couplet = re.compile(r'Perhaps [^.]+\. Perhaps [^.]+\.?', re.IGNORECASE)
            if perhaps_couplet.search(last_para):
                # Strip from first "Perhaps" in the couplet
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

    # 4. Claude default constructions
    # Strip plausibility shields — runs per sentence throughout the text,
    # not just at the very start. See _strip_plausibility_shields for why
    # the old line-anchored version missed anything past sentence one.
    #
    # DELIBERATE, DOCUMENTED DUPLICATION — read before editing either side:
    # This function (root-level, live Railway product) and
    # packages/voxa-core/src/voxa_core/text_guardrail.py (canonical for
    # the FastAPI/packages ecosystem) are two intentionally separate
    # copies, not one accidentally forked twice. Root doesn't depend on
    # packages/ at all — converting the live deployment to install
    # packages/ is a separate, higher-risk decision, explicitly scoped
    # out during the August 2026 guardrail-consolidation fix (see that
    # module's docstring for the full "Option A vs Option B" note). If
    # you fix a bug here, mirror it in voxa_core.text_guardrail, or the
    # gap this consolidation closed reopens on the API side.
    text = _strip_plausibility_shields(text)
    # "I am genuinely uncertain about" -> "I am not sure"
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
    ]
    for pattern, replacement in claude_constructions:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Analytical-register constructions — separate from claude_constructions
    # above because that list is tuned for corporate-slop vocabulary and
    # doesn't touch the abstract-noun-as-verb habit that shows up in
    # analytical/argumentative writing (drift, surface, land on, unpack).
    # Only applied when _classify_register says the text warrants it, so
    # this never fires on — and never risks mangling — corporate-register
    # input. Mirrors _ANALYTICAL_TELL_PHRASES in voice_engine.py; kept as
    # a replace-list here since this function's job is to fix, not flag.
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
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # 5. Repeated words
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)

    # 6. Double spaces
    text = re.sub(r'  +', ' ', text)

    # 7. [removed] Missing article fix — blacklist-based heuristic
    # ("was not X" -> "was not a X" unless X is in a ~35-word exception
    # list) couldn't distinguish adjectives from nouns. Demonstrated in
    # production: "The pressures are not small." -> "...not a small."
    # because "small" wasn't on the exception list. A blacklist against
    # the entire English adjective vocabulary can't scale - there is
    # always another adjective not on the list. Also redundant:
    # _grammar_fix_pass (the LLM call, runs right before this sweep)
    # already has "Missing articles before countable nouns" in its
    # brief, and can actually tell nouns from adjectives. Removed
    # rather than patched - patching means adding "small" today and
    # the next missed adjective next time.

    # 8. Tricolon fragment lists — three consecutive fragments starting with A/An/The
    # AI uses these as rhetorical closers. Deterministic strip.
    import re as _re_tri
    # Detect and collapse "A X Ying. A X Ying. A X Ying." into one sentence
    tricolon = _re_tri.compile(
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\.\s+"
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\.\s+"
        r"(A|An|The) ([\w\s]+?ing[^.!?]{0,60})\."
    )
    def _collapse_tricolon(m):
        # Return just the first fragment as a complete sentence
        return f"{m.group(1)} {m.group(2)}."
    text = tricolon.sub(_collapse_tricolon, text)

    # 9. Editorial additions — renderer adding judgments not in source
    editorial = [
        (r"That framing might be too simple[^.]*\.", ""),
        (r"That (observation|framing|assessment|reading) (might|may|could) be[^.]*\.", ""),
        (r"Whether that (is|was) (fair|accurate|right|correct)[^.]*\.", ""),
    ]
    for pattern, replacement in editorial:
        text = _re_tri.sub(pattern, replacement, text, flags=_re_tri.IGNORECASE)
    text = _re_tri.sub(r"  +", " ", text).strip()

    # 10. Recapitalise sentence starts — opener-strip rules above (I think that,
    # I believe that, I would argue that, As we/you know) can leave a lowercase
    # word at the front of a sentence or the whole string. Safety net, not
    # tied to any one pattern.
    text = re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)

    # 11. Strip hedges from sentences carrying an absolute/superlative claim.
    # Business-rule guardrail, not a prompt instruction - see
    # _strip_hedges_from_absolute_claims for why this had to move here.
    text = _strip_hedges_from_absolute_claims(text)

    # 12. Orphan/doubled punctuation cleanup — safety net, runs last so
    # it catches whatever any earlier step (or the upstream LLM grammar
    # pass in _grammar_fix_pass, which is not deterministic and can't
    # be regex-fixed at the source) leaves behind. Confirmed live:
    # "Hi Josh,." shipped from _grammar_fix_pass inserting a name
    # before the salutation comma and mishandling the close - this
    # runs after that stage every time, so it's a general catch, not a
    # patch for one salutation.
    #
    # ",." -> "." only, not the reverse (".," -> ","): a trailing comma
    # immediately before a sentence-ending period has no legitimate
    # English use, always a mechanical leftover. The reverse direction
    # is NOT safe to blanket-fix - "e.g.," "i.e.," "etc.," are all
    # correct abbreviation-plus-comma sequences that happen to contain
    # ".," and collapsing them would corrupt real abbreviations, not
    # fix an error. Left alone rather than guessed at.
    text = re.sub(r",\s*\.", ".", text)
    # Doubled commas - no legitimate construction has two commas
    # back to back, from any source (hedge deletion, dash-splitting
    # fallback, LLM stage).
    text = re.sub(r",\s*,+", ",", text)
    # Redundant terminal punctuation trailing a closing quote - e.g.
    # 'here is what happens when it did not.".' Confirmed live: the
    # quoted content already ends in [.!?], the closing quote follows
    # immediately, and a second terminal mark is appended outside the
    # quote as if the quote itself weren't already a complete sentence.
    # Always safe to drop the outer mark in this exact shape - a quote
    # whose own content ends with [.!?] never legitimately needs a
    # second terminal character right after the closing quote mark.
    text = re.sub(r'([.!?])"[.!?]', r'\1"', text)
    # Space before terminal punctuation - same safe pattern already
    # proven inside _split_dashes_deterministic's own local cleanup,
    # applied here as a general final pass rather than only within
    # that function's scope.
    text = re.sub(r" ([,.!?])", r"\1", text)

    return text
def _grammar_fix_pass(text: str, client) -> str:
    """
    Second Claude call — grammar errors only.
    Brief: find and fix grammar errors. Do not rewrite. Do not change voice.
    Returns corrected text. If no errors found, returns original text unchanged.
    """
    system = (
        "You are a precise grammar checker for UK English. Fix errors only. Never rewrite.\n\n"
        "FIX THESE:\n"
        "1. Adverb/adjective confusion: 'move quicker' → 'move more quickly', "
        "'runs faster' is fine (manner adverb), 'move quicker' is not.\n"
        "2. Missing prepositions: 'lagged the ambition' → 'lagged behind the ambition', "
        "'fell short expectations' → 'fell short of expectations'.\n"
        "3. Loose gerund constructions: 'no longer seeming to work' → 'no longer seem to work', "
        "'appearing to struggle' when the subject is clear → 'appears to struggle'.\n"
        "4. Open-ended lists that need a closer: a list ending without resolution "
        "(e.g. 'cost of living, NHS waiting lists, housing.') should end with "
        "'and so on' or 'among other things' where the writer clearly intended more. "
        "Only add a closer if the list is plainly incomplete — do not add to every list.\n"
        "5. Missing articles (a, an, the) before countable nouns.\n"
        "6. Dropped words that break the meaning of a sentence.\n"
        "\n"
        "DO NOT TOUCH:\n"
        "1. Collective nouns with plural verbs ('England are', 'the team are', 'Labour are') — correct in UK English.\n"
        "2. Sentence fragments used deliberately for rhythm ('Football in fragments.', 'Not a disaster.').\n"
        "3. Any word choice, sentence structure, or punctuation that is grammatically valid.\n"
        "4. Register, tone, or voice — change nothing that is not a clear error.\n"
        "5. UK spellings — do not Americanise anything.\n"
        "6. Names, proper nouns, numbers, dates, and any other factual detail. Never substitute, "
        "correct, or 'fix' a name — including the opening salutation name — even if it looks unusual "
        "or you think a different name is more likely. If a name looks like it might contain a typo, "
        "leave it exactly as given. Confirmed live: this pass has previously replaced the addressee's "
        "actual name with a different one while leaving the surrounding grammar clean — that is a "
        "content error, not a grammar fix, and it is never in scope here.\n"
        "\n"
        "Return only the corrected text. No explanation. No preamble."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return response.content[0].text.strip()


def build_voice_profile_summary_prompt() -> str:
    """
    System prompt for the one-time distillation call: take a person's
    raw writing corpus and observations, and have Claude condense them
    into a short natural-language profile of their distinctive habits.

    Grounded in a specific finding, not a guess: research on guided
    profile generation found that generating FROM a distilled profile
    outperformed generating from raw personal context directly — a
    real accuracy improvement in preference prediction, and a real
    quality lift on the closest comparable task (paraphrasing) to
    what VOICOVA does. The mechanism: an LLM given a person's raw
    writing samples and told "sound like this" has to do its own
    ad-hoc distillation under generation pressure, at the same time as
    generating; doing that distillation as a SEPARATE, dedicated step
    beforehand — with no generation task competing for the model's
    attention — produces a sharper signal to condition on.

    This is genuinely different from the anchor sentences and numeric
    baseline targets already in the prompt, not a replacement for
    them: anchors are real excerpts, numeric targets are measured
    dimensions, this is a synthesised natural-language description
    sitting between the two. All three get injected together — see
    _build_voice_dna's caller in app.py.

    Deliberately short output: this is one signal among several in an
    already-long system prompt, not the whole thing. A few sentences,
    not a report.
    """
    return (
        "You are analysing a sample of someone's own writing to produce a short, "
        "concise profile of their distinctive habits as a writer — not a summary "
        "of what they wrote, a description of HOW they write.\n\n"
        "Cover only what's genuinely distinctive: sentence rhythm, how they open "
        "and close thoughts, what they emphasise, recurring phrasing patterns, "
        "how directly they state things, how much (or little) they hedge or "
        "qualify, whether they use humour or bluntness, any characteristic verbal "
        "tics. Skip anything generic that could describe most competent writers.\n\n"
        "Write 3-5 sentences, plain prose, no headers, no bullet points, no "
        "preamble. Address the writer's habits directly ('Opens with the concrete "
        "problem before context.' not 'The writer opens with...'). This will be "
        "used as a compact reference for another model generating text in this "
        "person's voice, not shown to the person themselves — write for that "
        "purpose, not as a compliment or a critique.\n\n"
        "Return only the profile. Nothing else."
    )


# ============================================================
# New in v4 — correction prompt extended to target semantic drift
# ============================================================

def merge_starter_evidence(blended_delta: dict, starter_delta: dict | None) -> dict:
    """
    Widens what feeds build_correction_prompt using the starter-only
    baseline, not just the blended (sample1 + starters, word-count
    weighted) one.

    Why: the four sentence-starter completions are the least performed,
    most candid writing collected. Blended into a weighted average with
    sample1, a real drift on a starter-specific dimension can get
    diluted below the correction threshold if sample1 is long and
    already close to target. This does not change that dilution - the
    blended baseline stays the one reported as Voice Match. It only
    adds a second, independent check: if the starter-only baseline
    alone would flag a MISS that the blended check missed, that
    dimension gets promoted into the correction instructions too.

    Rule-based only. No API call. Reuses score_render_delta's own
    HIT/CLOSE/MISSED verdict logic on both baselines - this function
    just decides which entries reach the one correction call that
    already conditionally fires.
    """
    if not starter_delta:
        return blended_delta
    merged = dict(blended_delta)
    for key, s_entry in starter_delta.items():
        b_entry = merged.get(key)
        if s_entry.get("verdict") == "MISSED" and (not b_entry or b_entry.get("verdict") != "MISSED"):
            merged[key] = s_entry
    return merged


def build_correction_prompt(
    delta: dict,
    semantic: dict | None = None,
    input_has_opinion_content: bool = True,
    input_has_directive_content: bool = True,
) -> str | None:
    """
    Builds the targeted, surgical correction prompt for whatever the
    initial render missed — voice dimensions (proven logic, unchanged)
    and, new in v4, dropped entities/facts caught by score_semantic_drift.
    Returns None if nothing needs correcting.

    input_has_opinion_content: same signal as _build_restoration_targets -
    whether the input being rewritten has any first-person content of its
    own to convert. When False, the first_person_ratio correction is
    skipped outright rather than instructed-with-a-caveat: a caveat next
    to a numeric target still lost to the target in testing (see the
    Ownership fix in this same commit's history).

    input_has_directive_content: same shape of check for directness. Live
    test: a purely descriptive input got a fabricated call to action
    ("Watch a few matches...") from this correction pass alone, added
    after the initial render had already (correctly) stayed descriptive.
    When False, the directive_ratio correction is skipped the same way.
    """
    correction_instructions = []

    for key, d in delta.items():
        if d["verdict"] != "MISSED":
            continue
        b_val, o_val = d["baseline"], d["output"]
        if key == "hedge_density":
            if o_val < b_val:
                correction_instructions.append(
                    f"Hedge density is {o_val:.1f}% but should be {b_val:.1f}%. "
                    f"Add natural uncertainty in 2-3 places using words like 'might', 'could', 'perhaps'. "
                    f"Only where the writer would genuinely be uncertain. Do not force it. Never add "
                    f"a hedge to a sentence carrying an absolute or superlative claim (e.g. "
                    f"'unmatched', 'the best', 'guaranteed', 'always', 'never', 'only') - that changes "
                    f"the claim, not just the tone. Find 2-3 other sentences to hedge instead.")
            else:
                correction_instructions.append(
                    f"Hedge density is {o_val:.1f}% but should be {b_val:.1f}%. "
                    f"Remove hedging words. Make statements direct.")
        elif key == "sentence_length_sd":
            if o_val < b_val:
                correction_instructions.append(
                    f"Sentence rhythm is too uniform (SD {o_val:.1f}, target {b_val:.1f}). "
                    f"Vary the lengths deliberately — some very short (3-5 words), some longer (15-20 words). "
                    f"The contrast is part of their voice.")
            else:
                correction_instructions.append(
                    f"Sentence rhythm is too varied (SD {o_val:.1f}, target {b_val:.1f}). "
                    f"Bring the lengths closer together. More consistent pacing.")
        elif key == "first_person_ratio":
            if o_val < b_val and input_has_opinion_content:
                correction_instructions.append(
                    f"Ownership is too low ({o_val:.0%} first-person, target {b_val:.0%}). "
                    f"Replace passive or third-person constructions with direct first-person "
                    f"statements — but ONLY for the writer's own claims and reactions that are "
                    f"already present in the input. Never reassign credit for a point, idea, or "
                    f"argument that belongs to someone else in the conversation (e.g. do not turn "
                    f"'your point' into 'my point').")
            # else: input has nothing of the writer's own to convert (or
            # this is a factual/third-party rewrite) - skip the correction
            # entirely rather than nudge with a caveat that can lose to
            # the numeric target.
        elif key == "directive_ratio" and b_val >= 0.06:
            if o_val < b_val and input_has_directive_content:
                correction_instructions.append(
                    f"Directive pattern is missing ({o_val:.0%} action statements, target {b_val:.0%}). "
                    f"Convert 1-2 EXISTING suggestions or recommendations in the input into direct "
                    f"action statements. No 'please', no 'could you'. Do not invent a new suggestion "
                    f"or call to action that is not already implied by the input just to hit the ratio.")
            # else: input has nothing actionable of its own to convert -
            # skip rather than risk fabricating a suggestion.

    # New in v4 — semantic correction targets, parallel to voice correction
    dropped = (semantic or {}).get("dropped_entities", [])
    if dropped:
        named = ", ".join(dropped[:5])
        correction_instructions.append(
            f"The rewrite dropped specific facts from the original: {named}. "
            f"Add them back in naturally. Do not invent replacements — restore what was actually said.")

    # Attribution swaps ('your point' -> 'my point' or reverse) are a
    # meaning change disguised as a style change - score_semantic_drift's
    # word-overlap comparison can't see these ('your'/'my' are stopwords
    # there by design), so this is the one place they get caught and
    # corrected. Listed first and phrased as a hard instruction, not a
    # style nudge, since getting this wrong is worse than any of the
    # voice-dimension misses above.
    swaps = (semantic or {}).get("attribution_swaps", [])
    if swaps:
        listed = "; ".join(swaps)
        correction_instructions.insert(
            0,
            f"CREDIT ERROR — fix this first: {listed}. Restore who the original text actually "
            f"credited. This is not a style choice; check the original wording and correct it exactly."
        )

    if not correction_instructions:
        return None

    return (
        "You are making precise surgical corrections to a voice restoration. "
        "The text below is close but has missed specific targets from the writer's baseline, "
        "or dropped specific facts from the original. "
        "Make only the changes needed to hit the targets. Do not rewrite. Do not improve. "
        "Correct only what is listed. Preserve everything else exactly.\n\n"
        "CORRECTIONS NEEDED:\n"
        + "\n".join(f"{i+1}. {inst}" for i, inst in enumerate(correction_instructions))
        + "\n\nABSOLUTE RULES: No em dashes. UK English. Return only the corrected text."
    )

