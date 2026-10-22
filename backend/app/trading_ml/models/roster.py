"""Every spec model appears in the comparison. Only a passing direction model can vote."""
from __future__ import annotations


def _blank(name: str, role: str, *, note: str | None = None, error: str | None = None,
           research_log_loss: float | None = None) -> dict:
    return {
        "name": name,
        "role": role,
        "votes": False,
        "validation_log_loss": None,
        "test_log_loss": None,
        "test_auc": None,
        "test_brier": None,
        "research_log_loss": research_log_loss,
        "error": error,
        "note": note,
    }


def publish_roster(comparison: list[dict], notes: list[str], sequence: dict, hmm: dict,
                   pattern: dict, *, selected: str) -> list[dict]:
    by_name = {row["name"]: dict(row) for row in comparison}
    rows = []
    for name in ("logistic", "xgboost", "lightgbm", "catboost"):
        row = by_name.get(name)
        if row is None:
            skipped = next((item for item in notes if item.lower().startswith(name)), None)
            rows.append(_blank(
                name, "direction",
                error=skipped or f"{name} was not scored, and no result was invented.",
            ))
            continue
        votes = selected == name or (selected == "ensemble" and row.get("error") is None and row.get("test_log_loss") is not None)
        rows.append({
            **_blank(name, "direction"),
            **row,
            "role": "direction",
            "votes": votes,
            "note": "Untouched chronological test." if row.get("test_log_loss") is not None else row.get("error"),
        })
    sequence_results = (sequence or {}).get("results") or {}
    sequence_reason = (sequence or {}).get("reason")
    for key, label in (("lstm", "lstm"), ("gru", "gru"), ("cnn", "temporal_cnn")):
        item = sequence_results.get(key)
        if item:
            rows.append(_blank(
                label, "sequence",
                research_log_loss=item.get("test_log_loss"),
                note="Scored on an earlier time slice. This is not the untouched Rise/Fall test, so it does not vote.",
            ))
        else:
            rows.append(_blank(label, "sequence", error=sequence_reason or "Sequence model was not run."))
    rows.append(_blank(
        "hmm", "regime",
        note=(hmm or {}).get("reason") or "HMM was not run.",
    ))
    rows.append(_blank(
        "nearest_neighbors", "similarity",
        note=(pattern or {}).get("reason") or "Nearest neighbors were not run.",
    ))
    return rows
