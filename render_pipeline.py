"""
render_pipeline.py — the pure generation/correction/scoring core,
extracted from app.py's _run_render (5 Sept 2026).

Functional-core, imperative-shell split: this module is the "core" —
every input is an explicit parameter, every output is returned in
RenderResult, nothing here reads or writes st.session_state, and
nothing here is Streamlit-specific. app.py's _run_render becomes the
"shell": it resolves Streamlit-specific concerns (device cookie,
st.secrets, the free-tier lifetime/daily render caps, st.spinner UI
feedback, and writing the result into session_state / render history /
the persisted voice-profile summary), then delegates the actual
generation work here. api/routes/fix.py is the second shell, calling
the exact same core with its own concerns (render-credit accounting,
evidence sealing, telemetry) layered on top instead.

This is a mechanical extraction, not a rewrite: every line of actual
logic below is unchanged from _run_render — same order of operations,
same fixers, same correction passes, same fail-closed behaviour. What
changed is only what removing st.session_state required:

  - device_id, the lifetime/daily render caps, and st.secrets/env
    resolution are gone entirely — those are caller-side concerns
    (billing/entitlement), never part of what this function decides.
  - observations, locale, sample_fitness, and dimension_stability are
    no longer read from a cache — they're cheap, deterministic
    functions of raw_text/baseline_texts (confirmed: analyse_writing,
    _detect_locale, _score_sample_fitness, compute_dimension_stability
    make no LLM calls and have no other hidden inputs), so they're
    recomputed fresh on every call instead. This trades a small amount
    of CPU for not needing a second, parallel cache to keep in sync.
  - _run_fabrication_correction_pass moved here too (it was a
    same-file helper in app.py, already fully parameterised except for
    one st.spinner call) — its own docstring explains why it lives
    alongside code allowed to call the LLM (prompts.py/
    deterministic_fixers.py explicitly rule that out) rather than as a
    shared module-level function; this module is the new home that
    satisfies the same rule. dev_tools/harness.py keeps its own
    parallel copy, per that docstring's existing note — unchanged by
    this extraction.
  - Every st.spinner(...) call becomes an on_stage(name) callback
    invocation. Optional and a no-op by default — the API path never
    supplies one; app.py's wrapper supplies one that drives its
    existing st.spinner text, so the website's UX is unchanged.

See RenderResult for the complete, explicit set of outputs this used
to scatter across a dozen separate st.session_state keys.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from logging_config import get_logger
from voice_engine import (
    analyse_writing,
    compute_baseline_metrics,
    _score_sample_fitness,
    _score_ai_signal,
    score_semantic_drift,
    compute_confidence,
    compute_risk,
    compute_risk_reason,
    has_content_integrity_hard_fail,
    score_render_delta,
    build_voice_report,
    uses_contractions,
    score_ai_tells,
    score_restructure_fidelity,
    compute_dimension_stability,
    compute_burrows_delta,
    compute_sentence_economy,
    compute_passive_voice,
)
from prompts import (
    _build_voice_dna, _build_system_prompt,
    _detect_mode, apply_intent_mode, _detect_locale,
    _apply_uk_english, _regex_sweep, _grammar_fix_pass,
    build_correction_prompt, merge_starter_evidence,
    build_voice_profile_summary_prompt, uses_em_dashes,
    CORRECTION_TOOL, response_looks_contaminated,
    build_fabrication_correction_prompt,
)
from deterministic_fixers import (
    _fix_hedge_density, _fix_sentence_length_sd,
    _fix_first_person_ratio, _fix_first_person_over_ratio,
    _fix_directive_ratio, _fix_modal_hedge, _fix_scaffolding_density,
    _check_uncorrected_insertions, _fix_entity_casing,
    ownership_miss_is_content_driven, restore_fabricated_ownership_sentences,
    get_fabricated_blocks,
)

log = get_logger(__name__)


@dataclass
class RenderResult:
    """Everything _run_render used to write into st.session_state,
    returned explicitly instead. success=False means the caller should
    treat this exactly as a False return from the old _run_render —
    error holds a user-facing message, nothing else is populated."""
    success: bool
    error: str | None = None
    output_text: str | None = None
    intent_mode: str | None = None
    restructure_declined: bool = False
    # Set only when this call generated a NEW voice-profile summary
    # (the lazy-generation path) — the caller decides how/whether to
    # persist it (Streamlit: save_profile_if_available(); API: a
    # targeted profile update). None means nothing new to persist.
    voice_profile_summary_generated: str | None = None
    insertion_check: dict | None = None
    keep_contractions: bool | None = None
    keep_dashes: bool | None = None
    delta: dict | None = None
    semantic_drift: dict | None = None
    confidence: dict | None = None
    risk: dict | None = None
    risk_reason: str | None = None
    ai_tells: dict | None = None
    burrows_delta: dict | None = None
    voice_report: dict | None = None
    render_id: str | None = None
    render_completed_at: str | None = None
    content_integrity_hard_fail: bool = False
    correction_tier: str | None = None


def _generate_voice_profile_summary(corpus_text: str, api_key: str) -> str | None:
    """
    Moved from app.py (5 Sept 2026 extraction) — see module docstring.
    Only change from the original: api_key is now a parameter (the
    caller already resolved it) rather than re-resolved here via
    os.environ/st.secrets — st.secrets doesn't exist outside a running
    Streamlit script, and re-resolving here would just be a redundant
    second lookup of a value the caller already has.

    One-time distillation call: condenses a person's raw writing corpus
    into a short natural-language profile of their distinctive habits.
    See build_voice_profile_summary_prompt's own docstring for the
    research basis — generating from a distilled profile measurably
    outperforms generating from raw context directly.

    Cost guardrail, per standing rule, checked before this was built:
    minimum viable max_tokens (200 — this only needs to hold 3-5
    sentences), no auto-retry on failure, cached rather than
    regenerated on every render (caller's responsibility — see
    run_voice_render's lazy-generation call site below).

    Returns None on any failure — this is a quality enhancement, not
    a required part of the pipeline. A render with no distilled
    profile falls back to exactly what already existed before this
    feature: anchor sentences and numeric targets alone.
    """
    if not api_key or not corpus_text or not corpus_text.strip():
        return None

    import anthropic
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=200, temperature=0,
            system=build_voice_profile_summary_prompt(),
            messages=[{"role": "user", "content": corpus_text}],
        )
        summary = response.content[0].text.strip()
        # Same deterministic backstop the render output gets (em dashes,
        # corporate filler, verbose openers, Claude-isms) - the prompt
        # guardrails above are the first line of defence, this is the
        # second. keep_contractions=True: this is a synthesised
        # description, not a copy of the person's own words, so there's
        # no baseline to check contraction usage against either way.
        summary = _regex_sweep(summary, keep_contractions=True)
        log.info("voice_profile_summary_generated", summary_length=len(summary))
        return summary
    except Exception:
        log.error("voice_profile_summary_generation_failed", exc_info=True)
        return None


def _run_fabrication_correction_pass(
    client, input_text: str, clean: str, insertion_check: dict,
    keep_contractions: bool, keep_dashes: bool, locale: str,
    on_stage: Callable[[str], None] | None = None,
) -> tuple:
    """Moved verbatim from app.py (see module docstring) — only the
    st.spinner call became an on_stage callback. See the original
    call site's extensive docstring history in git blame for the full
    rationale; unchanged here.

    Fails closed: any exception, no usable candidate, or a re-check
    that doesn't show STRICT improvement over the passed-in
    insertion_check returns the inputs unchanged and applied=False.

    Returns (clean, insertion_check, applied).
    """
    if insertion_check.get("sentence_growth", 0) <= 0:
        return clean, insertion_check, False

    flagged_blocks = get_fabricated_blocks(input_text, clean)
    if not flagged_blocks:
        return clean, insertion_check, False

    try:
        fabrication_prompt = build_fabrication_correction_prompt(input_text, flagged_blocks)
        fixed_candidate = None
        for attempt in range(2):
            if on_stage:
                on_stage("checking_invented_content")
            fab_response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=4096, temperature=0,
                system=fabrication_prompt,
                messages=[{"role": "user", "content": clean}],
                tools=[CORRECTION_TOOL],
                tool_choice={"type": "tool", "name": "return_correction"},
            )
            tool_use_block = next(
                (b for b in fab_response.content if b.type == "tool_use"),
                None,
            )
            if tool_use_block is None:
                log.error(
                    "fabrication_pass_no_tool_use",
                    attempt=attempt,
                    stop_reason=fab_response.stop_reason,
                )
                continue
            candidate = tool_use_block.input.get("corrected_text", "")
            if candidate and not response_looks_contaminated(candidate):
                fixed_candidate = candidate
                break
            log.error(
                "fabrication_pass_contaminated_response",
                attempt=attempt,
                candidate_preview=candidate[:200],
            )
        if not fixed_candidate:
            log.error("fabrication_pass_failed_both_attempts")
            return clean, insertion_check, False

        fixed_candidate = _regex_sweep(
            fixed_candidate, keep_contractions=keep_contractions,
            original_input_text=input_text, keep_dashes=keep_dashes,
        )
        if locale == "uk":
            fixed_candidate = _apply_uk_english(fixed_candidate)
        recheck = _check_uncorrected_insertions(input_text, fixed_candidate)
        log.info(
            "fabrication_pass_result",
            pre_sentence_growth=insertion_check["sentence_growth"],
            post_sentence_growth=recheck["sentence_growth"],
            cleared=recheck["sentence_growth"] == 0,
        )
        if recheck["sentence_growth"] < insertion_check["sentence_growth"]:
            return fixed_candidate, recheck, True
        log.error("fabrication_pass_did_not_improve")
        return clean, insertion_check, False
    except Exception:
        log.error("fabrication_pass_llm_failed", exc_info=True)
        return clean, insertion_check, False


def run_voice_render(
    *,
    input_text: str,
    api_key: str,
    raw_text: str,
    sample2_completions: list[str],
    baseline: dict | None,
    baseline_texts: list[str],
    voice_profile_summary: str | None = None,
    starter_baseline: dict | None = None,
    baseline_fingerprints_by_format: dict | None = None,
    render_mode: str = "preserve",
    render_context: str = "",
    platform_format: str | None = None,
    is_refinement: bool = False,
    on_stage: Callable[[str], None] | None = None,
) -> RenderResult:
    """The actual generation pipeline, callable outside Streamlit.

    Caller is responsible for (none of this happens here, all
    unchanged in behaviour from before this extraction, just moved to
    whichever shell is calling in):
      - resolving api_key (env / st.secrets / wherever else)
      - the lifetime/daily render-cap check-and-reserve, and releasing
        a reserved render on failure
      - persisting voice_profile_summary_generated if set
      - writing render history
      - anything session-state or HTTP-response shaped

    render_mode / platform_format / is_refinement: same meaning as
    _run_render's own docstring (build_correction_prompt's mode
    parameter; opt-in social/email line-editing; whether this is the
    one-time included refinement of an already-rendered original).
    """
    if not api_key:
        return RenderResult(success=False, error="API key missing.")

    import anthropic

    detected_mode = _detect_mode(input_text)
    log.info(
        "render_start", input_words=len(input_text.split()),
        is_refinement=is_refinement, detected_mode=detected_mode,
    )

    # Recomputed fresh, not read from a cache — see module docstring.
    # locale, observations, and sample_fitness are all pure functions
    # of raw_text alone; user_uses_em_dashes reads raw_text directly,
    # same as the original.
    locale = _detect_locale(raw_text) if raw_text else "uk"
    user_uses_em_dashes = len(re.findall(r"[—–\u2014\u2013]", raw_text)) > 0 if raw_text else False
    ai_score = _score_ai_signal(input_text, user_uses_em_dashes=user_uses_em_dashes)
    observations = analyse_writing(raw_text) if raw_text else []
    sample_fitness = _score_sample_fitness(raw_text) if raw_text else None
    # fingerprint_samples: per-sample metrics feeding dimension
    # stability — recomputed the same way app.py accumulates them
    # during onboarding (one compute_baseline_metrics per sample),
    # just done fresh here from baseline_texts instead of a session
    # cache.
    fingerprint_samples = [compute_baseline_metrics(t) for t in baseline_texts] if baseline_texts else []
    dimension_stability = compute_dimension_stability(fingerprint_samples) if fingerprint_samples else None

    # Per-register baseline (30 Aug 2026): unchanged logic, just reads
    # a parameter instead of session_state.
    if platform_format:
        by_format = baseline_fingerprints_by_format or {}
        format_baseline = by_format.get(platform_format)
        if format_baseline and format_baseline.get("word_count", 0) >= 800:
            baseline = format_baseline

    fingerprint_corpus = raw_text + " " + " ".join(sample2_completions or [])
    fingerprint_corpus = fingerprint_corpus.strip()

    # Lazy voice-profile-summary generation — same trigger as before
    # (first render, no summary yet), but the actual persistence is
    # now the caller's job (see RenderResult.voice_profile_summary_generated).
    generated_summary = None
    if baseline and not voice_profile_summary:
        generated_summary = _generate_voice_profile_summary(fingerprint_corpus or raw_text, api_key)
        if generated_summary:
            voice_profile_summary = generated_summary

    voice_dna = _build_voice_dna(observations, fingerprint_corpus or raw_text, baseline, ai_score, current_input_text=input_text)
    mode_instruction = apply_intent_mode(input_text, detected_mode)
    word_count_input = len(input_text.split())

    keep_contractions = (
        (uses_contractions(fingerprint_corpus) if fingerprint_corpus else False)
        or uses_contractions(input_text)
    )
    _dash_evidence = f"{fingerprint_corpus} {input_text}" if fingerprint_corpus else input_text
    keep_dashes = uses_em_dashes(_dash_evidence)

    input_metrics_signal = compute_baseline_metrics(input_text)
    input_has_opinion_content = input_metrics_signal["first_person_ratio"] > 0
    input_has_directive_content = input_metrics_signal["directive_ratio"] > 0

    system = _build_system_prompt(
        voice_dna=voice_dna, mode_instruction=mode_instruction,
        word_count_input=word_count_input, ai_score=ai_score, baseline=baseline,
        input_text=input_text, render_context=render_context,
        voice_profile_summary=voice_profile_summary or "",
        platform_format=platform_format,
        locale=locale,
    )

    client = anthropic.Anthropic(api_key=api_key)
    restructure_declined = False
    try:
        if on_stage:
            on_stage("writing")
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4096, temperature=0,
            system=system, messages=[{"role": "user", "content": input_text}],
        )
        clean = response.content[0].text
        clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
        if locale == "uk":
            clean = _apply_uk_english(clean)
        clean = _grammar_fix_pass(clean, client, locale=locale, original_input_text=input_text)
        clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
    except Exception:
        log.error("render_failed", reason="llm_call_exception", stage="initial_render", exc_info=True)
        return RenderResult(success=False, error="That didn't go through. Your text is safe, try again.")

    clean, casing_restored, casing_still_dropped = _fix_entity_casing(clean, input_text)
    if casing_restored:
        log.info("entity_casing_restored", restored=casing_restored, still_dropped=casing_still_dropped)

    initial_insertion_check = _check_uncorrected_insertions(input_text, clean)
    log.info(
        "initial_render_insertion_check",
        new_hedges=initial_insertion_check["new_hedges"],
        sentence_growth=initial_insertion_check["sentence_growth"],
        flagged=initial_insertion_check["flagged"],
    )

    clean, initial_insertion_check, _ = _run_fabrication_correction_pass(
        client, input_text, clean, initial_insertion_check,
        keep_contractions=keep_contractions, keep_dashes=keep_dashes,
        locale=locale, on_stage=on_stage,
    )

    hedge_fixed = modal_fixed = rhythm_fixed = ownership_fixed = directive_fixed = False
    correction_prompt = None
    delta = None
    semantic = None
    ai_tells = {"clean": True, "flagged": []}
    insertion_check = initial_insertion_check
    correction_tier = "none"

    if baseline:
        delta = score_render_delta(baseline, clean)
        semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)

        starter_delta = score_render_delta(starter_baseline, clean) if starter_baseline else None
        correction_delta = merge_starter_evidence(delta, starter_delta)

        if correction_delta.get("hedge_density", {}).get("verdict") == "MISSED":
            d = correction_delta["hedge_density"]
            clean, hedge_fixed = _fix_hedge_density(clean, d["baseline"], d["output"])
            clean, modal_fixed = _fix_modal_hedge(clean, d["baseline"], d["output"])
        if correction_delta.get("sentence_length_sd", {}).get("verdict") == "MISSED":
            d = correction_delta["sentence_length_sd"]
            clean, rhythm_fixed = _fix_sentence_length_sd(clean, d["baseline"], d["output"])
        if correction_delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            d = correction_delta["first_person_ratio"]
            clean, ownership_fixed = _fix_first_person_ratio(
                clean, d["baseline"], d["output"], input_has_opinion_content
            )
            clean, ownership_over_fixed = _fix_first_person_over_ratio(
                clean, d["baseline"], d["output"], input_text
            )
            clean, ownership_restored = restore_fabricated_ownership_sentences(clean, input_text)
            ownership_fixed = ownership_fixed or ownership_over_fixed or ownership_restored
        if correction_delta.get("directive_ratio", {}).get("verdict") == "MISSED":
            d = correction_delta["directive_ratio"]
            clean, directive_fixed = _fix_directive_ratio(
                clean, d["baseline"], d["output"], input_has_directive_content
            )
        if correction_delta.get("scaffolding_density", {}).get("verdict") == "MISSED":
            d = correction_delta["scaffolding_density"]
            clean, scaffolding_fixed = _fix_scaffolding_density(clean, d["baseline"], d["output"])
        else:
            scaffolding_fixed = False
        log.info(
            "deterministic_fixers_applied",
            hedge_density=hedge_fixed, modal_hedge=modal_fixed,
            sentence_length_sd=rhythm_fixed, first_person_ratio=ownership_fixed,
            directive_ratio=directive_fixed, scaffolding_density=scaffolding_fixed,
        )

        clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
        if locale == "uk":
            clean = _apply_uk_english(clean)
        delta = score_render_delta(baseline, clean)
        semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)
        starter_delta = score_render_delta(starter_baseline, clean) if starter_baseline else None
        correction_delta = merge_starter_evidence(delta, starter_delta)

        sentence_economy = None
        passive_voice = None
        if render_mode == "elevate":
            sentence_economy = compute_sentence_economy(clean)
            passive_voice = compute_passive_voice(clean)

        correction_prompt = build_correction_prompt(
            correction_delta, semantic, input_has_opinion_content, input_has_directive_content,
            mode=render_mode, sentence_economy=sentence_economy, passive_voice=passive_voice,
            platform_format=platform_format,
            locale=locale,
            user_uses_em_dashes=user_uses_em_dashes,
        )
        log.info(
            "correction_pass_decision",
            llm_correction_needed=bool(correction_prompt),
            missed_dimensions=[k for k, d in correction_delta.items() if d["verdict"] == "MISSED"],
        )
        insertion_check = initial_insertion_check
        if correction_prompt:
            try:
                pre_llm_correction = clean
                corrected = None
                for attempt in range(2):
                    if on_stage:
                        on_stage("refining")
                    correction_response = client.messages.create(
                        model="claude-sonnet-4-6", max_tokens=4096, temperature=0,
                        system=correction_prompt,
                        messages=[{"role": "user", "content": clean}],
                        tools=[CORRECTION_TOOL],
                        tool_choice={"type": "tool", "name": "return_correction"},
                    )
                    tool_use_block = next(
                        (b for b in correction_response.content if b.type == "tool_use"),
                        None,
                    )
                    if tool_use_block is None:
                        log.error(
                            "correction_pass_no_tool_use",
                            attempt=attempt,
                            stop_reason=correction_response.stop_reason,
                        )
                        continue
                    candidate = tool_use_block.input.get("corrected_text", "")
                    if candidate and not response_looks_contaminated(candidate):
                        corrected = candidate
                        break
                    log.error(
                        "correction_pass_contaminated_response",
                        attempt=attempt,
                        candidate_preview=candidate[:200],
                    )
                if corrected is None:
                    log.error("correction_pass_failed_both_attempts")
                    corrected = pre_llm_correction
                corrected = _regex_sweep(corrected, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
                if locale == "uk":
                    corrected = _apply_uk_english(corrected)
                clean = corrected

                if platform_format in ("social", "email"):
                    fidelity = score_restructure_fidelity(pre_llm_correction, clean)
                    if not fidelity["clean"]:
                        log.error(
                            "platform_restructure_fidelity_failed",
                            platform_format=platform_format,
                            fabricated_words=fidelity["fabricated_words"],
                        )
                        clean = pre_llm_correction
                        restructure_declined = True

                correction_insertion_check = _check_uncorrected_insertions(pre_llm_correction, clean)
                insertion_check = {
                    "new_hedges": insertion_check["new_hedges"] + correction_insertion_check["new_hedges"],
                    "sentence_growth": insertion_check["sentence_growth"] + correction_insertion_check["sentence_growth"],
                    "flagged": insertion_check["flagged"] or correction_insertion_check["flagged"],
                }
                if correction_insertion_check["new_hedges"]:
                    new_hedge_count = len(correction_insertion_check["new_hedges"])
                    clean, _ = _fix_hedge_density(clean, 0, new_hedge_count)
                    clean, _ = _fix_modal_hedge(clean, 0, new_hedge_count)
                log.info(
                    "correction_pass_side_effect_caught",
                    new_hedges=correction_insertion_check["new_hedges"],
                    sentence_growth=correction_insertion_check["sentence_growth"],
                    flagged=correction_insertion_check["flagged"],
                )

                full_check = _check_uncorrected_insertions(input_text, clean)
                clean, full_check, _ = _run_fabrication_correction_pass(
                    client, input_text, clean, full_check,
                    keep_contractions=keep_contractions, keep_dashes=keep_dashes,
                    locale=locale, on_stage=on_stage,
                )
                insertion_check = full_check

                delta = score_render_delta(baseline, clean)
                semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)

                if delta.get("scaffolding_density", {}).get("verdict") == "MISSED":
                    d = delta["scaffolding_density"]
                    clean, scaffolding_refixed = _fix_scaffolding_density(clean, d["baseline"], d["output"])
                    if scaffolding_refixed:
                        log.info("scaffolding_density_reintroduced_and_refixed")
                        delta = score_render_delta(baseline, clean)
            except Exception:
                log.error("correction_pass_llm_failed", stage="correction", exc_info=True)

        still_missed = [k for k, d in delta.items() if d["verdict"] == "MISSED"]
        if still_missed:
            if "hedge_density" in still_missed:
                d = delta["hedge_density"]
                clean, _ = _fix_hedge_density(clean, d["baseline"], d["output"])
                clean, _ = _fix_modal_hedge(clean, d["baseline"], d["output"])
            if "sentence_length_sd" in still_missed:
                d = delta["sentence_length_sd"]
                clean, _ = _fix_sentence_length_sd(clean, d["baseline"], d["output"])
            if "first_person_ratio" in still_missed:
                d = delta["first_person_ratio"]
                clean, _ = _fix_first_person_ratio(
                    clean, d["baseline"], d["output"], input_has_opinion_content
                )
                clean, _ = _fix_first_person_over_ratio(
                    clean, d["baseline"], d["output"], input_text
                )
                clean, _ = restore_fabricated_ownership_sentences(clean, input_text)
            if "directive_ratio" in still_missed:
                d = delta["directive_ratio"]
                clean, _ = _fix_directive_ratio(
                    clean, d["baseline"], d["output"], input_has_directive_content
                )
            if "scaffolding_density" in still_missed:
                d = delta["scaffolding_density"]
                clean, _ = _fix_scaffolding_density(clean, d["baseline"], d["output"])
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
            if locale == "uk":
                clean = _apply_uk_english(clean)
            delta = score_render_delta(baseline, clean)
            semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)
            log.info(
                "post_correction_verify_pass",
                still_missed_before=still_missed,
                still_missed_after=[k for k, d in delta.items() if d["verdict"] == "MISSED"],
            )

        ai_tells = score_ai_tells(clean, original_input_text=input_text, calibration_text=fingerprint_corpus or "")
        if not ai_tells["clean"]:
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text, keep_dashes=keep_dashes)
            ai_tells = score_ai_tells(clean, original_input_text=input_text, calibration_text=fingerprint_corpus or "")

        if not input_has_opinion_content and delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            delta["first_person_ratio"]["verdict"] = "SKIPPED"
            delta["first_person_ratio"]["skip_reason"] = "no_content"
        if not input_has_directive_content and delta.get("directive_ratio", {}).get("verdict") == "MISSED":
            delta["directive_ratio"]["verdict"] = "SKIPPED"
            delta["directive_ratio"]["skip_reason"] = "no_content"

        if delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            if ownership_miss_is_content_driven(clean, input_text):
                delta["first_person_ratio"]["verdict"] = "SKIPPED"
                delta["first_person_ratio"]["skip_reason"] = "content_ceiling"

        confidence = compute_confidence(sample_fitness, baseline, len(observations), dimension_stability)
        risk = compute_risk(delta, semantic, ai_tells, insertion_check)
        risk_reason = compute_risk_reason(delta, semantic, ai_tells, insertion_check)
        content_integrity_hard_fail = has_content_integrity_hard_fail(semantic, ai_tells, insertion_check)

        log.info(
            "render_complete", is_refinement=is_refinement,
            confidence=confidence.get("level") if isinstance(confidence, dict) else confidence,
            risk=risk.get("level") if isinstance(risk, dict) else risk,
            risk_reason=risk_reason,
            ai_tells_clean=ai_tells["clean"],
            missed_dimensions=[k for k, d in delta.items() if d["verdict"] == "MISSED"],
        )

        if content_integrity_hard_fail:
            correction_tier = "hard_fail"
        elif correction_prompt:
            correction_tier = "llm_correction"
        elif hedge_fixed or modal_fixed or rhythm_fixed or ownership_fixed or directive_fixed:
            correction_tier = "deterministic_only"
        else:
            correction_tier = "none"
        log_render_event_kwargs = dict(
            risk=risk.get("level") if isinstance(risk, dict) else risk,
            risk_reason=risk_reason,
            semantic_match=semantic.get("semantic_match") if semantic else None,
            missed_dimensions=sum(1 for d in delta.values() if d["verdict"] == "MISSED"),
            ai_tells_clean=ai_tells["clean"],
            is_refinement=is_refinement,
            correction_tier=correction_tier,
        )
        try:
            from render_events import log_render_event
            from scoring_rules import scoring_rules_version
            log_render_event(scoring_rules_version=scoring_rules_version(), **log_render_event_kwargs)
        except Exception:
            log.error("log_render_event_failed", exc_info=True)

        burrows_delta = compute_burrows_delta(baseline_texts or [], clean)

        voice_report = build_voice_report(
            delta, semantic, confidence, risk, ai_tells, burrows_delta,
            content_integrity_hard_fail=content_integrity_hard_fail,
        )
        render_id = str(uuid.uuid4())
        render_completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return RenderResult(
            success=True,
            output_text=clean,
            intent_mode=detected_mode,
            restructure_declined=restructure_declined,
            voice_profile_summary_generated=generated_summary,
            insertion_check=insertion_check,
            keep_contractions=keep_contractions,
            keep_dashes=keep_dashes,
            delta=delta,
            semantic_drift=semantic,
            confidence=confidence,
            risk=risk,
            risk_reason=risk_reason,
            ai_tells=ai_tells,
            burrows_delta=burrows_delta,
            voice_report=voice_report,
            render_id=render_id,
            render_completed_at=render_completed_at,
            content_integrity_hard_fail=content_integrity_hard_fail,
            correction_tier=correction_tier,
        )

    # No baseline at all — generation still happened above, but there
    # is nothing to score against. Matches the original function's
    # else-branch: every scoring/report field comes back empty rather
    # than a real-looking-but-meaningless report.
    return RenderResult(
        success=True,
        output_text=clean,
        intent_mode=detected_mode,
        restructure_declined=restructure_declined,
        voice_profile_summary_generated=generated_summary,
        insertion_check=None,
        keep_contractions=keep_contractions,
        keep_dashes=keep_dashes,
    )
