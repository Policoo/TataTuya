"""Tuya device-cache persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from tatatuya.domain.models import Device, EnergyEligibility
from tatatuya.infrastructure.repositories._mapping import from_utc_text, to_utc_text


class DeviceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, device: Device, seen_at_utc: datetime | None = None) -> Device:
        seen_at = seen_at_utc or device.last_seen_at_utc or datetime.now(UTC)
        first_seen = device.first_seen_at_utc or seen_at
        self.connection.execute(
            """
            INSERT INTO devices(
                device_id, name, product_id, product_name, category, online,
                energy_code, energy_unit, energy_scale, raw_device_json,
                first_seen_at_utc, last_seen_at_utc, energy_eligibility,
                present_in_tuya, raw_specification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                name = excluded.name,
                product_id = excluded.product_id,
                product_name = excluded.product_name,
                category = excluded.category,
                online = excluded.online,
                energy_code = CASE
                    WHEN devices.product_id IS NOT excluded.product_id
                         OR excluded.energy_eligibility = 'unsupported'
                        THEN excluded.energy_code
                    ELSE COALESCE(excluded.energy_code, devices.energy_code)
                END,
                energy_unit = CASE
                    WHEN devices.product_id IS NOT excluded.product_id
                         OR excluded.energy_eligibility = 'unsupported'
                        THEN excluded.energy_unit
                    ELSE COALESCE(excluded.energy_unit, devices.energy_unit)
                END,
                energy_scale = CASE
                    WHEN devices.product_id IS NOT excluded.product_id
                         OR excluded.energy_eligibility = 'unsupported'
                        THEN excluded.energy_scale
                    ELSE COALESCE(excluded.energy_scale, devices.energy_scale)
                END,
                energy_eligibility = CASE
                    WHEN devices.product_id IS NOT excluded.product_id
                        THEN excluded.energy_eligibility
                    WHEN excluded.energy_eligibility = 'unknown'
                         AND excluded.raw_specification_json IS NULL
                        THEN devices.energy_eligibility
                    ELSE excluded.energy_eligibility
                END,
                raw_specification_json = CASE
                    WHEN devices.product_id IS NOT excluded.product_id
                        THEN excluded.raw_specification_json
                    ELSE COALESCE(
                        excluded.raw_specification_json,
                        devices.raw_specification_json
                    )
                END,
                present_in_tuya = excluded.present_in_tuya,
                raw_device_json = excluded.raw_device_json,
                last_seen_at_utc = excluded.last_seen_at_utc
            """,
            (
                device.device_id,
                device.name,
                device.product_id,
                device.product_name,
                device.category,
                None if device.online is None else int(device.online),
                device.energy_code,
                device.energy_unit,
                device.energy_scale,
                device.raw_device_json,
                to_utc_text(first_seen),
                to_utc_text(seen_at),
                device.energy_eligibility.value,
                (
                    None
                    if device.present_in_tuya is None
                    else int(device.present_in_tuya)
                ),
                device.raw_specification_json,
            ),
        )
        saved = self.get(device.device_id)
        assert saved is not None
        return saved

    def mark_all_missing(self) -> None:
        self.connection.execute("UPDATE devices SET present_in_tuya = 0")

    def get(self, device_id: str) -> Device | None:
        row = self.connection.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        return None if row is None else _map_device(row)

    def list_all(self) -> list[Device]:
        rows = self.connection.execute(
            "SELECT * FROM devices ORDER BY name COLLATE NOCASE, device_id"
        ).fetchall()
        return [_map_device(row) for row in rows]


def _map_device(row: sqlite3.Row) -> Device:
    online = row["online"]
    return Device(
        device_id=row["device_id"],
        name=row["name"],
        product_id=row["product_id"],
        product_name=row["product_name"],
        category=row["category"],
        online=None if online is None else bool(online),
        energy_code=row["energy_code"],
        energy_unit=row["energy_unit"],
        energy_scale=row["energy_scale"],
        raw_device_json=row["raw_device_json"],
        first_seen_at_utc=from_utc_text(row["first_seen_at_utc"]),
        last_seen_at_utc=from_utc_text(row["last_seen_at_utc"]),
        energy_eligibility=EnergyEligibility(row["energy_eligibility"]),
        present_in_tuya=(
            None
            if row["present_in_tuya"] is None
            else bool(row["present_in_tuya"])
        ),
        raw_specification_json=row["raw_specification_json"],
    )
