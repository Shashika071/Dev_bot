"""
Signal generator — strategies + ML + EV filter + daily cap.

Safety constraints:
- NEVER places trades
- Max signals per day + cooldown
- Alerts disabled until setup confirmed
- No signal without held-out EV evidence
- Suppressed during stale tick/quote data
"""

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deriv.barriers import barrier_for_direction, configured_directions
from app.features.pipeline import select_feature_matrix
from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.ev_filter import EVFilter
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.signal_engine.settings_lookup import get_latest_confirmed_settings
from app.strategies.registry import StrategyRegistry

logger = structlog.get_logger(__name__)


class SignalGenerator:
    """Combines all components to generate qualified signals."""

    def __init__(
        self,
        strategy_registry: StrategyRegistry,
        ev_filter: EVFilter,
        daily_cap: DailyCapManager,
        lifecycle: SignalLifecycleManager,
    ):
        self.strategies = strategy_registry
        self.ev_filter = ev_filter
        self.daily_cap = daily_cap
        self.lifecycle = lifecycle
        self._models: dict[str, dict] = {}

    def set_model(
        self,
        model=None,
        calibrator=None,
        version_id: int = 1,
        has_edge: bool = False,
        direction: str = "upper",
        metadata: Optional[dict] = None,
        version_tag: str = "",
        bundle=None,
    ):
        """Load a trained pipeline bundle (preferred) or legacy single model."""
        self._models[direction] = {
            "model": model,
            "calibrator": calibrator,
            "bundle": bundle,
            "version_id": version_id,
            "has_edge": has_edge,
            "metadata": metadata or {},
            "version_tag": version_tag,
        }

    def clear_models(self):
        self._models.clear()

    async def check_prerequisites(
        self,
        session: AsyncSession,
        *,
        ignore_pause: bool = False,
    ) -> tuple[bool, str]:
        settings_row = await get_latest_confirmed_settings(session)
        if not settings_row:
            return False, "Contract settings not confirmed. Complete setup first."
        if not self._models:
            return False, "ML model not loaded. Train a model first."
        if (
            not ignore_pause
            and getattr(settings_row, "alerts_paused", False)
        ):
            reason = settings_row.pause_reason or "Alerts paused by drift monitor"
            return False, reason
        return True, "All prerequisites met"

    async def evaluate_and_generate(
        self,
        session: AsyncSession,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
        symbol: str,
        current_quote: Optional[dict] = None,
        quotes_by_direction: Optional[dict] = None,
        allowed_directions: Optional[list[str]] = None,
        features_by_direction: Optional[dict] = None,
        sequences_by_direction: Optional[dict] = None,
        last_tick_age_seconds: Optional[float] = None,
        force_no_edge: bool = False,
        confidence_override: bool = False,
        min_confidence: Optional[float] = None,
        min_margin_over_breakeven: Optional[float] = None,
    ) -> Optional[dict]:
        """
        confidence_override: allow signals without demonstrated edge, but only when
        calibrated probability clears min_confidence and margin over quote breakeven.
        force_no_edge: legacy blind bypass (avoid for UI; tests only).
        """
        # Manual research analysis may run while auto-alerts are paused.
        ok, reason = await self.check_prerequisites(
            session, ignore_pause=bool(confidence_override or force_no_edge)
        )
        if not ok:
            logger.debug("signal_prerequisites_failed", reason=reason)
            return None

        allow_stale = force_no_edge  # confidence path still needs fresh ticks
        if last_tick_age_seconds is not None and last_tick_age_seconds > settings.max_tick_age_seconds:
            if not allow_stale:
                logger.debug("signal_stale_ticks", age=last_tick_age_seconds)
                return None
            logger.warning("signal_stale_ticks_force", age=last_tick_age_seconds)

        can_issue, cap_reason = await self.daily_cap.can_issue_signal(session, symbol)
        if not can_issue:
            logger.debug("signal_daily_cap_blocked", reason=cap_reason)
            return None

        conf_settings = await get_latest_confirmed_settings(session)
        dirs = allowed_directions or configured_directions(
            getattr(conf_settings, "barrier_direction", "both") if conf_settings else "both"
        )

        # Collect passing candidates, then pick best EV
        passing: list[dict] = []
        require_confluence = bool(settings.require_touch_confluence) and not force_no_edge

        for direction in dirs:
            feat_df = None
            if features_by_direction and direction in features_by_direction:
                feat_df = features_by_direction[direction]
            else:
                feat_df = features_df
            if feat_df is None or feat_df.empty:
                continue

            candidates = self.strategies.evaluate_all(feat_df, current_price, barrier_distance)
            candidates = [c for c in candidates if c.direction == direction]
            if require_confluence:
                # Auto alerts and Analyze & Signal both require direction-matched confluence.
                candidates = [
                    c for c in candidates if c.strategy_name == "touch_confluence"
                ]
            if not candidates and force_no_edge:
                from app.strategies.base import StrategySignal

                candidates = [
                    StrategySignal(
                        direction=direction,
                        confidence_raw=0.5,
                        strategy_name="manual_analyze",
                        strategy_params={
                            "force_no_edge": force_no_edge,
                            "confidence_override": confidence_override,
                        },
                        explanation="Manual analyze candidate (model confidence gated)",
                    )
                ]
            if not candidates:
                continue

            pack = self._models.get(direction)
            if pack is None:
                logger.debug("signal_no_model_for_direction", direction=direction)
                continue

            has_edge = pack["has_edge"]
            version_id = pack["version_id"]
            meta = pack.get("metadata") or {}
            selected_pipeline = meta.get("selected_pipeline", "catboost")
            component_probs: dict = {}

            try:
                feature_row = select_feature_matrix(feat_df.iloc[[-1]])
                seq = None
                if sequences_by_direction and direction in sequences_by_direction:
                    seq = sequences_by_direction[direction]
                    if seq is not None and getattr(seq, "ndim", 0) == 2:
                        seq = np.expand_dims(seq, axis=0)

                bundle = pack.get("bundle")
                if bundle is not None:
                    pred = bundle.predict_calibrated(feature_row, seq)
                    raw_prob = float(pred["raw_probability"])
                    cal_prob = float(pred["calibrated_probability"])
                    stats = pred.get("empirical_stats") or {
                        "sample_count": 0, "hit_rate": 0.0, "ci_lower": 0.0, "ci_upper": 0.0
                    }
                    component_probs = pred.get("component_probabilities") or {}
                    selected_pipeline = pred.get("selected_pipeline", selected_pipeline)
                else:
                    model = pack["model"]
                    calibrator = pack["calibrator"]
                    raw_prob = float(model.predict_proba(feature_row)[0])
                    if calibrator is not None and getattr(calibrator, "is_fitted", True):
                        cal_prob = float(calibrator.calibrate(np.array([raw_prob]))[0])
                        stats = calibrator.empirical_stats(cal_prob, band=0.05)
                    else:
                        cal_prob = raw_prob
                        stats = {
                            "sample_count": 0,
                            "hit_rate": 0.0,
                            "ci_lower": 0.0,
                            "ci_upper": 0.0,
                        }
            except Exception as e:
                logger.error("signal_ml_prediction_error", error=str(e), direction=direction)
                continue

            quote = None
            if quotes_by_direction:
                quote = quotes_by_direction.get(direction)
            if quote is None:
                quote = current_quote
            if quote is None:
                continue

            # Quote freshness (optional quote_epoch)
            quote_epoch = quote.get("quote_epoch")
            if quote_epoch and not force_no_edge:
                age = datetime.now(timezone.utc).timestamp() - float(quote_epoch)
                if age > settings.max_quote_age_seconds:
                    logger.debug("signal_stale_quote", direction=direction, age=age)
                    continue

            purchase_price = float(quote.get("ask_price", 0) or 0)
            total_payout = float(quote.get("payout", 0) or 0)
            if purchase_price <= 0 or total_payout <= 0:
                continue

            conf_floor = (
                float(min_confidence)
                if min_confidence is not None
                else float(settings.manual_min_confidence)
            )
            margin_floor = (
                float(min_margin_over_breakeven)
                if min_margin_over_breakeven is not None
                else float(settings.manual_min_margin_over_breakeven)
            )

            for candidate in candidates:
                from app.signal_engine.ev_filter import EVFilterResult

                if force_no_edge:
                    breakeven = purchase_price / total_payout
                    conservative = float(cal_prob)
                    ev_net = conservative * total_payout - purchase_price
                    margin = conservative - breakeven
                    ev_result = EVFilterResult(
                        passes=True,
                        purchase_price=purchase_price,
                        total_payout=total_payout,
                        breakeven_probability=breakeven,
                        calibrated_probability=cal_prob,
                        conservative_probability=conservative,
                        ev_net=ev_net,
                        margin=margin,
                        min_required_margin=0.0,
                        reason="FORCE OVERRIDE — edge/EV gates bypassed by user",
                    )
                elif confidence_override:
                    # Same conservative CI / freshness evidence as EV path, but
                    # without requiring demonstrated edge (research Analyze).
                    research_filter = EVFilter(
                        min_ev_margin=margin_floor,
                        min_samples_in_range=settings.min_calibration_samples,
                        require_demonstrated_edge=False,
                    )
                    ev_result = research_filter.evaluate(
                        calibrated_prob=cal_prob,
                        purchase_price=purchase_price,
                        total_payout=total_payout,
                        model_has_edge=has_edge,
                        calibration_sample_count=int(stats.get("sample_count", 0)),
                        empirical_hit_rate=stats.get("hit_rate"),
                        empirical_ci_lower=stats.get("ci_lower"),
                    )
                    # Also enforce absolute calibrated confidence floor for UI clarity
                    if ev_result.passes and float(cal_prob) < conf_floor:
                        from app.signal_engine.ev_filter import EVFilterResult as _EFR

                        ev_result = _EFR(
                            passes=False,
                            purchase_price=ev_result.purchase_price,
                            total_payout=ev_result.total_payout,
                            breakeven_probability=ev_result.breakeven_probability,
                            calibrated_probability=cal_prob,
                            conservative_probability=ev_result.conservative_probability,
                            ev_net=ev_result.ev_net,
                            margin=ev_result.margin,
                            min_required_margin=margin_floor,
                            reason=(
                                f"Calibrated confidence {cal_prob:.3f} "
                                f"< floor {conf_floor:.3f}"
                            ),
                        )
                else:
                    ev_result = self.ev_filter.evaluate(
                        calibrated_prob=cal_prob,
                        purchase_price=purchase_price,
                        total_payout=total_payout,
                        model_has_edge=has_edge,
                        calibration_sample_count=int(stats.get("sample_count", 0)),
                        empirical_hit_rate=stats.get("hit_rate"),
                        empirical_ci_lower=stats.get("ci_lower"),
                    )
                if not ev_result.passes:
                    logger.debug(
                        "signal_ev_filter_rejected",
                        reason=ev_result.reason,
                        direction=direction,
                        strategy=candidate.strategy_name,
                    )
                    continue

                passing.append(
                    {
                        "candidate": candidate,
                        "direction": direction,
                        "raw_prob": raw_prob,
                        "cal_prob": cal_prob,
                        "quote": quote,
                        "ev_result": ev_result,
                        "version_id": version_id,
                        "has_edge": has_edge,
                        "purchase_price": purchase_price,
                        "total_payout": total_payout,
                        "component_probs": component_probs,
                        "selected_pipeline": selected_pipeline,
                        "version_tag": pack.get("version_tag", ""),
                        "barrier_unit": meta.get("barrier_unit", "relative_price_points"),
                    }
                )

        if not passing:
            return None

        # Confidence/force: highest model probability; normal: best EV
        if force_no_edge or confidence_override:
            best = max(passing, key=lambda x: x["cal_prob"])
        else:
            best = max(passing, key=lambda x: x["ev_result"].ev_net)
        candidate = best["candidate"]
        direction = best["direction"]
        quote = best["quote"]
        ev_result = best["ev_result"]

        day_date = self.daily_cap.get_today_date()
        day_sequence = await self.daily_cap.get_next_sequence(session)
        signed_barrier = barrier_for_direction(
            conf_settings.barrier_input if conf_settings else str(barrier_distance),
            direction,
        )

        comps = best.get("component_probs") or {}
        comp_txt = ", ".join(f"{k}={v:.3f}" for k, v in comps.items())
        explanation = (
            f"{candidate.explanation} | selected={best.get('selected_pipeline')} "
            f"| components: {comp_txt}"
        )
        if force_no_edge:
            explanation = (
                "FORCE OVERRIDE (no demonstrated edge) — research only, not a validated alert. | "
                + explanation
            )
        elif confidence_override:
            explanation = (
                f"HIGH-CONFIDENCE ANALYZE (p={best['cal_prob']:.3f}, "
                f"margin={ev_result.margin:.3f}) — research signal when edge evidence is weak. | "
                + explanation
            )

        limitations = (
            "Model predictions are based on historical patterns. "
            "Past performance does not guarantee future results. "
            "Synthetic index behavior may change without notice. "
            "Zero alerts is acceptable when edge is not demonstrated."
        )
        if force_no_edge:
            limitations = (
                "USER FORCE OVERRIDE: edge and EV evidence gates were bypassed. "
                "This signal is NOT validated. Treat as research only. " + limitations
            )
        elif confidence_override and not best["has_edge"]:
            limitations = (
                "Confidence override: model cleared probability/margin thresholds but "
                "does not have demonstrated historical edge. Research only. " + limitations
            )

        validated = bool(best["has_edge"]) and not force_no_edge
        signal = await self.lifecycle.create_signal(
            session,
            symbol=symbol,
            direction=direction,
            duration_seconds=conf_settings.duration_seconds if conf_settings else 540,
            barrier_input=signed_barrier,
            barrier_unit=(
                best.get("barrier_unit")
                or (conf_settings.barrier_unit_description if conf_settings else "relative_price_points")
            ),
            reference_price=current_price,
            resolved_barrier=quote.get("barrier_resolved"),
            quote_id=None,
            quote_timestamp=datetime.now(timezone.utc),
            purchase_price=best["purchase_price"],
            total_payout=best["total_payout"],
            breakeven_probability=ev_result.breakeven_probability,
            calibrated_probability=best["cal_prob"],
            raw_probability=best["raw_prob"],
            ev_net=ev_result.ev_net,
            ev_margin=ev_result.margin,
            strategy_name=f"{candidate.strategy_name}/{best.get('selected_pipeline')}",
            strategy_params={
                **(candidate.strategy_params or {}),
                "force_no_edge": force_no_edge,
                "confidence_override": confidence_override,
            },
            explanation=explanation,
            evidence_limitations=limitations,
            model_version_id=best["version_id"],
            is_validated=validated,
            day_date=day_date,
            day_sequence=day_sequence,
        )

        return {
            "signal_id": signal.signal_id,
            "symbol": symbol,
            "direction": direction,
            "barrier_input": signed_barrier,
            "barrier_unit": best.get("barrier_unit", "relative_price_points"),
            "duration_seconds": conf_settings.duration_seconds if conf_settings else 540,
            "calibrated_probability": best["cal_prob"],
            "raw_probability": best["raw_prob"],
            "component_probabilities": comps,
            "selected_pipeline": best.get("selected_pipeline"),
            "model_version": best.get("version_tag"),
            "breakeven_probability": ev_result.breakeven_probability,
            "ev_net": ev_result.ev_net,
            "purchase_price": best["purchase_price"],
            "total_payout": best["total_payout"],
            "quote_timestamp": datetime.now(timezone.utc).isoformat(),
            "explanation": explanation,
            "is_validated": validated,
            "force_no_edge": force_no_edge,
            "confidence_override": confidence_override,
        }
