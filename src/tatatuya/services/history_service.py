"""Read-only reading and calculation history assembly."""

from __future__ import annotations

from dataclasses import dataclass

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Reading
from tatatuya.services.ports import CalculationStore, ReadingStore


@dataclass(frozen=True, slots=True)
class CalculationHistoryItem:
    calculation: Calculation
    start_reading: Reading
    end_reading: Reading


@dataclass(frozen=True, slots=True)
class HistoryContext:
    device_id: str
    readings: tuple[Reading, ...]
    calculations: tuple[CalculationHistoryItem, ...]


class HistoryService:
    def __init__(
        self,
        readings: ReadingStore,
        calculations: CalculationStore,
    ) -> None:
        self.readings = readings
        self.calculations = calculations

    def prepare(self, device_id: str) -> HistoryContext:
        readings = tuple(
            sorted(
                self.readings.list_for_device(device_id),
                key=lambda reading: (
                    reading.recorded_at_utc,
                    reading.id if reading.id is not None else -1,
                ),
                reverse=True,
            )
        )
        readings_by_id = {
            reading.id: reading for reading in readings if reading.id is not None
        }
        calculations = sorted(
            self.calculations.list_for_device(device_id),
            key=lambda calculation: (
                calculation.created_at_utc,
                calculation.id if calculation.id is not None else -1,
            ),
            reverse=True,
        )
        items: list[CalculationHistoryItem] = []
        for calculation in calculations:
            start = readings_by_id.get(calculation.start_reading_id)
            end = readings_by_id.get(calculation.end_reading_id)
            if start is None or end is None:
                raise UserFacingError(
                    "Istoric indisponibil",
                    "Detaliile unui calcul salvat nu au putut fi reconstruite.",
                    "Calculul face referire la o citire care nu mai este disponibilă.",
                )
            items.append(CalculationHistoryItem(calculation, start, end))
        return HistoryContext(device_id, readings, tuple(items))

