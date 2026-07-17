"""Combo-box widgets that remain readable with the application's light theme."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QComboBox, QWidget


class PaletteSafeComboBox(QComboBox):
    """Keep the native popup palette aligned with the styled combo-box view."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        popup_row_limit: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._popup_row_limit = popup_row_limit
        if popup_row_limit is not None:
            self.setMaxVisibleItems(popup_row_limit)

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        popup = self.view().window()
        popup.setMaximumHeight(16_777_215)
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
        self._cap_popup_height(popup)

    def _cap_popup_height(self, popup: QWidget) -> None:
        """Enforce the row limit for styles that ignore maxVisibleItems."""
        limit = self._popup_row_limit
        if limit is None or self.count() <= limit:
            return
        view = self.view()
        visible_rows_height = sum(
            max(1, view.sizeHintForRow(row)) for row in range(limit)
        )
        popup_chrome_height = max(0, popup.height() - view.height())
        maximum_height = (
            visible_rows_height
            + (2 * view.frameWidth())
            + popup_chrome_height
        )
        popup.setMaximumHeight(maximum_height)
        if popup.height() > maximum_height:
            popup.resize(popup.width(), maximum_height)
        layout = popup.layout()
        if layout is not None:
            layout.activate()
        missing_height = visible_rows_height - view.viewport().height()
        if missing_height > 0:
            maximum_height = popup.height() + missing_height
            popup.setMaximumHeight(maximum_height)
            popup.resize(popup.width(), maximum_height)
