"""
Diagnostic for AI_CONTAMINATION_PATH_THRESHOLD (scoring_rules.py:268).

_score_ai_signal (voice_engine.py:1243) returns a single float with no
visibility into which sub-signal drove it. This reimplements the same
scoring logic but returns a per-feature breakdown, so a false-positive
can be traced to the specific feature responsible instead of guessing.

NOTE: the paragraphs below are illustrative stand-ins (varied, plainly
human, written for this diagnostic) — not John's actual baseline
writing. Swap in real flagged samples from his own onboarding pastes
or past renders for a real calibration run; this establishes the
methodology and confirms which feature is most trigger-happy in
principle.
"""
import re

AI_CONTAMINATION_PATH_THRESHOLD = 0.25


def score_ai_signal_breakdown(text: str) -> dict:
    scores = {}
    words = text.split()
    total = max(len(words), 1)

    em_dashes = len(re.findall(r"[—–\u2014\u2013]", text))
    if em_dashes >= 2:
        scores["em_dashes"] = (0.30, f"{em_dashes} em dashes")
    elif em_dashes == 1:
        scores["em_dashes"] = (0.12, "1 em dash")
    else:
        scores["em_dashes"] = (0.0, "0 em dashes")

    verbose_openers = re.compile(
        r"\b(it is (important|worth|essential|crucial|critical|key) to (note|recognise|recognize|understand|consider)|"
        r"in (today's|the current|our) (landscape|world|environment|era|age|climate)|"
        r"when it comes to|at the end of the day|it goes without saying|"
        r"needless to say|it is worth noting|with that (said|in mind)|"
        r"in light of (this|the above|recent)|as (we|you) (know|can see|may know)|"
        r"it (should|must) be (noted|acknowledged|recognised|recognized) that|"
        r"one (cannot|can't) (overstate|underestimate|deny)|"
        r"this (underscores|highlights|demonstrates|illustrates|showcases|exemplifies)|"
        r"leveraging|synergies|holistic(ally)?|paradigm|robust(ly)?|"
        r"cutting.edge|game.changing|transformative|groundbreaking)\b",
        re.I
    )
    vo_hits = len(verbose_openers.findall(text))
    scores["verbose_openers"] = (min(0.30, vo_hits * 0.10), f"{vo_hits} hits")

    hedge = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|arguably|seemingly|"
        r"apparently|ostensibly|presumably|it seems|it appears)\b", re.I
    )
    hedge_count = len(hedge.findall(text))
    hedge_density = hedge_count / total
    if hedge_density > 0.05:
        scores["hedge_density"] = (0.20, f"{hedge_density:.3f} density")
    elif hedge_density > 0.03:
        scores["hedge_density"] = (0.10, f"{hedge_density:.3f} density")
    else:
        scores["hedge_density"] = (0.0, f"{hedge_density:.3f} density")

    filler_transitions = re.compile(
        r"\b(furthermore|moreover|additionally|in addition|nevertheless|"
        r"notwithstanding|consequently|subsequently|accordingly|"
        r"in conclusion|to summarise|to summarize|in summary|"
        r"to be clear|to be fair|to that end|with this in mind|"
        r"it is (also )?(important|worth) (mentioning|highlighting|noting))\b",
        re.I
    )
    ft_hits = len(filler_transitions.findall(text))
    scores["filler_transitions"] = (min(0.20, ft_hits * 0.07), f"{ft_hits} hits")

    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip() and len(s.split()) >= 3]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 22:
            scores["avg_sentence_length"] = (0.15, f"{avg_len:.1f} words/sentence")
        elif avg_len > 16:
            scores["avg_sentence_length"] = (0.07, f"{avg_len:.1f} words/sentence")
        else:
            scores["avg_sentence_length"] = (0.0, f"{avg_len:.1f} words/sentence")

    passive = re.compile(
        r"\b(is|are|was|were|has been|have been|had been|will be|"
        r"can be|could be|should be|would be|may be|might be) \w+ed\b",
        re.I
    )
    passive_count = len(passive.findall(text))
    passive_ratio = passive_count / max(len(sentences), 1)
    if passive_ratio > 0.4:
        scores["passive_concentration"] = (0.10, f"{passive_ratio:.2f} ratio")
    else:
        scores["passive_concentration"] = (0.0, f"{passive_ratio:.2f} ratio")

    total_score = min(1.0, sum(v[0] for v in scores.values()))
    return {"total": total_score, "breakdown": scores, "trips_threshold": total_score >= AI_CONTAMINATION_PATH_THRESHOLD}


SAMPLE_PARAGRAPHS = {
    "direct_em_dash_heavy": (
        "We shipped the fix Tuesday — three days ahead of plan. Ops confirmed "
        "the rollback path works — tested it twice, no surprises. Next week "
        "we start the migration — same team, same process."
    ),
    "long_sentence_analytical": (
        "The reason the previous quarter's numbers looked stronger than they "
        "actually were is that a large chunk of revenue got pulled forward from "
        "renewals that would ordinarily have landed a few weeks later, which "
        "means the underlying run rate, once you strip that timing effect back "
        "out, is closer to flat than the headline growth figure would suggest."
    ),
    "hedgy_but_human": (
        "I might be wrong about this, but it seems like the vendor's pricing "
        "page hasn't actually changed since March. Could be a caching issue on "
        "their end, or maybe they just haven't updated it. Worth checking before "
        "we quote the old number to anyone."
    ),
    "short_punchy_direct": (
        "Ship it. We've tested enough. The risk of waiting is bigger than the "
        "risk of a small bug slipping through, and we can patch fast if one does."
    ),
}

if __name__ == "__main__":
    for name, text in SAMPLE_PARAGRAPHS.items():
        result = score_ai_signal_breakdown(text)
        print(f"\n=== {name} ===")
        print(f"Total: {result['total']:.3f}  (threshold: {AI_CONTAMINATION_PATH_THRESHOLD})  "
              f"-> {'TRIPS' if result['trips_threshold'] else 'clear'}")
        for feature, (score, detail) in result["breakdown"].items():
            marker = "  <-- contributes" if score > 0 else ""
            print(f"  {feature:22s} +{score:.2f}  ({detail}){marker}")
