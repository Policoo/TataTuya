"""Combo-box widgets that remain readable with the application's light theme."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QComboBox


class PaletteSafeComboBox(QComboBox):
    """Keep the native popup palette aligned with the styled combo-box view."""

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        super().showPopup()
        popup = self.view().window()
        popup.setObjectName("ComboPopup")
        view_palette = self.view().palette()
        palette = popup.palette()
        palette.setColor(
            QPalette.ColorRole.Window,
            view_palette.color(QPalette.ColorRole.Base),
        )
        palette.setColor(
            QPalette.ColorRole.Base,
            view_palette.color(QPalette.ColorRole.Base),
        )
        palette.setColor(
            QPalette.ColorRole.WindowText,
            view_palette.color(QPalette.ColorRole.Text),
        )
        palette.setColor(
            QPalette.ColorRole.Text,
            view_palette.color(QPalette.ColorRole.Text),
        )
        popup.setPalette(palette)
        popup.setAutoFillBackground(True)
