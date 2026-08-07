"""
Voxa — Deterministic Output Cleaning (Layer 3 support)

Also referenced by engine.py's render() pipeline but never built.
Code enforcement, not a prompt instruction — runs on every render
output regardless of what the LLM produced, same principle as the
live app's _regex_sweep in prompts.py.

This is a self-contained subset port, not a call into prompts.py:
voxa_rendering is a separate, independently-installable package
(see pyproject.toml) and shouldn't take a hard dependency on the
root-level Streamlit app module. The two universal, non-parameterised
cleanups are ported here (em dash removal, Claude default-construction
replacement). The live sweep's contraction-expansion step is
deliberately NOT ported: it depends on a per-user keep_contractions
flag derived from the profile's own baseline, which clean_render_output
doesn't have access to at this layer, and guessing a default would
silently override the user's actual voice baseline rather than respect
it. If this package is ever wired to real profiles, contraction
handling belongs in engine.py where the profile is in scope, not here.
"""

from __future__ import annotations

import re

_DASH_VARIANTS = ["\u2014", "\u2013", "\u2012", "\u2015", "—", "–", "‒"]

# Mirrors claude_constructions in prompts.py's _regex_sweep — kept as a
# separate list here (not imported) to avoid coupling this package to
# the root-level app module. Update both if the list changes.
_CLAUDE_CONSTRUCTIONS: list[tuple[str, str]] = [
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


def clean_render_output(text: str) -> str:
    """
    Deterministic guardrail sweep — runs on every render output.
    No API call. No LLM involvement. Code enforces these rules.

    1. Em dashes — all unicode variants replaced with a hyphen
    2. Claude default constructions replaced

    See module docstring for why contraction handling isn't included
    at this layer.
    """
    if not text:
        return text

    for dash in _DASH_VARIANTS:
        text = text.replace(dash, " - ")
    text = re.sub(r"[\u2012\u2013\u2014\u2015]", " - ", text)
    text = re.sub(r"  +", " ", text)

    for pattern, replacement in _CLAUDE_CONSTRUCTIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text
