from datetime import UTC, datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tatatuya.domain.models import Device
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.device_info import DeviceInfoDialog


def app() -> QApplication:
    existing = QApplication.instance()
    instance = existing if isinstance(existing, QApplication) else QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def test_info_translates_metadata_and_has_no_mutation_controls(tmp_path) -> None:
    qt_app = app()
    device = Device(
        "bf1234567890",
        "Contor principal — locuința familiei",
        product_id="prod-electric-1",
        product_name="Smart Energy Meter",
        category="zndb",
        online=True,
        energy_code="forward_energy_total",
        energy_unit="kWh",
        energy_scale=2,
        raw_device_json='{"category":"zndb"}',
        first_seen_at_utc=datetime(2026, 7, 1, 10, tzinfo=UTC),
        last_seen_at_utc=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    dialog = DeviceInfoDialog(device)
    dialog.show()
    qt_app.processEvents()

    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    for expected in (
        text.DEVICE_NAME,
        text.DEVICE_ID,
        text.PRODUCT,
        text.CATEGORY,
        text.STATE,
        text.ENERGY_DATA_POINT,
        "Contor principal — locuința familiei",
        "prod-electric-1",
        "Smart Energy Meter",
        "zndb",
        text.ONLINE,
        "forward_energy_total",
        "kWh",
    ):
        assert expected in labels

    button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
    assert button_texts == [text.CLOSE]
    assert "Redenumește" not in labels
    assert "Pornește" not in labels
    assert "Oprește" not in labels

    screenshot_path = tmp_path / "device-info.png"
    assert dialog.grab().save(str(screenshot_path))
    assert screenshot_path.stat().st_size > 8_000
    assert dialog.minimumSizeHint().height() <= dialog.height()
    dialog.close()
