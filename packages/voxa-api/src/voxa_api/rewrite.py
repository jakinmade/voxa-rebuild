"""
Voxa — Line-Level Rewrite (Iteration 2)

Rewrites ONE flagged sentence closer to the user's voice profile.
Never rewrites a whole draft — that's a deliberate, documented decision
(Voxa_V1_Design_and_Build_Plan.docx, revised 25 July 2026), not an
oversight. Doing otherwise turns Voxa into a generator, which is
exactly the crowded space the checker was built to avoid.

Cost discipline (standing rule): minimum viable max_tokens, no retry,
no web search tool, one call per flagged line.

Self-check discipline: a suggestion is only returned if it actually
scores closer to the profile than the original on re-check. If Claude's
rewrite doesn't improve the match, we say so rather than pretend.
"""

from __future__ import annotations

import os

import anthropic

from voxa_rendering.fingerprint import _extract_sentences

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 200


async def suggest_rewrite(
    sentence: str,
    dimension_label: str,
    profile_dimension: dict,
) -> str | None:
    """
    Returns a rewritten version of `sentence`, or None if no API key is
    configured, the call fails, or the rewrite doesn't actually improve
    on the flagged dimension.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    direction = "direct and owns the statement" if profile_dimension.get("value") else "hedges and cushions the statement"
    prompt = (
        f"Rewrite ONLY this one sentence so it matches the writer's usual voice on "
        f"'{dimension_label}', where their established style {direction}. "
        f"Keep the same meaning and length as close as possible. "
        f"Return only the rewritten sentence, nothing else.\n\n"
        f'Sentence: "{sentence}"'
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        rewritten = response.content[0].text.strip().strip('"')
    except Exception:
        return None

    return rewritten if rewritten and rewritten != sentence else None


async def suggest_and_verify(
    sentence: str,
    dim_id: str,
    dimension_label: str,
    data_key: str,
    scorer_fn,
    profile_dimension: dict,
) -> str | None:
    """
    Gets a rewrite, then checks it against the SAME scorer used everywhere
    else. Only returns the rewrite if it actually lands on the profile's
    baseline value for this dimension. This is the self-check: a
    suggestion is never shown unless it demonstrably fixes the thing
    it claims to fix.
    """
    rewritten = await suggest_rewrite(sentence, dimension_label, profile_dimension)
    if rewritten is None:
        return None

    check_sentences = _extract_sentences(rewritten) or [rewritten]
    obs = scorer_fn(check_sentences, rewritten)
    rewritten_value = obs.data.get(data_key)

    if rewritten_value != profile_dimension.get("value"):
        return None  # Didn't actually fix it - don't show a suggestion that fails its own check

    return rewritten
