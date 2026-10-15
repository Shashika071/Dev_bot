"""Baselines, tree models, optional LSTM, and calibration.

Preprocessing is fit on the rows passed in. Callers must pass only the
training side of a chronological split.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from app.research_pipeline.evaluate import binary_logloss
from app.research_pipeline.settings import ExperimentConfig


def design_matrix(frame, columns: list[str], fills: dict | None = None) -> tuple[np.ndarray, dict]:
    values = frame.loc[:, columns].to_numpy(dtype=float)
    if fills is None:
        fills = {}
        for j, name in enumerate(columns):
            finite = values[:, j]
            finite = finite[np.isfinite(finite)]
            fills[name] = float(np.median(finite)) if len(finite) else 0.0
    for j, name in enumerate(columns):
        bad = ~np.isfinite(values[:, j])
        if bad.any():
            values[bad, j] = fills[name]
    return values, fills


def fit_constant(y: np.ndarray) -> dict:
    return {"kind": "constant", "p": float(np.mean(y))}


def fit_momentum(ret: np.ndarray, y: np.ndarray) -> dict:
    y = np.asarray(y, dtype=float)
    ret = np.asarray(ret, dtype=float)
    base = float(np.mean(y))

    def _rate(mask: np.ndarray) -> float:
        if int(mask.sum()) == 0:
            return base
        return float(np.mean(y[mask]))

    return {"kind": "momentum", "p_up": _rate(ret > 0), "p_down": _rate(ret <= 0)}


def fit_logistic(x: np.ndarray, y: np.ndarray, seed: int) -> dict:
    scaler = StandardScaler()
    scaled = scaler.fit_transform(x)
    clf = LogisticRegression(max_iter=500, random_state=seed)
    clf.fit(scaled, y.astype(int))
    return {"kind": "logistic", "scaler": scaler, "clf": clf}


def predict_bundle(bundle: dict, x: np.ndarray, ret: np.ndarray | None = None) -> np.ndarray:
    kind = bundle["kind"]
    if kind == "constant":
        return np.full(len(x), float(bundle["p"]))
    if kind == "momentum":
        if ret is None:
            raise ValueError("momentum prediction needs the return column")
        return np.where(np.asarray(ret) > 0, bundle["p_up"], bundle["p_down"]).astype(float)
    if kind == "logistic":
        scaled = bundle["scaler"].transform(x)
        return bundle["clf"].predict_proba(scaled)[:, 1]
    if kind == "xgboost":
        return bundle["clf"].predict_proba(x)[:, 1]
    if kind == "catboost":
        return bundle["clf"].predict_proba(x)[:, 1]
    if kind == "lstm":
        return _predict_lstm(bundle, x)
    raise ValueError(kind)


def fit_tree(kind: str, x: np.ndarray, y: np.ndarray, x_es, y_es, cfg: ExperimentConfig, depth: int) -> dict:
    y = np.asarray(y).astype(int)
    use_es = x_es is not None and len(y_es) >= 8 and len(np.unique(y_es)) == 2 and len(np.unique(y)) == 2
    leaf = 1 if len(y) < 100 else 5
    if kind == "xgboost":
        return {"kind": "xgboost", "clf": _fit_xgb(x, y, x_es, y_es, cfg, depth, use_es, leaf)}
    if kind == "catboost":
        return {"kind": "catboost", "clf": _fit_cat(x, y, x_es, y_es, cfg, depth, use_es, leaf)}
    raise ValueError(kind)


def choose_depth(kind: str, x, y, epochs, horizon: int, cfg: ExperimentConfig) -> int:
    depths = (2, 3) if kind == "xgboost" else (3, 4)
    if cfg.fast or len(y) < 50:
        return depths[0]
    fit, es = inner_masks(epochs, horizon)
    if int(fit.sum()) < 25 or int(es.sum()) < 8 or len(np.unique(y[fit])) < 2:
        return depths[0]
    best_depth = depths[0]
    best_loss = float("inf")
    for depth in depths:
        bundle = fit_tree(kind, x[fit], y[fit], x[es], y[es], cfg, depth)
        loss = binary_logloss(y[es], predict_bundle(bundle, x[es]))
        if loss < best_loss:
            best_loss = loss
            best_depth = depth
    return int(best_depth)


def inner_masks(epochs: np.ndarray, horizon: int, fraction: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    epochs = np.asarray(epochs, dtype=float)
    t0 = float(epochs.min())
    t1 = float(epochs.max())
    if t1 <= t0:
        fit = np.ones(len(epochs), dtype=bool)
        return fit, np.zeros(len(epochs), dtype=bool)
    cut = t0 + (1.0 - fraction) * (t1 - t0)
    early = epochs >= cut
    fit = epochs + int(horizon) <= cut
    return fit, early


def fit_calibrator(scores: np.ndarray, y: np.ndarray) -> dict:
    scores = np.asarray(scores, dtype=float)
    y = np.asarray(y).astype(int)
    if len(y) < 30 or len(np.unique(y)) < 2:
        return {"type": "identity"}
    if len(y) >= 80 and int(min(np.bincount(y))) >= 8:
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(scores, y)
        return {"type": "isotonic", "model": model}
    model = LogisticRegression(max_iter=200)
    model.fit(scores.reshape(-1, 1), y)
    return {"type": "platt", "model": model}


def apply_calibrator(calibrator: dict, scores: np.ndarray) -> np.ndarray:
    scores = np.asarray(scores, dtype=float)
    kind = calibrator.get("type", "identity")
    if kind == "identity":
        return np.clip(scores, 0.0, 1.0)
    if kind == "isotonic":
        return np.clip(calibrator["model"].predict(scores), 0.0, 1.0)
    return calibrator["model"].predict_proba(scores.reshape(-1, 1))[:, 1]


def train_lstm(train_seq, train_y, val_seq, val_y, cfg: ExperimentConfig) -> tuple[dict | None, dict]:
    try:
        import torch
        from torch import nn
    except ImportError:
        return None, {"status": "skipped", "reason": "torch is not installed"}
    train_y = np.asarray(train_y, dtype=float)
    val_y = np.asarray(val_y, dtype=float)
    if len(train_y) < 40 or len(val_y) < 8:
        return None, {"status": "skipped", "reason": "not enough sequences"}
    if len(np.unique(train_y)) < 2 or len(np.unique(val_y)) < 2:
        return None, {"status": "skipped", "reason": "one class in the LSTM split"}
    torch.manual_seed(int(cfg.seed))
    flat = train_seq.reshape(-1, train_seq.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)

    def norm(values):
        return (values - mean) / std

    device = torch.device("cpu")
    x_train = torch.tensor(norm(train_seq), dtype=torch.float32, device=device)
    y_train = torch.tensor(train_y, dtype=torch.float32, device=device)
    x_val = torch.tensor(norm(val_seq), dtype=torch.float32, device=device)
    y_val = torch.tensor(val_y, dtype=torch.float32, device=device)
    model = _TinyLSTM(train_seq.shape[-1], hidden=int(cfg.lstm_hidden), dropout=0.2).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.BCEWithLogitsLoss()
    best_state = None
    best_loss = float("inf")
    wait = 0
    for _ in range(int(cfg.lstm_epochs)):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(x_train), y_train)
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(x_val), y_val).item())
        if val_loss < best_loss - 1e-4:
            best_loss = val_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= int(cfg.lstm_patience):
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        probs = torch.sigmoid(model(x_val)).cpu().numpy()
    bundle = {
        "kind": "lstm",
        "state": best_state,
        "mean": mean,
        "std": std,
        "hidden": int(cfg.lstm_hidden),
        "n_features": int(train_seq.shape[-1]),
        "seq_len": int(train_seq.shape[1]),
    }
    return bundle, {
        "status": "trained",
        "val_logloss": binary_logloss(val_y, probs),
        "val_probabilities": probs,
    }


def _predict_lstm(bundle: dict, sequences: np.ndarray) -> np.ndarray:
    import torch

    mean = bundle["mean"]
    std = bundle["std"]
    model = _TinyLSTM(bundle["n_features"], hidden=bundle["hidden"], dropout=0.2)
    model.load_state_dict(bundle["state"])
    model.eval()
    values = (np.asarray(sequences, dtype=float) - mean) / std
    with torch.no_grad():
        logits = model(torch.tensor(values, dtype=torch.float32))
        return torch.sigmoid(logits).cpu().numpy()


class _TinyLSTM:
    """Constructed lazily so importing the package does not require torch."""

    def __new__(cls, n_features: int, hidden: int, dropout: float):
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(n_features, hidden, num_layers=1, batch_first=True)
                self.drop = nn.Dropout(dropout)
                self.head = nn.Linear(hidden, 1)

            def forward(self, inputs):
                out, _ = self.lstm(inputs)
                return self.head(self.drop(out[:, -1, :])).squeeze(-1)

        return Net()


def _fit_xgb(x, y, x_es, y_es, cfg: ExperimentConfig, depth: int, use_es: bool, leaf: int):
    from xgboost import XGBClassifier

    params = dict(
        n_estimators=int(cfg.tree_iterations if use_es else min(cfg.tree_iterations, 40)),
        max_depth=int(depth),
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        min_child_weight=int(leaf),
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=int(cfg.seed),
        n_jobs=1,
        verbosity=0,
    )
    if not use_es:
        model = XGBClassifier(**params)
        model.fit(x, y)
        return model
    try:
        model = XGBClassifier(**params, early_stopping_rounds=int(cfg.early_stopping_rounds))
        model.fit(x, y, eval_set=[(x_es, np.asarray(y_es).astype(int))], verbose=False)
        return model
    except TypeError:
        from xgboost.callback import EarlyStopping

        model = XGBClassifier(**params)
        model.fit(
            x,
            y,
            eval_set=[(x_es, np.asarray(y_es).astype(int))],
            verbose=False,
            callbacks=[EarlyStopping(rounds=int(cfg.early_stopping_rounds), save_best=True)],
        )
        return model


def _fit_cat(x, y, x_es, y_es, cfg: ExperimentConfig, depth: int, use_es: bool, leaf: int):
    import tempfile

    from catboost import CatBoostClassifier

    params = dict(
        iterations=int(cfg.tree_iterations if use_es else min(cfg.tree_iterations, 40)),
        depth=int(depth),
        learning_rate=0.05,
        l2_leaf_reg=5.0,
        min_data_in_leaf=int(leaf),
        loss_function="Logloss",
        random_seed=int(cfg.seed),
        allow_writing_files=False,
        train_dir=tempfile.mkdtemp(prefix="catboost-research-"),
        thread_count=1,
        logging_level="Silent",
    )
    if use_es:
        params["early_stopping_rounds"] = int(cfg.early_stopping_rounds)
    model = CatBoostClassifier(**params)
    if use_es:
        model.fit(x, y, eval_set=(x_es, np.asarray(y_es).astype(int)), use_best_model=True)
    else:
        model.fit(x, y)
    return model
