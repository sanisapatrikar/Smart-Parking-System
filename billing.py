# billing.py — Entry/exit logging and peak/off-peak billing
# database.json schema: { "MH12AB1234": {"entry_time": "2026-04-30T17:30:00", "slot": 2} }

import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_DATABASE_FILE   = Path(__file__).parent / "database.json"
_ISO_FORMAT      = "%Y-%m-%dT%H:%M:%S"

_PEAK_START_HOUR = 17
_PEAK_END_HOUR   = 22
_RATE_OFFPEAK    = 50.0  # Rs/hr
_RATE_PEAK       = 75.0  # Rs/hr


class BillingSystem:
    def __init__(self, db_path: Path = _DATABASE_FILE) -> None:
        self._db_path = db_path
        if not self._db_path.exists():
            self._save({})

    def _load(self) -> dict:
        try:
            with self._db_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("DB load failed: %s", exc)
            return {}

    def _save(self, data: dict) -> None:
        try:
            with self._db_path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            logger.error("DB save failed: %s", exc)

    def log_entry(self, plate_number: str, slot_assigned: int) -> None:
        db = self._load()
        entry_time = datetime.now().strftime(_ISO_FORMAT)
        db[plate_number] = {"entry_time": entry_time, "slot": slot_assigned}
        self._save(db)
        logger.info("Entry: %s -> slot %d at %s", plate_number, slot_assigned, entry_time)

    def log_exit(self, slot_freed: int) -> Optional[str]:
        db = self._load()

        # Find which plate is in this slot
        plate = next((p for p, info in db.items() if info.get("slot") == slot_freed), None)
        if plate is None:
            logger.warning("log_exit: no record for slot %d", slot_freed)
            return None

        try:
            entry_dt = datetime.strptime(db[plate]["entry_time"], _ISO_FORMAT)
        except (KeyError, ValueError) as exc:
            logger.error("log_exit: bad entry_time for %s: %s", plate, exc)
            return None

        exit_dt = datetime.now()
        peak_min, offpeak_min, total_bill = self._calculate_bill(entry_dt, exit_dt)
        hours, minutes = divmod(int(peak_min + offpeak_min), 60)

        print("\n" + "=" * 46)
        print("     SMART PARKING SYSTEM — RECEIPT")
        print("=" * 46)
        print(f"  Plate Number : {plate}")
        print(f"  Slot         : {slot_freed}")
        print(f"  Entry Time   : {entry_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Exit Time    : {exit_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Duration     : {hours}h {minutes:02d}m")
        print("-" * 46)
        if offpeak_min > 0:
            h = offpeak_min / 60.0
            print(f"  Off-peak  {h:6.2f} hr x Rs.{_RATE_OFFPEAK:>5.0f}/hr  = Rs. {h * _RATE_OFFPEAK:>8.2f}")
        if peak_min > 0:
            h = peak_min / 60.0
            print(f"  Peak      {h:6.2f} hr x Rs.{_RATE_PEAK:>5.0f}/hr  = Rs. {h * _RATE_PEAK:>8.2f}")
        print("-" * 46)
        print(f"  TOTAL BILL   :              Rs. {total_bill:>8.2f}")
        print("=" * 46 + "\n")

        del db[plate]
        self._save(db)
        return plate

    @staticmethod
    def _calculate_bill(entry_dt: datetime, exit_dt: datetime) -> Tuple[float, float, float]:
        if exit_dt <= entry_dt:
            return 0.0, 0.0, 0.0

        total_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
        peak_minutes  = 0.0
        day = entry_dt.date()

        # Walk each calendar day to accumulate peak-window overlap
        while day <= exit_dt.date():
            peak_start    = datetime.combine(day, time(_PEAK_START_HOUR, 0))
            peak_end      = datetime.combine(day, time(_PEAK_END_HOUR, 0))
            overlap_start = max(entry_dt, peak_start)
            overlap_end   = min(exit_dt, peak_end)
            if overlap_end > overlap_start:
                peak_minutes += (overlap_end - overlap_start).total_seconds() / 60.0
            day += timedelta(days=1)

        offpeak_minutes = total_minutes - peak_minutes
        total_bill = (peak_minutes / 60.0) * _RATE_PEAK + (offpeak_minutes / 60.0) * _RATE_OFFPEAK
        return peak_minutes, offpeak_minutes, total_bill
