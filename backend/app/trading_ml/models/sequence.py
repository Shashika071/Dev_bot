"""Optional sequence research. Results are withheld from the signal path."""
from __future__ import annotations

import numpy as np


def sequence_status() -> dict:
    try:
        import torch
        version = torch.__version__
        available = True
    except ImportError:
        version = None
        available = False
    reason = (
        "LSTM, GRU and temporal CNN research can be run explicitly. "
        "This signal evaluation did not use them."
        if available else
        "PyTorch is not installed. LSTM, GRU and temporal CNN were not run, and no sequence result was invented."
    )
    return {"available": available, "torch": version, "used_for_signal": False, "results": None, "reason": reason}


def _windows(returns: np.ndarray, labels: np.ndarray, length: int = 16):
    rows, targets = [], []
    for index in range(length, len(returns)):
        window = returns[index - length:index]
        if np.isfinite(window).all() and np.isfinite(labels[index]):
            rows.append(window)
            targets.append(labels[index])
    if not rows:
        return np.empty((0, length, 1)), np.empty(0)
    return np.asarray(rows, dtype=np.float32)[:, :, None], np.asarray(targets, dtype=np.float32)


def run_sequence_benchmark(returns: np.ndarray, labels: np.ndarray) -> dict:
    """Train tiny CPU sequence models and return their held-out log loss."""
    status = sequence_status()
    if not status["available"]:
        return status
    import torch
    from torch import nn

    values = np.asarray(returns, dtype=float)
    target = np.asarray(labels, dtype=float)
    features, outputs = _windows(values, target)
    if len(outputs) < 40:
        return {**status, "reason": "Not enough aligned sequences to run the sequence benchmark."}
    cut = int(len(outputs) * 0.8)
    if len(set(outputs[:cut].tolist())) < 2 or len(outputs[cut:]) < 8:
        return {**status, "reason": "The sequence split does not contain both outcomes and a test tail."}

    class Net(nn.Module):
        def __init__(self, kind: str):
            super().__init__()
            if kind == "lstm":
                self.core = nn.LSTM(1, 8, batch_first=True)
            elif kind == "gru":
                self.core = nn.GRU(1, 8, batch_first=True)
            else:
                self.core = nn.Sequential(nn.Conv1d(1, 8, 3), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
            self.kind = kind
            self.head = nn.Linear(8, 1)

        def forward(self, batch):
            if self.kind == "cnn":
                hidden = self.core(batch.transpose(1, 2)).squeeze(-1)
            else:
                hidden = self.core(batch)[0][:, -1]
            return self.head(hidden).squeeze(-1)

    results = {}
    loss_fn = nn.BCEWithLogitsLoss()
    for kind in ("lstm", "gru", "cnn"):
        torch.manual_seed(41)
        model = Net(kind)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
        train_x = torch.tensor(features[:cut])
        train_y = torch.tensor(outputs[:cut])
        model.train()
        for _ in range(8):
            optimizer.zero_grad()
            loss = loss_fn(model(train_x), train_y)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            logits = model(torch.tensor(features[cut:])).numpy()
        probability = 1 / (1 + np.exp(-logits))
        probability = np.clip(probability, 1e-6, 1 - 1e-6)
        actual = outputs[cut:]
        log_loss = float(-(actual * np.log(probability) + (1 - actual) * np.log(1 - probability)).mean())
        results[kind] = {"test_log_loss": log_loss, "test_rows": int(len(actual)), "used_for_signal": False}
    return {
        **status, "results": results, "used_for_signal": False,
        "reason": "Sequence models were scored on a later time slice. They do not vote on the signal.",
    }
