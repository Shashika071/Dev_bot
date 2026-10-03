import asyncio
import os
import glob
from datetime import datetime, timezone, timedelta
import pandas as pd
import structlog
from sqlalchemy import select

from app.deriv.client import DerivWSClient
from app.collector.history_collector import HistoryCollector
from app.ml.trainer import TrainingOrchestrator
from app.database import async_session, init_db
from app.models.tick import Tick
from app.config import settings
from app.ml.catboost_model import CatBoostTouchModel
from app.ml.calibration import ProbabilityCalibrator

logger = structlog.get_logger(__name__)

async def run_training(days_back: int = 7):
    logger.info("starting_training_process", days_back=days_back)
    
    # 1. Connect to Deriv
    client = DerivWSClient()
    await client.connect()
    
    symbol = "R_100"
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days_back)
    
    # 2. Fetch Historical Data
    collector = HistoryCollector(client, symbol)
    logger.info("fetching_historical_data", start=start_dt, end=end_dt)
    
    # Fetch ticks (This saves to DB and Parquet)
    await collector.fetch_range(start_dt, end_dt, save_parquet=True)
    
    await client.disconnect()
    
    # 3. Load ticks from Database for training
    logger.info("loading_data_for_training")
    async with async_session() as session:
        result = await session.execute(
            select(Tick).where(Tick.symbol == symbol).order_by(Tick.epoch.asc())
        )
        ticks = result.scalars().all()
        
    df = pd.DataFrame([{"epoch": t.epoch, "tick_time": t.tick_time, "quote": t.quote} for t in ticks])
    
    if len(df) < 1000:
        logger.error("not_enough_data_for_training", count=len(df))
        return

    # 4. Run Training Orchestrator
    # We use a 9-minute duration (540s) and +0.09 upper barrier as default for Volatility 100
    orchestrator = TrainingOrchestrator(
        barrier_distance=0.09, 
        barrier_direction="upper", 
        duration_seconds=540
    )
    
    logger.info("training_ml_pipeline")
    results = orchestrator.train_full_pipeline(df, sampling_interval_seconds=60)
    
    if "error" in results:
        logger.error("training_failed", error=results["error"])
        return
        
    logger.info("training_complete", 
        catboost_win_rate=results.get("catboost", {}).get("selected_win_rate"),
        baseline_win_rate=results.get("catboost", {}).get("baseline_win_rate"),
        improvement=results.get("catboost", {}).get("improvement_over_baseline")
    )
    
    print("\n=== TRAINING SUCCESSFUL ===")
    print(f"Models saved to: /app/data/models/")
    if "catboost" in results:
        print(f"CatBoost Selected Win Rate: {results['catboost'].get('selected_win_rate', 0):.2%}")
        print(f"Baseline Win Rate: {results['catboost'].get('baseline_win_rate', 0):.2%}")
        
if __name__ == "__main__":
    asyncio.run(run_training(days_back=7))
