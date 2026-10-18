"""Behavioral checks for timing, no future information, and persistent signal limits."""
import asyncio
import json
import pickle

import numpy as np
import pytest

from app.accuracy.core import (COLOMBO_OFFSET, SignalPolicy, features, market_trend_bias,
                               side_matrix, touch_labels, validate_ticks)
from app.accuracy.service import AccuracyService
from app.accuracy.training import Predictor, summarize


def test_feature_snapshot_ignores_future_and_open_candle():
    t=np.arange(0,2402,2); p=100+np.sin(t/30)*.2
    before=features(t,p,1200)
    changed=p.copy(); changed[t>1200]=1000
    assert features(t,changed,1200)==before
    # A closed one-minute candle ends strictly before decision 1200.
    changed=p.copy(); changed[t==1200]=1000
    after=features(t,changed,1200)
    for key in before:
        if key.startswith("candle"):
            assert before[key]==after[key]
    assert before["move_5s"]!=after["move_5s"]


def test_touch_does_not_count_entry_tick_as_a_touch_of_previous_spot():
    t=np.arange(0,604,2); p=np.full(len(t),100.)
    # Decision0, processing5, anchor tick6. Anchor jumps up, then stays there.
    p[t>=6]=101.
    assert touch_labels(t,p,0)==(0,0)
    p[t==8]=101.01
    assert touch_labels(t,p,0)==(1,0)


def test_touch_uses_processing_expiry_not_anchor_expiry():
    t=np.arange(0,604,2); p=np.full(len(t),100.)
    p[t==546]=100.01  # Expiry is545, not first-entry-tick6+540=546.
    assert touch_labels(t,p,0)==(0,0)
    p[t==544]=99.99
    assert touch_labels(t,p,0)==(0,1)


def test_both_sides_can_touch_and_gaps_are_unresolved():
    t=np.arange(0,604,2); p=np.full(len(t),100.)
    p[t==8]=100.01; p[t==10]=99.99
    assert touch_labels(t,p,0)==(1,1)
    keep=(t<100)|(t>110)
    assert touch_labels(t[keep],p[keep],0) is None
    assert touch_labels(t[t<544],p[t<544],0) is None


def test_features_reject_stale_ticks_and_internal_gap():
    t=np.arange(0,1202,2); p=np.full(len(t),100.)
    assert features(t,p,1210) is None
    keep=(t<1000)|(t>1010)
    assert features(t[keep],p[keep],1200) is None


def test_policy_does_not_fill_quota_or_accept_invalid_probabilities():
    policy=SignalPolicy(min_probability=.97)
    assert policy.choose(0,[.96,.965]) is None
    assert policy.choose(0,[float("nan"),.99]) is None
    assert policy.choose(0,[1.1,.99]) is None
    assert policy.choose(0,[.98,.99])=="lower"
    assert policy.choose(5759,[.99,.99]) is None
    assert policy.choose(5760,[.99,.98])=="upper"


def test_trend_filter_blocks_counter_trend_without_consuming_slot():
    policy=SignalPolicy(min_probability=.95,cooldown_seconds=600)
    assert policy.choose(0,[.99,.90],trend_bias=-1) is None  # upper vs downtrend
    assert policy.counts=={} and policy.last_epoch==-10**18
    assert policy.choose(0,[.90,.99],trend_bias=-1)=="lower"
    assert policy.choose(700,[.99,.90],trend_bias=1)=="upper"
    assert policy.choose(1400,[.90,.99],trend_bias=1) is None  # lower vs uptrend


def test_market_trend_bias_needs_short_and_long_agreement():
    t=np.arange(0,3601,1,dtype=np.int64)
    down=100-t.astype(float)*0.001
    assert market_trend_bias(t,down,3600)==-1
    up=100+t.astype(float)*0.001
    assert market_trend_bias(t,up,3600)==1
    flat=np.full(len(t),100.0)
    assert market_trend_bias(t,flat,3600)==0


def test_cap_resets_at_colombo_midnight_but_cooldown_survives_it():
    policy=SignalPolicy(cooldown_seconds=600,max_per_day=2)
    midnight=86400-COLOMBO_OFFSET
    assert policy.choose(midnight-1200,[.98,.97])=="upper"
    assert policy.choose(midnight-600,[.98,.97])=="upper"
    assert policy.choose(midnight-1,[.99,.99]) is None
    assert policy.choose(midnight,[.98,.97])=="upper"
    assert policy.choose(midnight+1,[.98,.97]) is None


def test_side_features_match_target_order_and_bundle_round_trip():
    x=np.array([[1.,2.],[3.,4.]])
    extended=side_matrix(x)
    np.testing.assert_array_equal(extended[:2,-2:],x)
    np.testing.assert_array_equal(extended[2:,-2:],-x)
    model=pickle.loads(pickle.dumps(Predictor("frequency",["a","b"],(.97,.96))))
    np.testing.assert_allclose(model.predict(x),[[.97,.96],[.97,.96]])


def test_restart_restores_caps_and_pending_outcomes(tmp_path):
    signal={"type":"signal","signal_id":"a","decision_epoch":10000,"resolved":False}
    (tmp_path/"signals.jsonl").write_text(json.dumps(signal)+"\n",encoding="utf-8")
    service=AccuracyService(tmp_path); service.policy=SignalPolicy(); service._restore()
    assert service.policy.last_epoch==10000
    assert service.policy.choose(10001,[.99,.99]) is None
    assert "a" in service.pending


def test_reset_signals_clears_history_and_slots(tmp_path):
    signal={"type":"signal","signal_id":"a","decision_epoch":10000,"resolved":False}
    (tmp_path/"signals.jsonl").write_text(json.dumps(signal)+"\n",encoding="utf-8")
    service=AccuracyService(tmp_path); service.policy=SignalPolicy(); service._restore()
    assert "a" in service.pending
    out=asyncio.run(service.reset_signals())
    assert out["reset"] is True
    assert out["cleared_events"]==1
    assert not service.pending and list(service.recent)==[]
    assert service.policy.last_epoch==-10**18
    assert service.policy.counts=={}
    assert not (tmp_path/"signals.jsonl").exists()
    assert service.policy.choose(10001,[.99,.99])=="upper"


def test_replay_is_online_and_does_not_rank_later_entries_into_earlier_slots():
    d=np.arange(-COLOMBO_OFFSET,86400-COLOMBO_OFFSET,600)
    q=np.tile([.96,.95],(len(d),1)); y=np.tile([1,0],(len(d),1))
    q[-1]=[.999,.999]
    stats=summarize(d,y,q,.95,-COLOMBO_OFFSET,86400-COLOMBO_OFFSET)
    assert stats["signals"]==15
    assert stats["touches"]==15
    assert stats["signals_per_full_day"]==15


def test_duplicate_tick_cannot_emit_a_second_signal(tmp_path,monkeypatch):
    async def notification(*args,**kwargs):
        pass
    monkeypatch.setattr("app.notifications.browser.send_notification",notification)
    monkeypatch.setattr("app.accuracy.service.time.time",lambda:1200)
    service=AccuracyService(tmp_path)
    t=np.arange(0,1200,2); p=100+np.sin(t/30)*.2
    names=list(features(np.append(t,1200),np.append(p,100),1200))
    service.ticks.extend(zip(t.tolist(),p.tolist()))
    service.policy=SignalPolicy()
    service.bundle={"predictor":Predictor("frequency",names,(.97,.96)),"report":{"run_id":"test"}}
    asyncio.run(service.on_tick({"tick":{"epoch":1200,"quote":100,"symbol":"R_100"}}))
    asyncio.run(service.on_tick({"tick":{"epoch":1200,"quote":100,"symbol":"R_100"}}))
    assert len(service.pending)==1
    assert len((tmp_path/"signals.jsonl").read_text().splitlines())==1


def test_invalid_tick_order_rejected():
    with pytest.raises(ValueError):
        validate_ticks([2,1],[100,100])


def test_late_boundary_tick_does_not_emit_or_consume_decision(tmp_path,monkeypatch):
    monkeypatch.setattr("app.accuracy.service.time.time",lambda:1208)
    service=AccuracyService(tmp_path)
    service.policy=SignalPolicy()
    service.ticks.extend((epoch,100.) for epoch in range(0,1200,2))
    asyncio.run(service.on_tick({"tick":{"epoch":1200,"quote":100,"symbol":"R_100"}}))
    assert service.last_evaluated==-1
    assert not service.pending
    assert service.status()["feed_fresh"] is False
