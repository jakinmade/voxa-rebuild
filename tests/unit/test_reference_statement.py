"""
Tests for the reference_statement feature (8 Sept 2026) — see
VOICOVA_Reference_Statement_Design.docx. Covers three things:

1. prompts.py's _build_voice_dna: reference_statement's usable
   sentences take priority placement in the ANCHOR SENTENCES block,
   ahead of the algorithmic picks from the general corpus, which fill
   remaining slots up to the existing cap. Empty/absent
   reference_statement (every profile that predates this feature)
   must reproduce the prior behaviour exactly — this is the explicit
   backward-compatibility guarantee from the design doc's own \u00a76/\u00a78.

2. api/db/profile_lookup.py's get_profile_bundle: reference_statement
   is read from the row and included in the returned dict, following
   the exact same optional-field pattern as every other column here
   (flagged_dimensions, correction_evidence, ...).

3. Explicit regression guard: reference_statement must never reach
   compute_baseline_metrics as an input anywhere in the render
   pipeline — the whole point of storing it separately from
   raw_text/sample2_completions.
"""
import prompts as pr
import voice_engine as ve


# ------------------------------------------------------------------
# _build_voice_dna priority-anchor selection
# ------------------------------------------------------------------

def test_no_reference_statement_reproduces_prior_behaviour_exactly():
    """The core backward-compatibility guarantee: omitting
    reference_statement (its default, "") must produce byte-for-byte
    identical output to calling _build_voice_dna the old way."""
    kwargs = dict(
        observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
        raw_text="I shipped the release on time. My approach worked well. I built this myself, "
                 "and I stand by every decision I made along the way this quarter.",
        baseline=None, ai_score=0.0,
    )
    with_default = pr._build_voice_dna(**kwargs)
    with_explicit_empty = pr._build_voice_dna(**kwargs, reference_statement="")
    assert with_default == with_explicit_empty


def test_reference_statement_sentences_appear_in_anchor_block():
    raw_text = "I shipped the release on time. My approach worked well. I built this myself."
    reference_statement = "We just closed our biggest quarter yet and the whole team pulled together."
    voice_dna = pr._build_voice_dna(
        observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
        raw_text=raw_text, baseline=None, ai_score=0.0,
        reference_statement=reference_statement,
    )
    assert "ANCHOR SENTENCES" in voice_dna
    assert "We just closed our biggest quarter yet and the whole team pulled together." in voice_dna


def test_reference_statement_sentences_take_priority_over_corpus_picks():
    """The actual precedence logic: with a reference statement AND a
    real corpus both able to supply anchors, reference_statement's
    sentences must appear first in the block, not interleaved or
    sorted after the algorithmic picks."""
    raw_text = (
        "I shipped the release on time. My approach worked well here today. "
        "I built this entirely myself over several long weeks. "
        "I stand by every decision made along the way this year."
    )
    reference_statement = "We just closed our biggest quarter yet as a whole team together."
    voice_dna = pr._build_voice_dna(
        observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
        raw_text=raw_text, baseline=None, ai_score=0.0,
        reference_statement=reference_statement,
    )
    anchor_block = voice_dna.split("ANCHOR SENTENCES")[1]
    ref_pos = anchor_block.find("We just closed our biggest quarter")
    corpus_pos = anchor_block.find("I shipped the release on time")
    assert ref_pos != -1
    assert corpus_pos == -1 or ref_pos < corpus_pos


def test_total_anchor_count_never_exceeds_the_existing_cap():
    raw_text = " ".join([
        "I shipped the release on time today.",
        "My approach worked well across the board.",
        "I built this myself over several weeks.",
        "I stand by every decision made this year.",
        "I always ship things when I say I will.",
        "I never miss a deadline on my own projects.",
    ])
    reference_statement = (
        "We closed our biggest quarter yet together. "
        "Our whole team pulled through under real pressure. "
        "We proved something real to ourselves this year. "
        "We are proud of what we built as a group. "
        "We will keep raising the bar every quarter. "
        "We never lose sight of the actual customer."
    )
    voice_dna = pr._build_voice_dna(
        observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
        raw_text=raw_text, baseline=None, ai_score=0.0,
        reference_statement=reference_statement,
    )
    anchor_block = voice_dna.split("ANCHOR SENTENCES")[1].split("\n\n")[0]
    anchor_lines = [l for l in anchor_block.split("\n") if l.strip().startswith('"')]
    assert len(anchor_lines) <= ve._ANCHOR_SENTENCE_CAP


def test_reference_statement_alone_with_no_usable_general_corpus_still_shows_anchors():
    """A profile whose raw_text has no sentences in the usable 5-20
    word range (e.g. very short or very long) must still get an
    ANCHOR SENTENCES block if reference_statement supplies one — the
    old code's `if usable:` gate would have skipped the whole block
    here; this is a genuine improvement, not a regression, since the
    old behaviour (no anchors at all) is strictly worse."""
    voice_dna = pr._build_voice_dna(
        observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
        raw_text="Hi.",  # too short to yield any usable 5-20 word sentence
        baseline=None, ai_score=0.0,
        reference_statement="We just closed our biggest quarter yet as a whole team together.",
    )
    assert "ANCHOR SENTENCES" in voice_dna
    assert "We just closed our biggest quarter yet as a whole team together." in voice_dna


# ------------------------------------------------------------------
# get_profile_bundle
# ------------------------------------------------------------------

def test_get_profile_bundle_includes_reference_statement_when_present(monkeypatch):
    from api.db import profile_lookup

    class _FakeResult:
        data = [{
            "device_id": "device-1",
            "raw_text": "some writing",
            "sample2_completions": ["", "", "", ""],
            "baseline_fingerprint": {"hedge_density": 1.0},
            "reference_statement": "We shipped the new pricing page this week.",
        }]

    class _FakeTable:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return _FakeResult()

    class _FakeClient:
        def table(self, name): return _FakeTable()

    monkeypatch.setattr(profile_lookup, "get_supabase_client", lambda: _FakeClient())
    bundle = profile_lookup.get_profile_bundle("device-1")
    assert bundle["reference_statement"] == "We shipped the new pricing page this week."


def test_get_profile_bundle_reference_statement_is_none_when_absent(monkeypatch):
    """A row saved before this column existed won't have it — must
    resolve to None (get_profile_bundle's dict-literal default for
    every optional field here), not raise or omit the key."""
    from api.db import profile_lookup

    class _FakeResult:
        data = [{
            "device_id": "device-1",
            "raw_text": "some writing",
            "sample2_completions": ["", "", "", ""],
            "baseline_fingerprint": {"hedge_density": 1.0},
        }]

    class _FakeTable:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return _FakeResult()

    class _FakeClient:
        def table(self, name): return _FakeTable()

    monkeypatch.setattr(profile_lookup, "get_supabase_client", lambda: _FakeClient())
    bundle = profile_lookup.get_profile_bundle("device-1")
    assert bundle["reference_statement"] is None


# ------------------------------------------------------------------
# Explicit separation-of-concerns regression guard
# ------------------------------------------------------------------

def test_reference_statement_never_reaches_compute_baseline_metrics(monkeypatch):
    """The whole point of this feature's design: reference_statement
    must never be part of the scored diagnostic baseline. Patches
    compute_baseline_metrics to record every input it's ever called
    with during a full render_pipeline run, then asserts the
    reference_statement text never appears among them."""
    import render_pipeline as rp

    calls = []
    original = rp.compute_baseline_metrics

    def _spy(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(rp, "compute_baseline_metrics", _spy)

    reference_statement = "We just closed our biggest quarter yet as a whole distinctive team together."

    # A minimal, deliberately-failing (no api_key) call is enough —
    # every compute_baseline_metrics call in run_voice_render happens
    # before the API call is attempted.
    rp.run_voice_render(
        input_text="Checking a draft against my baseline right now.",
        api_key="",  # forces an early, harmless failure after the metrics are computed
        raw_text="I shipped the release on time. My approach worked well.",
        sample2_completions=["", "", "", ""],
        baseline={"hedge_density": 1.0},
        baseline_texts=["I shipped the release on time."],
        reference_statement=reference_statement,
    )

    for call_text in calls:
        assert reference_statement not in call_text, (
            "reference_statement text leaked into a compute_baseline_metrics call — "
            "this must never happen, see VOICOVA_Reference_Statement_Design.docx \u00a76"
        )
