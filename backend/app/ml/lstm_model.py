"""
LSTM touch-probability model using price sequences + contract context.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class LSTMTouchModel:
    """Small PyTorch LSTM classifier for touch probability."""

    name = "lstm_touch"

    def __init__(
        self,
        direction: str = "upper",
        hidden_size: int = 32,
        num_layers: int = 1,
        dropout: float = 0.1,
        lr: float = 1e-3,
        max_epochs: int = 15,
        batch_size: int = 64,
        patience: int = 3,
        random_state: int = 42,
    ):
        self.direction = direction
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.dropout = dropout
        self.lr = lr
        self.max_epochs = max_epochs
        self.batch_size = batch_size
        self.patience = patience
        self.random_state = random_state
        self._torch = None
        self._model = None
        self._device = "cpu"
        self._is_fitted = False
        self._input_size: Optional[int] = None

    def _ensure_torch(self):
        if self._torch is not None:
            return self._torch
        try:
            import torch
            import torch.nn as nn
        except ImportError as e:
            raise RuntimeError(
                "PyTorch is required for LSTM training. Install torch (CPU wheel is fine)."
            ) from e
        self._torch = torch
        self._nn = nn
        return torch

    def _build_net(self, input_size: int):
        torch = self._ensure_torch()
        nn = self._nn

        class _Net(nn.Module):
            def __init__(self, in_size, hidden, layers, dropout):
                super().__init__()
                self.lstm = nn.LSTM(
                    input_size=in_size,
                    hidden_size=hidden,
                    num_layers=layers,
                    batch_first=True,
                    dropout=dropout if layers > 1 else 0.0,
                )
                self.head = nn.Sequential(
                    nn.Linear(hidden, hidden),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden, 1),
                )

            def forward(self, x):
                out, _ = self.lstm(x)
                last = out[:, -1, :]
                return self.head(last).squeeze(-1)

        return _Net(input_size, self.hidden_size, self.num_layers, self.dropout)

    def fit(
        self,
        X_seq_train: np.ndarray,
        y_train: np.ndarray,
        X_seq_val: np.ndarray | None = None,
        y_val: np.ndarray | None = None,
    ):
        torch = self._ensure_torch()
        torch.manual_seed(self.random_state)
        np.random.seed(self.random_state)

        if X_seq_train.ndim != 3 or len(X_seq_train) == 0:
            raise ValueError("LSTM requires non-empty sequences of shape (N, T, C)")

        self._input_size = int(X_seq_train.shape[-1])
        self._model = self._build_net(self._input_size).to(self._device)
        opt = torch.optim.Adam(self._model.parameters(), lr=self.lr)
        loss_fn = self._nn.BCEWithLogitsLoss()

        X_t = torch.tensor(X_seq_train, dtype=torch.float32)
        y_t = torch.tensor(np.asarray(y_train, dtype=np.float32).ravel())

        has_val = X_seq_val is not None and y_val is not None and len(X_seq_val) > 0
        if has_val:
            Xv = torch.tensor(X_seq_val, dtype=torch.float32)
            yv = torch.tensor(np.asarray(y_val, dtype=np.float32).ravel())

        best_state = None
        best_val = float("inf")
        stale = 0

        n = len(X_t)
        for epoch in range(self.max_epochs):
            self._model.train()
            perm = torch.randperm(n)
            total_loss = 0.0
            batches = 0
            for start in range(0, n, self.batch_size):
                idx = perm[start : start + self.batch_size]
                xb, yb = X_t[idx], y_t[idx]
                opt.zero_grad()
                logits = self._model(xb)
                loss = loss_fn(logits, yb)
                loss.backward()
                opt.step()
                total_loss += float(loss.item())
                batches += 1

            val_loss = total_loss / max(batches, 1)
            if has_val:
                self._model.eval()
                with torch.no_grad():
                    val_loss = float(loss_fn(self._model(Xv), yv).item())
                if val_loss < best_val - 1e-5:
                    best_val = val_loss
                    best_state = {k: v.detach().cpu().clone() for k, v in self._model.state_dict().items()}
                    stale = 0
                else:
                    stale += 1
                    if stale >= self.patience:
                        break

            logger.info(
                "lstm_epoch",
                epoch=epoch + 1,
                train_loss=round(total_loss / max(batches, 1), 5),
                val_loss=round(val_loss, 5) if has_val else None,
            )

        if best_state is not None:
            self._model.load_state_dict(best_state)
        self._is_fitted = True
        logger.info("lstm_fitted", direction=self.direction, n_train=len(X_seq_train))

    def predict_proba(self, X_seq: np.ndarray) -> np.ndarray:
        if not self._is_fitted or self._model is None:
            raise RuntimeError("LSTM model not fitted")
        torch = self._ensure_torch()
        if len(X_seq) == 0:
            return np.array([], dtype=float)
        self._model.eval()
        with torch.no_grad():
            xt = torch.tensor(X_seq, dtype=torch.float32)
            logits = self._model(xt)
            probs = torch.sigmoid(logits).cpu().numpy()
        return np.asarray(probs, dtype=float).ravel()

    def save(self, path: str):
        if not self._is_fitted or self._model is None:
            raise RuntimeError("Refusing to save unfitted LSTM")
        torch = self._ensure_torch()
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        payload = {
            "state_dict": self._model.state_dict(),
            "input_size": self._input_size,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "direction": self.direction,
        }
        torch.save(payload, path)

    def load(self, path: str):
        torch = self._ensure_torch()
        payload = torch.load(path, map_location="cpu", weights_only=False)
        self.direction = payload.get("direction", self.direction)
        self.hidden_size = payload["hidden_size"]
        self.num_layers = payload["num_layers"]
        self.dropout = payload["dropout"]
        self._input_size = payload["input_size"]
        self._model = self._build_net(self._input_size)
        self._model.load_state_dict(payload["state_dict"])
        self._model.eval()
        self._is_fitted = True
        logger.info("lstm_loaded", path=path)
