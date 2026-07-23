"""Regression tests for the ParkPower frontend bundle."""

from pathlib import Path


PANEL_SOURCE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "pow_reporting"
    / "frontend"
    / "pow-reporting-panel.js"
)


def test_panel_registration_is_idempotent() -> None:
    """Repeated panel module loads must not redefine the custom element."""
    source = PANEL_SOURCE.read_text(encoding="utf-8")

    assert 'customElements.get("pow-reporting-panel")' in source
    assert source.count('customElements.define("pow-reporting-panel"') == 1
