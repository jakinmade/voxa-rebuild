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

Output cleanup: runs through voxa_core.text_guardrail.sweep() before
any comparison or self-check. Previously returned raw LLM output with
no cleanup at all — found during the August 2026 guardrail-
consolidation audit as the weakest of four independent output paths in
the codebase. See suggest_rewrite for the specific fix.
"""

from __future__ import annotations

import os

import anthropic

from voxa_core.text_guardrail import sweep as _sweep
from voxa_rendering.fingerprint import _extract_sentences

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 200


async def suggest_rewrite(
    sentence: str,
    dimension_label: str,
    profile_dimension: dict,
) -> tuple[str | None, str]:
    """
    Returns (rewritten_sentence_or_None, status).
    status is one of: "ok", "no_api_key", "api_error:<detail>"
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "no_api_key"

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
    except Exception as e:
        return None, f"api_error:{type(e).__name__}: {str(e)[:150]}"

    # Deterministic guardrail sweep — this endpoint previously returned
    # raw LLM output with ZERO cleanup, the only one of four output paths
    # in the codebase that did (found in the August 2026 guardrail-
    # consolidation audit: app.py had 12/12 steps, cleaner.py 2/12,
    # recalibrate.py ~4/12 and stale, this file 0/12). A single flagged
    # sentence is exactly where an em dash or a "leverage" is most likely
    # to slip straight through to the user, since nothing else in this
    # endpoint's path would ever catch it.
    rewritten = _sweep(rewritten)

    if not rewritten or rewritten == sentence:
        return None, "no_change_returned"

    return rewritten, "ok"


async def suggest_and_verify(
    sentence: str,
    dim_id: str,
    dimension_label: str,
    data_key: str,
    scorer_fn,
    profile_dimension: dict,
) -> tuple[str | None, str]:
    """
    Gets a rewrite, then checks it against the SAME scorer used everywhere
    else. Only returns the rewrite if it actually lands on the profile's
    baseline value for this dimension. This is the self-check: a
    suggestion is never shown unless it demonstrably fixes the thing
    it claims to fix.

    Returns (rewritten_or_None, status) - status is always meaningful,
    never silently swallowed, so a missing suggestion is diagnosable
    rather than a guess.
    """
    rewritten, status = await suggest_rewrite(sentence, dimension_label, profile_dimension)
    if rewritten is None:
        return None, status

    check_sentences = _extract_sentences(rewritten) or [rewritten]
    obs = scorer_fn(check_sentences, rewritten)
    rewritten_value = obs.data.get(data_key)

    if rewritten_value != profile_dimension.get("value"):
        return None, "self_check_failed"  # Rewrite didn't actually fix it - don't show it

    return rewritten, "ok"
