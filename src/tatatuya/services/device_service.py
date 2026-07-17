"""Device discovery and energy-specification caching workflows."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from tatatuya.domain.errors import (
    EnergySpecificationError,
    UnsupportedEnergyDeviceError,
    UserFacingError,
)
from tatatuya.domain.models import Device, EnergyEligibility, EnergySpecification
from tatatuya.services.ports import DeviceStore, TuyaGateway


class DeviceService:
    def __init__(
        self,
        gateway: TuyaGateway,
        devices: DeviceStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.devices = devices
        self.clock = clock or (lambda: datetime.now(UTC))

    def discover(self) -> list[Device]:
        try:
            remote_devices = self.gateway.list_devices()
        except Exception as exc:
            raise UserFacingError(
                "Conexiune Tuya nereușită",
                "Lista dispozitivelor nu a putut fi încărcată. Verificați conexiunea și setările Tuya.",
                str(exc),
            ) from exc
        seen_at = self.clock()
        self.devices.mark_all_missing()
        return [
            self.devices.upsert(replace(device, present_in_tuya=True), seen_at)
            for device in remote_devices
        ]

    def ensure_energy_specification(
        self,
        device: Device,
        *,
        force_refresh: bool = False,
    ) -> tuple[Device, EnergySpecification]:
        if not force_refresh and _has_complete_specification(device):
            return device, EnergySpecification(
                device.energy_code or "",
                device.energy_unit or "",
                device.energy_scale if device.energy_scale is not None else 0,
                device.raw_specification_json or "{}",
            )
        try:
            specification = self.gateway.get_device_specification(device.device_id)
        except UnsupportedEnergyDeviceError as exc:
            self.devices.upsert(
                replace(
                    device,
                    energy_code=None,
                    energy_unit=None,
                    energy_scale=None,
                    energy_eligibility=EnergyEligibility.UNSUPPORTED,
                    raw_specification_json=exc.raw_json,
                ),
                self.clock(),
            )
            raise
        except EnergySpecificationError as exc:
            self.devices.upsert(
                replace(
                    device,
                    energy_eligibility=EnergyEligibility.UNKNOWN,
                    raw_specification_json=exc.raw_json,
                ),
                self.clock(),
            )
            raise UserFacingError(
                "Specificație incompatibilă",
                f"Specificația de energie pentru „{device.name}” nu este validă.",
                str(exc),
            ) from exc
        except Exception as exc:
            raise UserFacingError(
                "Specificație indisponibilă",
                f"Specificația de energie pentru „{device.name}” nu a putut fi citită.",
                str(exc),
            ) from exc
        updated = self.devices.upsert(
            replace(
                device,
                energy_code=specification.code,
                energy_unit=specification.unit,
                energy_scale=specification.scale,
                energy_eligibility=EnergyEligibility.SUPPORTED,
                raw_specification_json=specification.raw_json,
            ),
            self.clock(),
        )
        return updated, specification


def _has_complete_specification(device: Device) -> bool:
    return (
        bool(device.energy_code)
        and device.energy_eligibility is EnergyEligibility.SUPPORTED
        and bool(device.energy_unit)
        and isinstance(device.energy_scale, int)
        and not isinstance(device.energy_scale, bool)
        and device.energy_scale >= 0
    )
