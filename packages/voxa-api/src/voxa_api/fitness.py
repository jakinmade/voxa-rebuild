"""
Voxa — Sample Fitness Gate

Ported from the original Streamlit build (app.py), which JA correctly
flagged as having a genuinely better-thought-through onboarding layer
than the first pass of the FastAPI checker. Kept close to verbatim —
this logic was already good, the job here is reuse, not rewrite.

Scores a pasted writing sample on three dimensions before it's allowed
to shape a voice profile:
  SPONTANEITY — unguarded, natural writing (idiolect lives here)
  SPECIFICITY — concrete, named, real details (what AI cannot fake)
  OWNERSHIP   — first-person, accountable, self-authored

A weak or generic sample gets a specific, actionable nudge rather than
a flat rejection - "paste an email you sent to someone you know", not
"try again".
"""

from __future__ import annotations

import math
import re


def score_sample_fitness(text: str) -> dict:
    words = text.split()
    total_words = max(len(words), 1)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip() and len(s.split()) >= 2]
    total_sents = max(len(sentences), 1)

    # SPONTANEITY (0-35)
    spontaneity = 0
    if len(sentences) >= 3:
        lengths = [len(s.split()) for s in sentences]
        avg = sum(lengths) / len(lengths)
        sd = math.sqrt(sum((l - avg) ** 2 for l in lengths) / len(lengths))
        if sd >= 8:
            spontaneity += 12
        elif sd >= 5:
            spontaneity += 8
        elif sd >= 3:
            spontaneity += 4

    subject_drop = re.compile(
        r'^(Will|Can|Could|Would|Pls|Please|Am|Have|Had|Apologies|Thanks|Noted|Confirmed)\b',
        re.IGNORECASE
    )
    drop_count = sum(1 for s in sentences if subject_drop.match(s.strip()))
    if drop_count >= 2:
        spontaneity += 8
    elif drop_count >= 1:
        spontaneity += 4

    shorthand = re.compile(r'\b(pls|btw|fyi|asap|tbc|tbd|re:|etc|vs)\b', re.IGNORECASE)
    sc = len(shorthand.findall(text))
    if sc >= 2:
        spontaneity += 8
    elif sc >= 1:
        spontaneity += 4

    if re.search(r'(\.\.|  |\.\s+[a-z])', text):
        spontaneity += 4

    if re.search(r'\b(hopefully|pls let|let me know|happy to|regards,|cheers,|best,|thanks,)\b', text, re.IGNORECASE):
        spontaneity += 3

    spontaneity = min(spontaneity, 35)

    # SPECIFICITY (0-35)
    specificity = 0
    non_proper = {'The', 'This', 'That', 'These', 'Those', 'They', 'Their', 'There', 'When',
                  'What', 'Which', 'Where', 'Who', 'How', 'And', 'But', 'For', 'With', 'From',
                  'Also', 'Some', 'Have', 'Been', 'Will', 'Would', 'Could', 'Should', 'Just',
                  'Still', 'Even', 'Here', 'Very', 'More', 'Most', 'Into', 'Over', 'After',
                  'About', 'Such', 'Each', 'Both', 'Only', 'Then', 'Than', 'Same', 'Another'}
    proper_nouns = [w for w in re.findall(r'(?<=[.!? ])[A-Z][a-z]{2,}', text) if w not in non_proper]
    unique_proper = len(set(proper_nouns))
    if unique_proper >= 5:
        specificity += 15
    elif unique_proper >= 3:
        specificity += 10
    elif unique_proper >= 1:
        specificity += 5

    number_count = len(re.findall(r'\b\d+[\d,.]*\b', text))
    if number_count >= 3:
        specificity += 10
    elif number_count >= 1:
        specificity += 5

    shared = len(re.findall(
        r'\b(the (meeting|call|proposal|project|report|issue|deal|team|client|product|platform|system))\b',
        text, re.IGNORECASE
    ))
    if shared >= 2:
        specificity += 10
    elif shared >= 1:
        specificity += 5

    specificity = min(specificity, 35)

    # OWNERSHIP (0-30)
    ownership = 0
    fp = re.compile(r'\b(I|me|my|mine|myself)\b', re.IGNORECASE)
    fp_sents = sum(1 for s in sentences if fp.search(s))
    fp_ratio = fp_sents / total_sents
    if fp_ratio >= 0.5:
        ownership += 12
    elif fp_ratio >= 0.3:
        ownership += 8
    elif fp_ratio >= 0.15:
        ownership += 4

    denial = re.compile(
        r"\b(I do not|I am not|I don't|I'm not|That is not|This is not|We do not)\b",
        re.IGNORECASE
    )
    dc = len(denial.findall(text))
    if dc >= 2:
        ownership += 10
    elif dc >= 1:
        ownership += 6

    if re.search(
        r"\b(I have (just|been|become|realised|decided)|I was|I became|I struggle|to be honest)\b",
        text, re.IGNORECASE
    ):
        ownership += 8

    ownership = min(ownership, 30)

    total = spontaneity + specificity + ownership
    wc = len(words)
    if wc < 100:
        total = int(total * 0.5)
        wc_note = "very short"
    elif wc < 200:
        total = int(total * 0.75)
        wc_note = "short"
    elif wc < 400:
        total = int(total * 0.9)
        wc_note = "good length"
    else:
        wc_note = "strong length"

    if total >= 75:
        tier = "gold"
    elif total >= 55:
        tier = "strong"
    elif total >= 35:
        tier = "thin"
    else:
        tier = "weak"

    nudge = None
    if tier in ("thin", "weak"):
        if specificity < 10 and ownership < 10:
            nudge = "Paste an email you sent to someone you know. Something with names, real context, not a formal document."
        elif specificity < 10:
            nudge = "Paste something with real names and specific context — an email to a colleague about an actual project."
        elif ownership < 10:
            nudge = "Paste something written in your own voice — where you say what you think, not what sounds professional."
        elif spontaneity < 10:
            nudge = "Paste something you wrote quickly without re-reading — a message or email dashed off on your phone."
        else:
            nudge = "Paste one more piece of your own writing to sharpen the fingerprint."

    return {
        "score": total, "tier": tier,
        "spontaneity": spontaneity, "specificity": specificity, "ownership": ownership,
        "word_count": wc, "wc_note": wc_note, "nudge": nudge,
    }


def fitness_gate(fitness: dict, cumulative_words: int, cumulative_docs: int) -> dict:
    """Decides whether the profile is ready, needs a specific nudge, or just needs more."""
    tier = fitness["tier"]
    wc = fitness["word_count"]
    nudge = fitness["nudge"]

    if tier in ("gold", "strong"):
        return {"action": "ready", "confidence": "high" if tier == "gold" else "medium", "message": None}
    if tier == "thin" and wc >= 150:
        return {"action": "ready", "confidence": "provisional", "message": None}
    if cumulative_words >= 250:
        return {"action": "ready", "confidence": "provisional", "message": None}
    if nudge:
        return {"action": "nudge", "confidence": "provisional", "message": nudge}
    return {
        "action": "accumulate", "confidence": "provisional",
        "message": "Paste one more piece of your writing to complete your profile.",
    }
