"""CI badge generator does not depend on optional tooling or a live server."""

import importlib.util
from pathlib import Path


def test_badge_thresholds():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_coverage_badge.py"
    spec = importlib.util.spec_from_file_location("badge", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.badge_payload(92.4)["message"] == "92%"
    assert module.badge_payload(92.4)["color"] == "brightgreen"
    assert module.badge_payload(86)["color"] == "green"
    assert module.badge_payload(80)["color"] == "yellow"
    assert module.badge_payload(60)["color"] == "red"
