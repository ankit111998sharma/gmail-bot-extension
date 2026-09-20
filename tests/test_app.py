from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_simple_home_layout_renders() -> None:
    at = AppTest.from_file(str(APP), default_timeout=20)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Gmail Draft Assistant"
    labels = [button.label for button in at.button]
    assert "Start" in labels
    assert "Pause" in labels
    assert "Stop" in labels


def test_start_pause_stop_do_not_crash_ui() -> None:
    at = AppTest.from_file(str(APP), default_timeout=20)
    at.run()
    by_label = {button.label: button for button in at.button}
    by_label["Start"].click().run()
    assert not at.exception
    by_label = {button.label: button for button in at.button}
    by_label["Pause"].click().run()
    assert not at.exception
    by_label = {button.label: button for button in at.button}
    by_label["Stop"].click().run()
    assert not at.exception
