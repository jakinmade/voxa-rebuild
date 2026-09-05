# Competitor fabrication benchmark — results

Run 5 Sept 2026 via Claude for Chrome, following
`docs/competitor-fabrication-benchmark-protocol.md`. Full writeup
delivered separately as VOICOVA_Competitor_Fabrication_Benchmark.docx.

## Results

| Tool | Fabricated? | Evidence | Caveat |
|---|---|---|---|
| VoiceMoat (rewrite tool) | **Yes** | On Input A, pulled a fabricated "env var rename" detail from the calibration voice sample into the rewrite of an unrelated deployment story. No warning, flag, or confidence signal shown in the UI. | Direct, solid test result. |
| ContentIn | No fabrication observed | Neither input produced an invented specific in the free tool. | Free tool could not exercise real voice-training (paywalled) — "didn't fabricate under a limited test," not a confirmed absence of the behavior. |
| River's Client Voice Match Analyzer | Not directly tested | River's actual tool is signup-only with no working free version. VoiceMoat's equivalent scorer-type tool was used as a stand-in and confirmed to measure tone/rhythm match only — no fabrication or factual-fidelity check of any kind, confident percentage regardless. | Evidence about this category of scoring tool, not a confirmed result for River specifically. |

## Takeaway

At least one direct competitor (VoiceMoat) exhibits the same
fabrication failure mode VOICOVA does, with no gate or warning at all.
VOICOVA's `has_content_integrity_hard_fail` gate is not something
either tested tool appears to have — on this evidence, it looks like a
genuine differentiator, not a gap VOICOVA is behind on. This reframes
item 1's finish line: "zero fabrication" may not be an achievable bar
for any current LLM at this compression task; "honest about when it
might have fabricated, and gated accordingly" is a real, defensible
position VOICOVA already holds, imperfectly, that at least one
competitor does not appear to hold at all.

Small sample (two inputs, three tools, one pass each) — directional,
not conclusive.
