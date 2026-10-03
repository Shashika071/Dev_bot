"""
Model version tracking — records which ML model version generated each signal.
"""

from sqlalchemy import Column, Integer, Float, DateTime, String, Text, Boolean
from sqlalchemy.sql import func
from app.database import Base


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- Model Identity ---
    model_name = Column(String(64), nullable=False, comment="e.g. catboost_upper_touch")
    version_tag = Column(String(64), nullable=False, comment="e.g. v1.0.0-20240101")
    algorithm = Column(String(32), nullable=False, comment="catboost, xgboost, logistic, baseline")

    # --- Training Details ---
    train_start = Column(DateTime(timezone=True), nullable=True)
    train_end = Column(DateTime(timezone=True), nullable=True)
    val_start = Column(DateTime(timezone=True), nullable=True)
    val_end = Column(DateTime(timezone=True), nullable=True)
    test_start = Column(DateTime(timezone=True), nullable=True)
    test_end = Column(DateTime(timezone=True), nullable=True)
    n_train_samples = Column(Integer, nullable=True)
    n_val_samples = Column(Integer, nullable=True)
    n_test_samples = Column(Integer, nullable=True)

    # --- Features ---
    feature_names = Column(Text, nullable=True, comment="JSON list of feature names")
    n_features = Column(Integer, nullable=True)

    # --- Metrics ---
    brier_score = Column(Float, nullable=True)
    log_loss = Column(Float, nullable=True)
    auc_roc = Column(Float, nullable=True)
    calibration_slope = Column(Float, nullable=True)
    calibration_intercept = Column(Float, nullable=True)
    selected_signal_win_rate = Column(Float, nullable=True)
    selected_signal_count = Column(Integer, nullable=True)

    # --- Walk-Forward ---
    walk_forward_folds = Column(Integer, nullable=True)
    purge_gap_seconds = Column(Integer, nullable=True)

    # --- Status ---
    is_active = Column(Boolean, default=False,
                       comment="Only one model per direction should be active")
    has_demonstrated_edge = Column(Boolean, default=False,
                                    comment="True only after validation shows advantage")
    edge_description = Column(Text, nullable=True)

    # --- File Paths ---
    model_path = Column(String(512), nullable=True, comment="Path to saved model file")
    scaler_path = Column(String(512), nullable=True, comment="Path to saved scaler")
    calibrator_path = Column(String(512), nullable=True, comment="Path to saved calibrator")

    # --- Parameters ---
    hyperparameters = Column(Text, nullable=True, comment="JSON of hyperparameters")
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<ModelVersion(name={self.model_name}, version={self.version_tag}, "
            f"active={self.is_active}, edge={self.has_demonstrated_edge})>"
        )
