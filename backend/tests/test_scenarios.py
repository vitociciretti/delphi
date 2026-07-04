"""
Unit tests for the scenario / domain preset layer.

These run without any external services (no LLM, Zep or OASIS), so they are the
primary verification for the scenario feature. Run with::

    cd backend && python -m pytest tests/test_scenarios.py -q
    # or, without pytest installed:
    cd backend && python tests/test_scenarios.py
"""

import json
import os
import sys

# The scenario layer is intentionally free of Flask / app dependencies, so we
# import it as a standalone package (``app/scenarios``) rather than via
# ``app`` — that keeps these tests runnable without the web stack installed.
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, _APP_DIR)

from scenarios import (  # noqa: E402
    ScenarioPreset,
    ScenarioRegistry,
    ScenarioLoadError,
    ActivityRhythm,
    DEFAULT_SCENARIO_ID,
)


def _fresh_registry():
    return ScenarioRegistry()


def test_builtin_presets_load_and_validate():
    reg = _fresh_registry()
    ids = reg.ids()
    assert DEFAULT_SCENARIO_ID in ids
    # Every shipped preset must be structurally valid.
    for preset in reg.all():
        assert preset.validate() == [], f"{preset.id} failed validation"
        assert preset.builtin is True


def test_default_reproduces_social_media_behaviour():
    reg = _fresh_registry()
    default = reg.default()
    assert default.id == "social_media"
    # The original engine defaults must be preserved for backward compatibility.
    assert default.default_total_hours == 72
    assert default.default_minutes_per_round == 60
    rhythm = default.activity_rhythm
    assert rhythm.peak_hours == [19, 20, 21, 22]
    assert rhythm.peak_multiplier == 1.5
    assert rhythm.off_peak_multiplier == 0.05
    assert default.engine_platforms() == ["twitter", "reddit"]
    tw = default.channel_for_platform("twitter")
    assert tw is not None and tw.viral_threshold == 10


def test_get_or_default_falls_back():
    reg = _fresh_registry()
    assert reg.get_or_default(None).id == DEFAULT_SCENARIO_ID
    assert reg.get_or_default("does_not_exist").id == DEFAULT_SCENARIO_ID
    assert reg.get_or_default("financial_market").id == "financial_market"


def test_get_unknown_raises():
    reg = _fresh_registry()
    try:
        reg.get("nope")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown scenario")


def test_roundtrip_serialisation():
    reg = _fresh_registry()
    for preset in reg.all():
        rebuilt = ScenarioPreset.from_dict(preset.to_dict())
        assert rebuilt.to_dict() == preset.to_dict()


def test_validation_catches_bad_platform():
    preset = ScenarioPreset.from_dict(
        {
            "id": "bad",
            "channels": [{"id": "c1", "engine_platform": "mastodon"}],
        }
    )
    errors = preset.validate()
    assert any("mastodon" in e for e in errors)


def test_validation_catches_missing_channels_and_bad_stance():
    preset = ScenarioPreset.from_dict(
        {"id": "empty", "channels": [], "stances": ["a"], "default_stance": "b"}
    )
    errors = preset.validate()
    assert any("no channels" in e for e in errors)
    assert any("default_stance" in e for e in errors)


def test_user_dir_overrides_builtin(tmp_path=None):
    import tempfile

    tmp = tmp_path or tempfile.mkdtemp()
    tmp = str(tmp)
    override = {
        "id": "social_media",
        "name": "Overridden",
        "channels": [{"id": "twitter", "engine_platform": "twitter"}],
        "default_total_hours": 5,
    }
    with open(os.path.join(tmp, "override.json"), "w", encoding="utf-8") as fh:
        json.dump(override, fh)
    reg = ScenarioRegistry(user_dir=tmp)
    sm = reg.get("social_media")
    assert sm.name == "Overridden"
    assert sm.default_total_hours == 5
    assert sm.builtin is False


def test_malformed_user_preset_raises():
    import tempfile

    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "broken.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json ")
    try:
        ScenarioRegistry(user_dir=tmp)
    except ScenarioLoadError:
        pass
    else:
        raise AssertionError("expected ScenarioLoadError for malformed preset")


def test_activity_rhythm_from_partial_dict():
    r = ActivityRhythm.from_dict({"peak_multiplier": 2.0})
    assert r.peak_multiplier == 2.0
    # Unspecified fields keep sensible defaults.
    assert r.off_peak_hours == [0, 1, 2, 3, 4, 5]


def _run_all():
    """Minimal runner so the file works without pytest."""
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
