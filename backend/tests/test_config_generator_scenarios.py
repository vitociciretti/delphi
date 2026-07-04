"""
Integration test: SimulationConfigGenerator honours the selected scenario.

The generator module imports heavy optional dependencies (openai, the Zep
reader) at import time, and uses package-relative imports. To test the
scenario wiring without installing the full web/LLM stack, we stub those
modules and import the generator as part of a minimal ``app`` package, then
exercise the two pure mapping methods (time config + platform config) with a
generator instance built via ``__new__`` (skipping the OpenAI client).

Run: cd backend && python tests/test_config_generator_scenarios.py
"""

import os
import sys
import types

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]


def _install_stubs():
    """Stub the heavy imports the generator (and its imports) pull in.

    We also register ``app`` as a lightweight namespace package pointing at the
    real source dir, so importing ``app.services.simulation_config_generator``
    does NOT execute the real ``app/__init__`` (which boots the whole Flask app).
    """
    _stub("openai", OpenAI=lambda *a, **k: None)
    _stub("dotenv", load_dotenv=lambda *a, **k: None)
    # flask: app.utils.locale imports request / has_request_context
    _stub("flask", request=None, has_request_context=lambda: False)

    # zep_cloud is imported for many exception/data classes; return a dummy for
    # any attribute so we don't have to enumerate them.
    zep = types.ModuleType("zep_cloud")

    def _any_attr(name):  # noqa: ANN001
        return type(name, (Exception,), {})

    zep.__getattr__ = _any_attr  # type: ignore[attr-defined]
    sys.modules["zep_cloud"] = zep
    _stub("zep_cloud.client", Zep=object)
    _stub("zep_cloud.core", ApiError=Exception)

    # Namespace-only ``app`` / ``app.services`` packages so importing the target
    # module does NOT run the real __init__ files (which eagerly import the
    # whole service/web stack).
    for pkg_name, rel in (("app", ""), ("app.services", "services")):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.join(BACKEND, "app", rel) if rel else os.path.join(BACKEND, "app")]
        sys.modules[pkg_name] = pkg


def main():
    _install_stubs()

    # Import the scenario layer (standalone) and the generator (via app pkg).
    sys.path.insert(0, os.path.join(BACKEND, "app"))
    from scenarios import get_registry  # noqa: E402

    from app.services.simulation_config_generator import (  # noqa: E402
        SimulationConfigGenerator,
        TimeSimulationConfig,
        PlatformConfig,
    )

    registry = get_registry()
    gen = object.__new__(SimulationConfigGenerator)  # skip __init__ / OpenAI

    failures = 0

    def check(name, cond):
        nonlocal failures
        if cond:
            print(f"PASS {name}")
        else:
            failures += 1
            print(f"FAIL {name}")

    # --- social_media default reproduces original hard-coded behaviour -----
    gen.scenario = registry.get("social_media")
    tc = gen._parse_time_config({}, num_entities=100)
    check("social_media total_hours == 72", tc.total_simulation_hours == 72)
    check("social_media peak_hours == [19..22]", tc.peak_hours == [19, 20, 21, 22])
    check("social_media off_peak_mult == 0.05", tc.off_peak_activity_multiplier == 0.05)
    tw = gen._build_platform_config("twitter")
    check("social_media twitter viral==10", tw.viral_threshold == 10)
    rd = gen._build_platform_config("reddit")
    check("social_media reddit echo==0.6", rd.echo_chamber_strength == 0.6)

    # --- financial_market pulls domain-specific values ---------------------
    gen.scenario = registry.get("financial_market")
    tc = gen._parse_time_config({}, num_entities=100)
    check("financial total_hours == 48", tc.total_simulation_hours == 48)
    check("financial minutes_per_round == 30", tc.minutes_per_round == 30)
    check("financial peak_hours from preset", 9 in tc.peak_hours and 22 not in tc.peak_hours)
    tw = gen._build_platform_config("twitter")  # mapped from news_feed channel
    check("financial news_feed recency==0.6", tw.recency_weight == 0.6)

    # --- LLM result still overrides preset defaults ------------------------
    gen.scenario = registry.get("social_media")
    tc = gen._parse_time_config({"total_simulation_hours": 12}, num_entities=100)
    check("LLM override total_hours == 12", tc.total_simulation_hours == 12)

    print(f"\n{'-'*40}\n{'FAILED' if failures else 'OK'}: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
