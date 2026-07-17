"""Errors that can safely cross the service/UI boundary."""

from __future__ import annotations


class UserFacingError(Exception):
    """An expected failure with Romanian text suitable for a dialog."""

    def __init__(
        self,
        title: str,
        message: str,
        technical_details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.title = title
        self.message = message
        self.technical_details = technical_details


class EnergySpecificationError(Exception):
    """A specification that cannot safely drive reading normalization."""

    def __init__(self, message: str, raw_json: str = "{}") -> None:
        super().__init__(message)
        self.raw_json = raw_json


class UnsupportedEnergyDeviceError(EnergySpecificationError):
    """A valid specification conclusively identifies a non-billable device."""
