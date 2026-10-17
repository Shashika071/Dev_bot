"""Small fixed model comparison with separate fit/calibration/selection/test periods."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .core import (BARRIER, COLOMBO_OFFSET, DELAY, DURATION, SCHEMA, SYMBOL,
                   SignalPolicy, features, side_matrix, touch_labels, validate_ticks, wilson)


def logit(values):
    q = np.clip(np.asarray(values, dtype=float), 1e-6, 1-1e-6)
    return np.log(q/(1-q)).reshape(-1,1)


@dataclass
class Predictor:
    name: str
    feature_names: list[str]
    frequency: tuple[float, float] | None = None
    estimator: object = None
    calibrator: object = None
    feature_profile: str = "compact_v1"

    @property
    def history_seconds(self):
        if getattr(self, "feature_profile", "compact_v1") == "technical_v2":
            from .technical import HISTORY_SECONDS
            return HISTORY_SECONDS
        return 900

    def predict(self, matrix):
        x = np.asarray(matrix, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.feature_names) or not np.isfinite(x).all():
            raise ValueError("Invalid feature matrix")
        if self.frequency is not None:
            return np.tile(self.frequency, (len(x),1))
        raw = self.estimator.predict_proba(side_matrix(x))[:,1]
        calibrated = self.calibrator.predict_proba(logit(raw))[:,1]
        return np.column_stack([calibrated[:len(x)], calibrated[len(x):]])

    def snapshot(self, t, p, epoch):
        row = features(t,p,int(epoch))
        if row is None:
            return None
        if getattr(self, "feature_profile", "compact_v1") == "technical_v2":
            from .technical import technical_features
            technical = technical_features(t, p, int(epoch))
            if technical is None:
                return None
            row.update(technical)
        return self.predict([[row[name] for name in self.feature_names]])[0]


def json_save(path: Path, content):
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(content, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def summarize(decisions, labels, predictions, threshold, start, end):
    policy = SignalPolicy(min_probability=threshold)
    selected, wins = [], []
    for epoch, pair, target in zip(decisions, predictions, labels):
        side = policy.choose(int(epoch), pair)
        if side:
            selected.append(int(epoch)); wins.append(int(target[int(side == "lower")]))
    wins = np.asarray(wins)
    n = len(wins); w = int(wins.sum())
    # Include full calendar days with zero signals, so abstention is visible.
    first = int(np.ceil((start+COLOMBO_OFFSET)/86400))
    last = int(np.floor((end+COLOMBO_OFFSET)/86400))
    days = np.arange(first,last)
    counts = np.zeros(len(days)); successes = np.zeros(len(days))
    for epoch, outcome in zip(selected, wins):
        day = (epoch+COLOMBO_OFFSET)//86400
        if first <= day < last:
            counts[day-first] += 1; successes[day-first] += outcome
    ci = None
    if len(days)>=3 and counts.sum()>0:
        ids = np.random.default_rng(20261018).integers(0,len(days),(2000,len(days)))
        denominators = counts[ids].sum(axis=1)
        rates = successes[ids].sum(axis=1)/np.maximum(denominators,1)
        ci = np.quantile(rates[denominators>0],[.025,.975]).tolist()
    return {"signals":n, "touches":w, "misses":n-w, "touch_rate":w/n if n else None,
            "wilson_95":list(wilson(w,n)), "full_day_cluster_ci95":ci,
            "full_day_signal_count":int(counts.sum()), "full_days":len(days),
            "signals_per_full_day":float(counts.mean()) if len(days) else 0,
            "threshold":threshold, "cap":15, "cooldown_seconds":5760}


def train(source: Path, output: Path, *, feature_profile="compact_v1", activate=True):
    if feature_profile == "technical_v2":
        from .technical_training import train_technical
        return train_technical(source, output, activate=activate)
    if feature_profile != "compact_v1":
        raise ValueError("Unknown feature profile")
    source, output = source.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run = output/run_id; run.mkdir()
    plan = {"schema":SCHEMA, "symbol":SYMBOL, "barrier_points":BARRIER,
            "duration_seconds":DURATION, "processing_delay_seconds":DELAY,
            "split":"45% fit / 15% calibration / 20% selection / 20% diagnostic test",
            "source":source.name, "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
            "status":"exploratory historical replay; fresh forward validation required",
            "models":["frequency","logistic","xgboost","catboost"],
            "calibration":"each fitted candidate has independent Platt calibration on held-out logit scores",
            "thresholds":[.95,.96,.97,.975,.98,.985,.99],
            "selection":"highest selection-period day-cluster lower touch bound with 10–15 signals/full day; tie lower Brier then model name",
            "cap":15, "cooldown_seconds":5760, "candidate_spacing_seconds":600,
            "objective":"touch accuracy; no payout or trade execution dependency"}
    json_save(run/"protocol.json",plan)
    table = pq.read_table(source)
    if "symbol" in table.column_names and set(table["symbol"].to_pylist()) != {SYMBOL}:
        raise ValueError("This profile accepts an R_100-only tick file")
    t,p = validate_ticks(np.asarray(table["epoch"]),np.asarray(table["quote"]))
    del table
    lo,hi = int(t[0]),int(t[-1])
    cuts = [lo]+[lo+int(f*(hi-lo)) for f in (.45,.6,.8)]+[hi]
    bounds = dict(zip(("fit","calibration","selection","test"),zip(cuts[:-1],cuts[1:])))
    first = ((lo+900+COLOMBO_OFFSET+599)//600)*600-COLOMBO_OFFSET
    records=[]; names=None
    for epoch in range(first,hi-DELAY-DURATION,600):
        row = features(t,p,epoch); target = touch_labels(t,p,epoch)
        if row is not None and target is not None:
            names = list(row)
            records.append((epoch,list(row.values()),target))
    if not records:
        raise ValueError("No complete, continuous labeled windows")
    d = np.array([r[0] for r in records]); x=np.array([r[1] for r in records]); y=np.array([r[2] for r in records])
    masks = {name:(d>=a)&(d+DELAY+DURATION<b) for name,(a,b) in bounds.items()}
    for name,mask in masks.items():
        if mask.sum()<200 or len(np.unique(y[mask]))<2:
            raise ValueError(f"Need >=200 decisions and both outcomes in {name}; collect more history")
    fitx=side_matrix(x[masks["fit"]]); fity=y[masks["fit"]].T.ravel()
    calx=side_matrix(x[masks["calibration"]]); caly=y[masks["calibration"]].T.ravel()
    models={"frequency":Predictor("frequency",names,tuple(y[masks["calibration"]].mean(axis=0)))}
    errors={}
    for name in ("logistic","xgboost","catboost"):
        try:
            if name=="logistic":
                estimator=make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=500))
            elif name=="xgboost":
                from xgboost import XGBClassifier
                estimator=XGBClassifier(objective="binary:logistic",n_estimators=80,max_depth=2,
                    learning_rate=.05,min_child_weight=20,reg_lambda=10,n_jobs=1,
                    random_state=20261018,tree_method="hist",verbosity=0)
            else:
                from catboost import CatBoostClassifier
                estimator=CatBoostClassifier(iterations=80,depth=3,learning_rate=.05,l2_leaf_reg=10,
                    loss_function="Logloss",thread_count=1,task_type="CPU",random_seed=20261018,
                    allow_writing_files=False,verbose=False)
            estimator.fit(fitx,fity)
            calibrator=LogisticRegression(C=1,max_iter=500).fit(logit(estimator.predict_proba(calx)[:,1]),caly)
            models[name]=Predictor(name,names,estimator=estimator,calibrator=calibrator)
            print(f"Fitted {name}",flush=True)
        except (ImportError,ValueError,RuntimeError) as exc:
            errors[name]=str(exc)
    # Freeze the full policy before computing final-period model metrics.
    dev=masks["selection"]; trials=[]
    for name,model in models.items():
        q=model.predict(x[dev]); brier=float(brier_score_loss(y[dev].ravel(),q.ravel()))
        for threshold in plan["thresholds"]:
            stats=summarize(d[dev],y[dev],q,threshold,*bounds["selection"])
            trials.append({"model":name,"brier":brier,**stats})
    eligible=[r for r in trials if r["signals"]>=100 and 10<=r["signals_per_full_day"]<=15 and r["full_day_cluster_ci95"]]
    if not eligible:
        json_save(run/"no_policy.json",{"trials":trials,"reason":"No candidate met accuracy/coverage requirements"})
        raise ValueError("No candidate supports the requested coverage at >=95% estimated confidence")
    selected=min(eligible,key=lambda r:(-r["full_day_cluster_ci95"][0],r["brier"],r["model"],-r["threshold"]))
    json_save(run/"selection.json",selected)
    model=models[selected["model"]]
    comparisons={}
    for name,candidate in models.items():
        comparisons[name]={}
        for phase in ("selection","test"):
            m=masks[phase]; q=candidate.predict(x[m]); target=y[m].ravel()
            comparisons[name][phase]={"brier":float(brier_score_loss(target,q.ravel())),
                "auc":float(roc_auc_score(target,q.ravel())), "decisions":int(m.sum()),
                "policy":summarize(d[m],y[m],q,selected["threshold"],*bounds[phase])}
    report={**plan,"run_id":run_id,"ticks":len(t),"span_days":(hi-lo)/86400,
            "feature_count":len(names),"side_feature_count":2*len(names)+1,
            "split_counts":{k:int(v.sum()) for k,v in masks.items()},"bounds":bounds,
            "selection":selected,"comparison":comparisons,"model_errors":errors,
            "test":comparisons[selected["model"]]["test"],"forward_validation":"pending"}
    artifact={"schema":SCHEMA,"predictor":model,"threshold":selected["threshold"],"report":report}
    with (run/"bundle.pkl").open("wb") as stream:
        pickle.dump(artifact,stream)
    json_save(run/"report.json",report)
    if activate:
        json_save(output/"latest.json",{"run_id":run_id})
    print(json.dumps({"run_id":run_id,"selected":selected["model"],"test":report["test"]},indent=2),flush=True)
    return report


def load_artifact(output: Path):
    run_id=json.loads((output/"latest.json").read_text(encoding="utf-8"))["run_id"]
    if not run_id or Path(run_id).name!=run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("Invalid artifact directory")
    # Only locally generated bundles in the app-owned accuracy directory are loaded.
    with (output/run_id/"bundle.pkl").open("rb") as stream:
        bundle=pickle.load(stream)
    if bundle.get("schema")!=SCHEMA:
        raise ValueError("Incompatible accuracy model; retrain this profile")
    predictor = bundle["predictor"]
    profile = getattr(predictor, "feature_profile", "compact_v1")
    if profile not in {"compact_v1", "technical_v2"}:
        raise ValueError("Unknown model feature profile; retrain this profile")
    if profile == "technical_v2":
        from .technical import FEATURE_GROUPS, FEATURE_NAMES
        report = bundle["report"]
        sample_t = np.arange(0, 1202, 2)
        expected = list(features(sample_t, np.full(len(sample_t), 100.), 1200)) + list(FEATURE_NAMES)
        if (bundle.get("feature_profile") != profile or report.get("feature_profile") != profile
                or report.get("feature_names") != predictor.feature_names or predictor.feature_names != expected):
            raise ValueError("Feature profile or column order does not match the trained model")
        if set(report.get("feature_groups", {})) != set(FEATURE_GROUPS):
            raise ValueError("Technical feature groups do not match the trained model")
    return bundle
