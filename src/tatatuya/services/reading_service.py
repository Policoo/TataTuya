"""Batch refresh and individual status reading capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Sequence

from tatatuya.domain.energy import normalize_energy
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, DeviceStatus, EnergySpecification, Reading
from tatatuya.services.device_service import DeviceService
from tatatuya.services.ports import ReadingStore, TuyaGateway


MAX_BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class DeviceRefreshResult:
    device: Device
    reading: Reading | None
    latest_reading: Reading | None
    error: UserFacingError | None = None

    @property
    def succeeded(self) -> bool:
        return self.reading is not None


@dataclass(frozen=True, slots=True)
class StatusCaptureResult:
    status: DeviceStatus
    reading: Reading | None
    capture_error: UserFacingError | None = None


class ReadingService:
    def __init__(
        self,
        gateway: TuyaGateway,
        device_service: DeviceService,
        readings: ReadingStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.device_service = device_service
        self.readings = readings
        self.clock = clock or (lambda: datetime.now(UTC))

    def refresh(self) -> list[DeviceRefreshResult]:
        devices = self.device_service.discover()
        prepared: dict[str, tuple[Device, EnergySpecification]] = {}
        failures: dict[str, UserFacingError] = {}
        for device in devices:
            try:
                # A user-triggered refresh revalidates scale and unit before capture.
                prepared[device.device_id] = (
                    self.device_service.ensure_energy_specification(
                        device, force_refresh=True
                    )
                )
            except UserFacingError as exc:
                failures[device.device_id] = exc

        statuses: dict[str, DeviceStatus] = {}
        eligible_ids = list(prepared)
        for chunk in _chunks(eligible_ids, MAX_BATCH_SIZE):
            try:
                statuses.update(self.gateway.get_devices_status(list(chunk)))
            except Exception as exc:
                for device_id in chunk:
                    failures[device_id] = UserFacingError(
                        "Citire indisponibilă",
                        "Starea curentă a contorului nu a putut fi citită.",
                        str(exc),
                    )

        results: list[DeviceRefreshResult] = []
        for original_device in devices:
            device_id = original_device.device_id
            current_device = prepared.get(device_id, (original_device, None))[0]
            error = failures.get(device_id)
            reading: Reading | None = None
            if error is None:
                status = statuses.get(device_id)
                if status is None:
                    error = UserFacingError(
                        "Citire indisponibilă",
                        f"Tuya nu a returnat starea contorului „{current_device.name}”.",
                        f"device_id={device_id}",
                    )
                else:
                    try:
                        specification = prepared[device_id][1]
                        reading = self._store_reading(
                            current_device, status, specification, source="batch"
                        )
                    except UserFacingError as exc:
                        error = exc
            results.append(
                DeviceRefreshResult(
                    current_device,
                    reading,
                    reading or self.readings.latest_for_device(device_id),
                    error,
                )
            )
        return results

    def capture_individual_status(self, device_id: str) -> StatusCaptureResult:
        device = self.device_service.devices.get(device_id)
        if device is None:
            raise UserFacingError(
                "Contor necunoscut",
                "Contorul selectat nu mai există în baza de date locală.",
                f"device_id={device_id}",
            )
        try:
            status = self.gateway.get_device_status(device_id)
        except Exception as exc:
            raise UserFacingError(
                "Status indisponibil",
                f"Statusul contorului „{device.name}” nu a putut fi încărcat.",
                str(exc),
            ) from exc
        try:
            # Individual Status is also a reading workflow, so revalidate first.
            device, specification = self.device_service.ensure_energy_specification(
                device, force_refresh=True
            )
            reading = self._store_reading(device, status, specification, source="status")
            return StatusCaptureResult(status, reading)
        except UserFacingError as exc:
            # Raw status remains useful for diagnostics even when it has no billable energy.
            return StatusCaptureResult(status, None, exc)

    def _store_reading(
        self,
        device: Device,
        status: DeviceStatus,
        specification: EnergySpecification,
        *,
        source: str,
    ) -> Reading:
        matches = [
            item.value for item in status.statuses if item.code == specification.code
        ]
        if len(matches) != 1:
            raise UserFacingError(
                "Citire de energie indisponibilă",
                f"Contorul „{device.name}” nu a returnat o valoare unică de energie cumulată.",
                f"code={specification.code!r}; matches={len(matches)}",
            )
        raw_value = matches[0]
        value_kwh = normalize_energy(raw_value, specification.scale, specification.unit)
        return self.readings.add(
            Reading(
                device_id=device.device_id,
                recorded_at_utc=self.clock(),
                raw_value=str(raw_value),
                scale=specification.scale,
                source_unit=specification.unit,
                value_kwh=value_kwh,
                source=source,
                raw_status_json=status.raw_json,
            )
        )


def _chunks(values: Sequence[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]
