import pytest

from app.deriv.barriers import touch_duration, touch_order_fields


def test_upper_and_lower_touch_barriers_are_signed():
    upper = touch_order_fields("upper", "0.09", 1.5)
    lower = touch_order_fields("lower", "+0.09", 1.5)
    assert upper == {"direction": "upper", "barrier": "+0.09", "stake": 1.5}
    assert lower["barrier"] == "-0.09"


def test_chart_duration_accepts_minutes_and_rejects_zero():
    assert touch_duration(5, "m") == (5, "m")
    assert touch_duration("30", "s") == (30, "s")
    with pytest.raises(ValueError):
        touch_duration(0, "m")
    with pytest.raises(ValueError):
        touch_order_fields("call", "0.09", 1)
    with pytest.raises(ValueError):
        touch_order_fields("upper", "0.09", 0)
