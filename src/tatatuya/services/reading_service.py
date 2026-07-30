"""Batch refresh and individual status reading capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol, Sequence

from tatatuya.domain.energy import normalize_energy
from tatatuya.domain.errors import UnsupportedEnergyDeviceError, UserFacingError
from tatatuya.domain.models import Device, DeviceStatus, EnergySpecification, Reading
from tatatuya.services.device_service import DeviceService
from tatatuya.services.ports import TuyaGateway
from tatatuya.domain.cancellation import CancellationContext, uncancelled_context


MAX_BATCH_SIZE = 20


class CaptureReadingStore(Protocol):
    def prepare_capture_phase(self) -> None: ...
    def add_all(
        self,
        readings: tuple[Reading, ...],
        *,
        busy_timeout_seconds: float,
    ) -> tuple[Reading, ...]: ...
    def latest_for_device(self, device_id: str) -> Reading | None: ...


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
        readings: CaptureReadingStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.device_service = device_service
        self.readings = readings
        self.clock = clock or (lambda: datetime.now(UTC))

    def refresh(
        self, cancellation: CancellationContext | None = None
    ) -> list[DeviceRefreshResult]:
        cancellation = cancellation or uncancelled_context()
        cancellation.checkpoint()
        devices = self.device_service.discover()
        prepared: dict[str, tuple[Device, EnergySpecification]] = {}
        failures: dict[str, UserFacingError] = {}
        unsupported: set[str] = set()
        for device in devices:
            cancellation.checkpoint()
            try:
                # A user-triggered refresh revalidates scale and unit before capture.
                prepared[device.device_id] = (
                    self.device_service.ensure_energy_specification(
                        device, force_refresh=True
                    )
                )
            except UnsupportedEnergyDeviceError:
                unsupported.add(device.device_id)
            except UserFacingError as exc:
                failures[device.device_id] = exc

        cancellation.checkpoint()
        statuses: dict[str, DeviceStatus] = {}
        captured: dict[str, Reading] = {}
        eligible_ids = list(prepared)
        if eligible_ids:
            self.readings.prepare_capture_phase()
            cancellation.checkpoint()
        for chunk in _chunks(eligible_ids, MAX_BATCH_SIZE):
            cancellation.checkpoint()
            try:
                with cancellation.reserve_after_remote(5):
                    response_statuses = self.gateway.get_devices_status(list(chunk))
            except UserFacingError:
                raise
            except Exception as exc:
                for device_id in chunk:
                    failures[device_id] = UserFacingError(
                        "Citire indisponibilă",
                        "Starea curentă a contorului nu a putut fi citită.",
                        str(exc),
                    )
                continue

            capture_stage_started_with = cancellation.remaining_seconds()
            statuses.update(response_statuses)
            pending: list[tuple[str, Reading]] = []
            for device_id in chunk:
                prepared_item = prepared[device_id]
                status = response_statuses.get(device_id)
                if status is None:
                    failures[device_id] = UserFacingError(
                        "Citire indisponibilă",
                        f"Tuya nu a returnat starea contorului „{prepared_item[0].name}”.",
                        f"device_id={device_id}",
                    )
                    continue
                try:
                    pending.append(
                        (
                            device_id,
                            self._build_reading(
                                prepared_item[0],
                                status,
                                prepared_item[1],
                                source="batch",
                            ),
                        )
                    )
                except UserFacingError as exc:
                    failures[device_id] = exc
            if pending:
                # Once a status response exists, this one transaction is the
                # documented cancellation-safe capture boundary.
                saved = self.readings.add_all(
                    tuple(reading for _, reading in pending),
                    busy_timeout_seconds=_capture_timeout(
                        cancellation, capture_stage_started_with
                    ),
                )
                for (device_id, _), reading in zip(pending, saved, strict=True):
                    captured[device_id] = reading

        results: list[DeviceRefreshResult] = []
        remote_ids = {device.device_id for device in devices}
        for original_device in devices:
            device_id = original_device.device_id
            prepared_item = prepared.get(device_id)
            current_device = (
                prepared_item[0]
                if prepared_item is not None
                else self.device_service.devices.get(device_id) or original_device
            )
            error = failures.get(device_id)
            reading: Reading | None = None
            if device_id not in unsupported and error is None:
                status = statuses.get(device_id)
                if status is None:
                    error = UserFacingError(
                        "Citire indisponibilă",
                        f"Tuya nu a returnat starea contorului „{current_device.name}”.",
                        f"device_id={device_id}",
                    )
                else:
                    reading = captured.get(device_id)
                    if reading is None:
                        error = UserFacingError(
                            "Citire indisponibilă",
                            f"Citirea contorului „{current_device.name}” nu a putut fi salvată.",
                        )
            results.append(
                DeviceRefreshResult(
                    current_device,
                    reading,
                    reading or self.readings.latest_for_device(device_id),
                    error,
                )
            )
        for cached_device in self.device_service.devices.list_all():
            if (
                cached_device.device_id in remote_ids
                or cached_device.present_in_tuya is not False
            ):
                continue
            results.append(
                DeviceRefreshResult(
                    cached_device,
                    None,
                    self.readings.latest_for_device(cached_device.device_id),
                )
            )
        return results

    def capture_individual_status(
        self,
        device_id: str,
        cancellation: CancellationContext | None = None,
    ) -> StatusCaptureResult:
        cancellation = cancellation or uncancelled_context(15)
        cancellation.checkpoint()
        device = self.device_service.devices.get(device_id)
        if device is None:
            raise UserFacingError(
                "Contor necunoscut",
                "Contorul selectat nu mai există în baza de date locală.",
                f"device_id={device_id}",
            )
        try:
            # Revalidation must finish before the cancellation-safe status boundary.
            device, specification = self.device_service.ensure_energy_specification(
                device, force_refresh=True
            )
        except UnsupportedEnergyDeviceError:
            raise UserFacingError(
                "Contor incompatibil",
                f"Contorul „{device.name}” nu oferă o specificație de energie acceptată.",
            )
        cancellation.checkpoint()
        self.readings.prepare_capture_phase()
        cancellation.checkpoint()
        try:
            with cancellation.reserve_after_remote(5):
                status = self.gateway.get_device_status(device_id)
        except UserFacingError:
            raise
        except Exception as exc:
            raise UserFacingError(
                "Status indisponibil",
                f"Statusul contorului „{device.name}” nu a putut fi încărcat.",
                str(exc),
            ) from exc
        capture_stage_started_with = cancellation.remaining_seconds()
        try:
            reading = self._build_reading(
                device, status, specification, source="status"
            )
            saved = self.readings.add_all(
                (reading,),
                busy_timeout_seconds=_capture_timeout(
                    cancellation, capture_stage_started_with
                ),
            )[0]
            return StatusCaptureResult(status, saved)
        except UserFacingError as exc:
            return StatusCaptureResult(status, None, exc)

    def _build_reading(
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
        return Reading(
            device_id=device.device_id,
            recorded_at_utc=self.clock(),
            raw_value=str(raw_value),
            scale=specification.scale,
            source_unit=specification.unit,
            value_kwh=value_kwh,
            source=source,
            raw_status_json=status.raw_json,
            raw_specification_json=specification.raw_json,
        )


def _chunks(values: Sequence[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _capture_timeout(
    cancellation: CancellationContext,
    remaining_at_response: float,
) -> float:
    """Return post-response SQLite time without observing a latched cancel."""

    current_remaining = cancellation.remaining_seconds()
    elapsed_since_response = max(0.0, remaining_at_response - current_remaining)
    remaining = min(5.0, remaining_at_response) - elapsed_since_response
    if remaining <= 0:
        raise UserFacingError(
            "Salvare expirată",
            "Citirea a fost primită, dar nu a mai rămas timp pentru salvarea sigură.",
        )
    return remaining
