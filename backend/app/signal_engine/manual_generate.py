"""
Manual Analyze & Signal for the dashboard.

Runs full model + live quote analysis. Creates a signal only when confidence
clears configured thresholds — never invents a weak signal on click.
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import Any, Optional

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.quote_collector import QuoteCollector
from app.config import settings
from app.deriv.barriers import (
    barrier_for_direction,
    barrier_magnitude,
    configured_directions,
)
from app.deriv.client import DerivWSClient
from app.features.pipeline import build_features, select_feature_matrix
from app.features.sequences import build_price_sequence
from app.ml.bundle import ModelPipelineBundle
from app.models.tick import Tick
from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.ev_filter import EVFilter
from app.signal_engine.generator import SignalGenerator
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.signal_engine.settings_lookup import get_latest_confirmed_settings
from app.strategies.registry import StrategyRegistry

logger = structlog.get_logger(__name__)


def _load_latest_metadata(direction: str) -> Optional[dict]:
    model_dir = settings.model_dir
    latest = os.path.join(model_dir, f"latest_{direction}.json")
    if os.path.exists(latest):
        with open(latest, encoding="utf-8") as f:
            return json.load(f)
    metas = sorted(glob.glob(os.path.join(model_dir, f"meta_{direction}_*.json")), reverse=True)
    if not metas:
        return None
    with open(metas[0], encoding="utf-8") as f:
        return json.load(f)


def _metadata_compatible(meta: dict, conf) -> bool:
    if not meta:
        return False
    if meta.get("symbol") and meta["symbol"] != conf.symbol:
        return False
    if abs(float(meta.get("barrier_distance", -1)) - float(barrier_magnitude(conf.barrier_input))) > 1e-9:
        return False
    if int(meta.get("duration_seconds", -1)) != int(conf.duration_seconds):
        return False
    return True


def _predict_direction(pack: dict, feat_df: pd.DataFrame, seq) -> dict:
    feature_row = select_feature_matrix(feat_df.iloc[[-1]])
    if seq is not None and getattr(seq, "ndim", 0) == 2:
        seq = np.expand_dims(seq, axis=0)

    bundle = pack.get("bundle")
    if bundle is not None:
        pred = bundle.predict_calibrated(feature_row, seq)
        return {
            "raw_probability": float(pred["raw_probability"]),
            "calibrated_probability": float(pred["calibrated_probability"]),
            "component_probabilities": pred.get("component_probabilities") or {},
            "selected_pipeline": pred.get("selected_pipeline") or pack.get("metadata", {}).get("selected_pipeline"),
        }

    model = pack["model"]
    calibrator = pack.get("calibrator")
    raw_prob = float(model.predict_proba(feature_row)[0])
    if calibrator is not None and getattr(calibrator, "is_fitted", True):
        cal_prob = float(calibrator.calibrate(np.array([raw_prob]))[0])
    else:
        cal_prob = raw_prob
    return {
        "raw_probability": raw_prob,
        "calibrated_probability": cal_prob,
        "component_probabilities": {},
        "selected_pipeline": pack.get("metadata", {}).get("selected_pipeline", "model"),
    }


async def generate_manual_signal(
    session: AsyncSession,
    *,
    force_no_edge: bool = False,
    client: Optional[DerivWSClient] = None,
) -> dict[str, Any]:
    """
    Analyze live market with trained models.
    Emit a signal only if confidence thresholds pass (default), or force_no_edge=True.
    Always returns per-direction analysis for the UI.
    """
    conf = await get_latest_confirmed_settings(session)
    if not conf:
        return {"ok": False, "reason": "Confirm contract settings in Setup first.", "analysis": []}

    dirs = configured_directions(conf.barrier_direction)
    strategies = StrategyRegistry()
    strategies.register_defaults()
    generator = SignalGenerator(
        strategies,
        EVFilter(),
        DailyCapManager(),
        SignalLifecycleManager(),
    )

    loaded = 0
    for direction in dirs:
        meta = _load_latest_metadata(direction)
        if not meta or not _metadata_compatible(meta, conf):
            continue
        cal_path = meta.get("calibrator_path")
        if not cal_path or not os.path.exists(cal_path):
            continue
        try:
            bundle = ModelPipelineBundle.load(meta)
            tag = meta.get("version_tag", "")
            generator.set_model(
                bundle=bundle,
                version_id=hash(tag) % 1_000_000 or 1,
                has_edge=bool(meta.get("has_demonstrated_edge", False)),
                direction=direction,
                metadata=meta,
                version_tag=tag,
            )
            loaded += 1
        except Exception as e:
            logger.error("manual_model_load_failed", direction=direction, error=str(e))

    if loaded == 0:
        return {"ok": False, "reason": "No compatible trained model found. Train first.", "analysis": []}

    result = await session.execute(
        select(Tick)
        .where(Tick.symbol == conf.symbol)
        .order_by(Tick.epoch.desc())
        .limit(600)
    )
    ticks = result.scalars().all()
    if len(ticks) <= 100:
        return {
            "ok": False,
            "reason": f"Need more ticks for features (have {len(ticks)}, need >100).",
            "analysis": [],
        }

    df = pd.DataFrame(
        [{"epoch": t.epoch, "tick_time": t.tick_time, "quote": t.quote} for t in reversed(ticks)]
    )
    barrier_dist = float(barrier_magnitude(conf.barrier_input))
    current_price = float(df.iloc[-1]["quote"])
    last_tick_age = time.time() - float(df.iloc[-1]["epoch"])

    features_by_direction = {}
    sequences_by_direction = {}
    entry_epoch = int(df.iloc[-1]["epoch"])
    for direction in dirs:
        features_by_direction[direction] = build_features(
            df,
            barrier_distance=barrier_dist,
            barrier_direction=direction,
        )
        sequences_by_direction[direction] = build_price_sequence(
            df,
            entry_epoch=entry_epoch,
            barrier_distance=barrier_dist,
            barrier_direction=direction,
            duration_seconds=int(conf.duration_seconds),
            seq_len=settings.lstm_seq_len,
        )

    owns_client = client is None
    if owns_client:
        client = DerivWSClient()
        try:
            await client.connect()
        except Exception as e:
            return {"ok": False, "reason": f"Deriv API unavailable: {e}", "analysis": []}

    quotes_by_direction: dict[str, dict] = {}
    try:
        for direction in dirs:
            signed = barrier_for_direction(conf.barrier_input, direction)
            qc = QuoteCollector(
                client=client,
                symbol=conf.symbol,
                barrier=signed,
                duration=int(conf.duration_value),
                duration_unit=conf.duration_unit,
                barrier_direction=direction,
            )
            q = await qc.fetch_single_quote()
            if q:
                quotes_by_direction[direction] = q
    finally:
        if owns_client and client is not None:
            await client.disconnect()

    if not quotes_by_direction:
        return {"ok": False, "reason": "Could not fetch live contract quotes from Deriv.", "analysis": []}

    min_conf = float(settings.manual_min_confidence)
    min_margin = float(settings.manual_min_margin_over_breakeven)
    analysis: list[dict] = []

    for direction in dirs:
        pack = generator._models.get(direction)
        feat_df = features_by_direction.get(direction)
        quote = quotes_by_direction.get(direction)
        if pack is None or feat_df is None or feat_df.empty or not quote:
            analysis.append({
                "direction": direction,
                "ok": False,
                "reason": "Missing model, features, or quote",
            })
            continue
        try:
            pred = _predict_direction(pack, feat_df, sequences_by_direction.get(direction))
        except Exception as e:
            analysis.append({"direction": direction, "ok": False, "reason": str(e)})
            continue

        ask = float(quote.get("ask_price", 0) or 0)
        payout = float(quote.get("payout", 0) or 0)
        breakeven = (ask / payout) if payout > 0 else 1.0
        cal = float(pred["calibrated_probability"])
        margin = cal - breakeven
        confluence_strategy = generator.strategies.get_strategy("touch_confluence")
        confluence = (
            confluence_strategy.evaluate(feat_df, current_price, barrier_dist)
            if confluence_strategy is not None
            else None
        )
        confluence_met = bool(confluence and confluence.direction == direction)
        meets = (
            cal >= min_conf
            and margin >= min_margin
            and (force_no_edge or confluence_met)
        )
        analysis.append({
            "direction": direction,
            "ok": True,
            "calibrated_probability": cal,
            "raw_probability": pred["raw_probability"],
            "breakeven_probability": breakeven,
            "margin_over_breakeven": margin,
            "purchase_price": ask,
            "total_payout": payout,
            "has_edge": bool(pack.get("has_edge")),
            "selected_pipeline": pred.get("selected_pipeline"),
            "component_probabilities": pred.get("component_probabilities") or {},
            "meets_confidence": meets,
            "confluence_met": confluence_met,
            "confluence_explanation": confluence.explanation if confluence_met else None,
            "spot": current_price,
        })

    thresholds = {
        "min_confidence": min_conf,
        "min_margin_over_breakeven": min_margin,
    }

    can_issue, cap_reason = await DailyCapManager().can_issue_signal(session, conf.symbol)
    if not can_issue:
        return {
            "ok": False,
            "reason": cap_reason,
            "analysis": analysis,
            "thresholds": thresholds,
            "current_price": current_price,
        }

    # Prefer confidence-gated analyze (not blind force)
    signal_data = await generator.evaluate_and_generate(
        session,
        features_df=features_by_direction[dirs[0]],
        current_price=current_price,
        barrier_distance=barrier_dist,
        symbol=conf.symbol,
        quotes_by_direction=quotes_by_direction,
        allowed_directions=dirs,
        features_by_direction=features_by_direction,
        sequences_by_direction=sequences_by_direction,
        last_tick_age_seconds=last_tick_age,
        force_no_edge=force_no_edge,
        confidence_override=not force_no_edge,
        min_confidence=min_conf,
        min_margin_over_breakeven=min_margin,
    )

    if not signal_data:
        best_line = ""
        scored = [a for a in analysis if a.get("ok")]
        if scored:
            best = max(scored, key=lambda a: a.get("calibrated_probability", 0.0))
            best_line = (
                f" Best now: {best['direction'].upper()} "
                f"{best['calibrated_probability']*100:.1f}% "
                f"(breakeven {best['breakeven_probability']*100:.1f}%, "
                f"margin {best['margin_over_breakeven']*100:.1f}%, "
                f"confluence={'yes' if best.get('confluence_met') else 'no'})."
            )
        return {
            "ok": False,
            "reason": (
                f"Analyzed — no high-confidence setup. "
                f"Need touch confluence, p≥{min_conf*100:.0f}%, and "
                f"margin≥{min_margin*100:.0f}% over quote breakeven."
                f"{best_line} Try again when the model is more confident."
            ),
            "analysis": analysis,
            "thresholds": thresholds,
            "current_price": current_price,
        }

    return {
        "ok": True,
        "signal": signal_data,
        "analysis": analysis,
        "thresholds": thresholds,
        "current_price": current_price,
        "force_no_edge": force_no_edge,
        "confidence_override": not force_no_edge,
    }
