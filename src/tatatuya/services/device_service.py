"""Device discovery and energy-specification caching workflows."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, EnergySpecification
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
        return [self.devices.upsert(device, seen_at) for device in remote_devices]

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
            )
        try:
            specification = self.gateway.get_device_specification(device.device_id)
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
            ),
            self.clock(),
        )
        return updated, specification


def _has_complete_specification(device: Device) -> bool:
    return (
        device.energy_code == "forward_energy_total"
        and bool(device.energy_unit)
        and isinstance(device.energy_scale, int)
        and not isinstance(device.energy_scale, bool)
        and device.energy_scale >= 0
    )
