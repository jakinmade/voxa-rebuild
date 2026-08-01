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
)

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
        samples = _pick_anchor_sentences(usable)
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
def _build_restoration_targets(baseline: dict) -> str:
    """
    Formats the RESTORATION TARGETS block from the baseline fingerprint.
    Only included when baseline exists and input is AI-contaminated.

    Applies floors and conditional logic per v10.1 spec:
    - Hedge density floor: 0.5% minimum (section 6.2)
    - Directive ratio: omitted if baseline < 3 directives equivalent (section 6.5)
    - First-person ratio: soft target only (section 6.4)
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
        f"  Hedge density: {hedge:.1f}% per 100 words — match this rate, do not go lower",
        f"  Sentence rhythm: SD {sd:.1f} words — mix sentence lengths, do not flatten to uniform short",
        f"  Ownership: {fp:.0%} of sentences use first-person — own statements at this rate",
    ]

    # Only include directive target if signal is meaningful
    # Threshold: ~3 directives in a 500-word sample ≈ 0.06 ratio
    if directive >= 0.06:
        lines.append(
            f"  Directness: {directive:.0%} of sentences are action statements — match this proportion"
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
) -> str:
    """
    Builds the full system prompt.
    Two paths: AI-contaminated input vs clean human input.
    Both use the same voice DNA. The AI path adds aggressive stripping instructions.
    """

    base_rules = (
        "ABSOLUTE RULES — never break these:\n"
        "1. No em dashes. Replace every — or – with a hyphen or rewrite the sentence.\n"
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
    )

    if ai_score >= 0.25:
        # AI-contaminated path — stripping + restoration
        restoration_block = (
            f"\n\n{_build_restoration_targets(baseline)}"
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
            f"TASK:\n{mode_instruction}\n\n"
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
            f"TASK:\n{mode_instruction}\n\n"
            "RENDERING INSTRUCTIONS:\n"
            "- Match the sentence length from the profile exactly. If they write short, write short.\n"
            "- Match the directness. If they own their statements, do not hedge.\n"
            "- Match the register. If they write peer-to-peer, do not write down to the reader.\n"
            "- Do not add warmth, polish, or formality that isn't already in the voice profile.\n"
            "- Do not smooth rough edges. The rough edges may be part of their voice.\n"
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
    Falls back to "uk" if inconclusive — Voxa is a UK product.
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
    return text
def _regex_sweep(text: str, keep_contractions: bool = False) -> str:
    """
    Deterministic guardrail sweep — runs on every render output.
    No API call. No Claude involvement. Code enforces these rules.

    1. Em dashes — all unicode variants replaced with hyphen
    2. Contractions expanded to full form — ONLY if the user's own baseline
       doesn't use them. This used to be unconditional (hardcoded to one
       person's writing convention). Fixed: contractions are a real voice
       marker, not a universal AI tell — stripping them from someone whose
       own writing uses them pushes the output away from their voice, not
       towards it.
    3. Claude literary closers stripped from paragraph endings
    4. Claude default constructions replaced
    5. Repeated words
    6. Double spaces
    7. Missing article fix
    """
    import re

    # 1. Em dashes — all variants
    for dash in ["\u2014", "\u2013", "&#8212;", "&#8211;", "\u2012", "\u2015", "—", "–", "‒"]:
        text = text.replace(dash, " - ")
    # Belt and braces — regex sweep for any remaining dash variants
    text = re.sub(r"[\u2012\u2013\u2014\u2015]", " - ", text)
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
    # Strip opening hedges — sentence start only, preserve mid-sentence ownership
    opener_hedge = re.compile(r'(?m)^I think (that )?', re.IGNORECASE)
    text = opener_hedge.sub('', text)
    opener_hedge2 = re.compile(r'(?m)^I believe (that )?', re.IGNORECASE)
    text = opener_hedge2.sub('', text)
    opener_hedge3 = re.compile(r'(?m)^I would argue (that )?', re.IGNORECASE)
    text = opener_hedge3.sub('', text)
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

    # 5. Repeated words
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)

    # 6. Double spaces
    text = re.sub(r'  +', ' ', text)

    # 7. Missing article fix
    article_needed = re.compile(
        r'\b(was not|is not|were not|are not|this was not|it was not|that was not)'
        r'\s+([bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]\w{2,})\b'
    )
    def _insert_article(m):
        verb_phrase = m.group(1)
        noun = m.group(2)
        no_article = {'clear', 'enough', 'simple', 'wrong', 'right', 'new',
                      'good', 'bad', 'free', 'ready', 'done', 'finished',
                      'certain', 'sure', 'possible', 'necessary', 'perfect',
                      'disaster', 'statement', 'mistake', 'accident',
                      'talent', 'quality', 'cohesion', 'progress', 'clarity',
                      'identity', 'momentum', 'confidence', 'rhythm', 'intent',
                      'pressure', 'direction', 'purpose', 'structure', 'balance'}
        if noun.lower() in no_article:
            return m.group(0)
        return f"{verb_phrase} a {noun}"
    text = article_needed.sub(_insert_article, text)

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


def build_correction_prompt(delta: dict, semantic: dict | None = None) -> str | None:
    """
    Builds the targeted, surgical correction prompt for whatever the
    initial render missed — voice dimensions (proven logic, unchanged)
    and, new in v4, dropped entities/facts caught by score_semantic_drift.
    Returns None if nothing needs correcting.
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
                    f"Only where the writer would genuinely be uncertain. Do not force it.")
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
            if o_val < b_val:
                correction_instructions.append(
                    f"Ownership is too low ({o_val:.0%} first-person, target {b_val:.0%}). "
                    f"Replace passive or third-person constructions with direct first-person statements. "
                    f"Own the points.")
        elif key == "directive_ratio" and b_val >= 0.06:
            if o_val < b_val:
                correction_instructions.append(
                    f"Directive pattern is missing ({o_val:.0%} action statements, target {b_val:.0%}). "
                    f"Convert 1-2 suggestions into direct action statements. No 'please', no 'could you'.")

    # New in v4 — semantic correction targets, parallel to voice correction
    dropped = (semantic or {}).get("dropped_entities", [])
    if dropped:
        named = ", ".join(dropped[:5])
        correction_instructions.append(
            f"The rewrite dropped specific facts from the original: {named}. "
            f"Add them back in naturally. Do not invent replacements — restore what was actually said.")

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

