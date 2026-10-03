"""
Background worker entrypoint.
Runs tick collection, quote polling, feature generation, and signal evaluation.
"""

import asyncio
import glob
import json
import os
import time
from datetime import datetime, timezone

import httpx
import pandas as pd
import structlog
from sqlalchemy import select

from app.collector.quote_collector import QuoteCollector
from app.collector.tick_collector import TickCollector
from app.config import settings
from app.database import async_session
from app.deriv.barriers import (
    barrier_magnitude,
    configured_directions,
    iter_direction_barriers,
)
from app.deriv.client import DerivWSClient
from app.features.pipeline import build_features
from app.features.sequences import build_price_sequence
from app.ml.bundle import ModelPipelineBundle
from app.models.settings import ContractSettings
from app.models.tick import Tick
from app.notifications.telegram import TelegramNotifier
from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.ev_filter import EVFilter
from app.signal_engine.generator import SignalGenerator
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.signal_engine.settings_lookup import get_latest_confirmed_settings
from app.strategies.registry import StrategyRegistry

logger = structlog.get_logger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")


def _auth_headers() -> dict:
    return {"X-Internal-Secret": settings.internal_api_secret}


async def _publish_status(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(
                f"{BACKEND_URL}/signals/worker-status",
                json=payload,
                headers=_auth_headers(),
            )
    except Exception as e:
        logger.warning("worker_status_publish_failed", error=str(e))


async def _publish_signal(signal_data: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(
                f"{BACKEND_URL}/signals/worker-event",
                json={"type": "signal_alert", "data": signal_data},
                headers=_auth_headers(),
            )
    except Exception as e:
        logger.warning("worker_signal_publish_failed", error=str(e))


def _config_key(conf: ContractSettings) -> tuple:
    return (
        conf.symbol,
        conf.barrier_input,
        conf.barrier_direction,
        conf.duration_value,
        conf.duration_unit,
        conf.duration_seconds,
    )


def _load_latest_metadata(direction: str) -> dict | None:
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


def _metadata_compatible(meta: dict, conf: ContractSettings) -> bool:
    if not meta:
        return False
    if meta.get("symbol") and meta["symbol"] != conf.symbol:
        return False
    if abs(float(meta.get("barrier_distance", -1)) - float(barrier_magnitude(conf.barrier_input))) > 1e-9:
        return False
    if int(meta.get("duration_seconds", -1)) != int(conf.duration_seconds):
        return False
    return True


async def worker_loop():
    logger.info("worker_starting", backend_url=BACKEND_URL)

    client = DerivWSClient()
    await client.connect()

    strategies = StrategyRegistry()
    strategies.register_defaults()

    ev_filter = EVFilter()
    daily_cap = DailyCapManager()
    lifecycle = SignalLifecycleManager()
    generator = SignalGenerator(strategies, ev_filter, daily_cap, lifecycle)
    telegram = TelegramNotifier()
    await telegram.initialize()

    tick_collector = None
    quote_collectors: dict[str, QuoteCollector] = {}
    quote_tasks: list[asyncio.Task] = []
    active_config = None
    active_symbol = None
    loaded_version_tags: dict[str, str] = {}

    try:
        while True:
            try:
                if not client.connected:
                    await client.ensure_connected()

                async with async_session() as session:
                    conf = await get_latest_confirmed_settings(session)

                if conf and _config_key(conf) != active_config:
                    if tick_collector:
                        await tick_collector.stop()
                    for qc in quote_collectors.values():
                        await qc.stop()
                    for task in quote_tasks:
                        task.cancel()
                    quote_tasks = []
                    quote_collectors = {}
                    generator.clear_models()
                    loaded_version_tags.clear()

                    active_config = _config_key(conf)
                    active_symbol = conf.symbol
                    tick_collector = TickCollector(client, active_symbol)
                    asyncio.create_task(tick_collector.start())

                    for direction, signed_barrier in iter_direction_barriers(
                        conf.barrier_input, conf.barrier_direction
                    ):
                        qc = QuoteCollector(
                            client=client,
                            symbol=active_symbol,
                            barrier=signed_barrier,
                            duration=conf.duration_value,
                            duration_unit=conf.duration_unit,
                            barrier_direction=direction,
                            interval=30.0,
                        )
                        quote_collectors[direction] = qc
                        quote_tasks.append(asyncio.create_task(qc.start()))

                    logger.info(
                        "collectors_started",
                        symbol=active_symbol,
                        directions=list(quote_collectors.keys()),
                        barriers={d: qc.barrier for d, qc in quote_collectors.items()},
                    )

                quotes_total = sum(qc._quote_count for qc in quote_collectors.values())
                await _publish_status({
                    "connected": client.connected,
                    "deriv_connected": client.connected,
                    "symbol": active_symbol,
                    "ticks_collected": tick_collector._tick_count if tick_collector else 0,
                    "quotes_collected": quotes_total,
                    "directions": list(quote_collectors.keys()),
                })

                if not conf:
                    await asyncio.sleep(5)
                    continue

                # Reload models when newer compatible metadata appears
                for direction in configured_directions(conf.barrier_direction):
                    meta = _load_latest_metadata(direction)
                    if not meta or not _metadata_compatible(meta, conf):
                        continue
                    tag = meta.get("version_tag", "")
                    if loaded_version_tags.get(direction) == tag:
                        continue
                    cal_path = meta.get("calibrator_path")
                    if not cal_path or not os.path.exists(cal_path):
                        continue
                    try:
                        bundle = ModelPipelineBundle.load(meta)
                        generator.set_model(
                            bundle=bundle,
                            version_id=hash(tag) % 1_000_000 or 1,
                            has_edge=bool(meta.get("has_demonstrated_edge", False)),
                            direction=direction,
                            metadata=meta,
                            version_tag=tag,
                        )
                        loaded_version_tags[direction] = tag
                        logger.info(
                            "model_loaded",
                            direction=direction,
                            version_tag=tag,
                            selected=meta.get("selected_pipeline"),
                            has_edge=meta.get("has_demonstrated_edge"),
                        )
                    except Exception as e:
                        logger.error("model_load_failed", direction=direction, error=str(e))

                async with async_session() as session:
                    result = await session.execute(
                        select(Tick)
                        .where(Tick.symbol == active_symbol)
                        .order_by(Tick.epoch.desc())
                        .limit(600)
                    )
                    ticks = result.scalars().all()

                if len(ticks) <= 100:
                    await asyncio.sleep(5)
                    continue

                df = pd.DataFrame([
                    {"epoch": t.epoch, "tick_time": t.tick_time, "quote": t.quote}
                    for t in reversed(ticks)
                ])
                barrier_dist = float(barrier_magnitude(conf.barrier_input))
                current_price = float(df.iloc[-1]["quote"])
                last_tick_age = time.time() - float(df.iloc[-1]["epoch"])

                features_by_direction = {}
                sequences_by_direction = {}
                entry_epoch = int(df.iloc[-1]["epoch"])
                for direction in configured_directions(conf.barrier_direction):
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

                quotes_by_direction = {}
                for direction, qc in quote_collectors.items():
                    q = await qc.fetch_single_quote()
                    if q:
                        quotes_by_direction[direction] = q

                async with async_session() as session:
                    signal_data = await generator.evaluate_and_generate(
                        session,
                        features_df=features_by_direction.get(
                            configured_directions(conf.barrier_direction)[0],
                            pd.DataFrame(),
                        ),
                        current_price=current_price,
                        barrier_distance=barrier_dist,
                        symbol=active_symbol,
                        quotes_by_direction=quotes_by_direction,
                        allowed_directions=configured_directions(conf.barrier_direction),
                        features_by_direction=features_by_direction,
                        sequences_by_direction=sequences_by_direction,
                        last_tick_age_seconds=last_tick_age,
                    )

                    if signal_data:
                        logger.info("signal_generated", data=signal_data)
                        await _publish_signal(signal_data)
                        await telegram.send_signal_alert(signal_data)

                    await lifecycle.expire_stale_signals(session)

            except Exception as e:
                logger.error("worker_loop_error", error=str(e))
                await _publish_status({
                    "connected": False,
                    "deriv_connected": False,
                    "symbol": active_symbol,
                    "ticks_collected": tick_collector._tick_count if tick_collector else 0,
                    "quotes_collected": sum(qc._quote_count for qc in quote_collectors.values()),
                })

            await asyncio.sleep(5)

    finally:
        if tick_collector:
            await tick_collector.stop()
        for qc in quote_collectors.values():
            await qc.stop()
        for task in quote_tasks:
            task.cancel()
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(worker_loop())
