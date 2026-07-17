"""Translated, read-only Tuya device metadata."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QDialogButtonBox, QWidget

from tatatuya.domain.models import Device
from tatatuya.ui import text
from tatatuya.ui.components.modal import AppModal
from tatatuya.ui.formatters import format_local_datetime, online_label


class DeviceInfoDialog(AppModal):
    def __init__(
        self,
        device: Device,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text.DEVICE_INFO_TITLE, text.DEVICE_INFO_SUBTITLE, parent)
        self.resize(720, 600)
        self.add_field_grid(
            [
                (text.DEVICE_NAME, device.name),
                (text.DEVICE_ID, device.device_id),
                (text.PRODUCT, display(device.product_name)),
                (text.PRODUCT_ID, display(device.product_id)),
                (text.CATEGORY, display(device.category)),
                (text.STATE, online_label(device.online)),
                (text.ENERGY_DATA_POINT, display(device.energy_code)),
                (text.ENERGY_UNIT, display(device.energy_unit)),
                (text.ENERGY_SCALE, display(device.energy_scale)),
                (
                    text.FIRST_SEEN,
                    timestamp(device.first_seen_at_utc),
                ),
                (
                    text.LAST_SEEN,
                    timestamp(device.last_seen_at_utc),
                ),
            ]
        )
        buttons = self.findChild(QDialogButtonBox)
        if buttons is not None:
            close = buttons.button(QDialogButtonBox.StandardButton.Close)
            close.setText(text.CLOSE)


def display(value: object | None) -> str:
    return text.NOT_AVAILABLE if value is None or value == "" else str(value)


def timestamp(value: datetime | None) -> str:
    return text.NOT_AVAILABLE if value is None else format_local_datetime(value)
