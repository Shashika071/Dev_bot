"""Write reproducible evaluation reports for the multi-model pipeline."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def write_evaluation_report(path: str, report: dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **report,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    # Human-readable companion
    md_path = path.replace(".json", ".md")
    lines = [
        "# Touch Model Evaluation Report",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Symbol: `{report.get('symbol')}`",
        f"- Direction: `{report.get('direction')}`",
        f"- Barrier: `{report.get('barrier_distance')}` ({report.get('barrier_unit')})",
        f"- Duration: `{report.get('duration_seconds')}`s",
        f"- Selected pipeline: `{report.get('selected_pipeline')}`",
        f"- Demonstrated edge: `{report.get('has_demonstrated_edge')}`",
        "",
        "## Contract semantics",
        "",
        str(report.get("contract_semantics", "")),
        "",
        "## Candidate comparison (validation selection + held-out test)",
        "",
    ]
    candidates = report.get("candidates") or {}
    for name, metrics in candidates.items():
        lines.append(f"### {name}")
        lines.append(f"- Val Brier: {metrics.get('val_brier')}")
        lines.append(f"- Test Brier: {metrics.get('test_brier')}")
        lines.append(f"- Test AUC: {metrics.get('test_auc')}")
        lines.append(f"- Test selected signals: {metrics.get('test_selected_signal_count')}")
        lines.append(f"- Quote-based note: {metrics.get('quote_note')}")
        lines.append("")

    lines.extend(
        [
            "## Calibration (selected pipeline on calibration set)",
            "",
            f"- Brier: {report.get('calibration', {}).get('brier_score')}",
            f"- Log loss: {report.get('calibration', {}).get('log_loss')}",
            f"- Samples: {report.get('calibration', {}).get('n_samples')}",
            "",
            "## Limitations",
            "",
            str(report.get("limitations", "")),
            "",
            "Passing software checks is not proof of profitability.",
            "",
        ]
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
