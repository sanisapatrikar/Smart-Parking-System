"""
billing.py — Entry/exit time tracking and dynamic billing for the Smart Parking System.

Provides:
  - BillingSystem : persists vehicle records in database.json and calculates
                    peak/off-peak parking charges.

Pricing
-------
  Off-peak (all hours outside 17:00–22:00) : Rs. 50 per hour
  Peak     (17:00–22:00 daily)             : Rs. 75 per hour

Partial hours are billed per-minute (fractional hours).
"""

import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DATABASE_FILE = Path(__file__).parent / "database.json"
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Peak-hour window (24-hour clock, inclusive start, exclusive end)
_PEAK_START_HOUR = 17  # 17:00
_PEAK_END_HOUR = 22    # 22:00

_RATE_OFFPEAK = 50.0   # Rs per hour
_RATE_PEAK = 75.0      # Rs per hour


# ---------------------------------------------------------------------------
# BillingSystem
# ---------------------------------------------------------------------------

class BillingSystem:
    """
    Manages vehicle entry/exit records and calculates parking charges.

    All active records are stored in ``database.json`` as a flat dictionary
    keyed by plate number::

        {
            "MH12AB1234": {"entry_time": "2026-04-30T17:30:00", "slot": 2}
        }

    An empty object ``{}`` means the lot is completely vacant.
    """

    def __init__(self, db_path: Path = _DATABASE_FILE) -> None:
        self._db_path = db_path
        # Create the file with an empty object if it does not already exist.
        if not self._db_path.exists():
            self._save({})
        logger.info("BillingSystem initialised (database: %s)", self._db_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        """Load and return the JSON database as a Python dict."""
        try:
            with self._db_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load database: %s — resetting to empty.", exc)
            return {}

    def _save(self, data: dict) -> None:
        """Persist *data* to the JSON database file."""
        try:
            with self._db_path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to save database: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_entry(self, plate_number: str, slot_assigned: int) -> None:
        """
        Record a vehicle's entry into the parking lot.

        Writes *plate_number*, *slot_assigned*, and the current timestamp
        to ``database.json``.  Any existing open record for *plate_number*
        is overwritten (handles duplicate scans gracefully).

        Parameters
        ----------
        plate_number : str
            Upper-case plate string, e.g. ``"MH12AB1234"``.
        slot_assigned : int
            Slot number (1–4) allocated to this vehicle.
        """
        db = self._load()
        entry_time = datetime.now().strftime(_ISO_FORMAT)
        db[plate_number] = {"entry_time": entry_time, "slot": slot_assigned}
        self._save(db)
        logger.info(
            "Entry logged — Plate: %s  Slot: %d  Time: %s",
            plate_number, slot_assigned, entry_time,
        )

    def log_exit(self, slot_freed: int) -> Optional[str]:
        """
        Record a vehicle's exit, calculate its bill, and print it.

        Searches ``database.json`` for the vehicle parked in *slot_freed*,
        computes the charge based on peak/off-peak hour overlap, prints a
        formatted itemised receipt to the terminal, then removes the record.

        Parameters
        ----------
        slot_freed : int
            Slot number (1–4) that has just become vacant.

        Returns
        -------
        str or None
            The plate number of the vehicle that exited, or ``None`` if no
            matching record was found in the database.
        """
        db = self._load()

        # Locate the vehicle record for this slot
        plate: Optional[str] = None
        for plate_num, info in db.items():
            if info.get("slot") == slot_freed:
                plate = plate_num
                break

        if plate is None:
            logger.warning(
                "log_exit: no record found for slot %d — ignoring.", slot_freed
            )
            return None

        record = db[plate]
        try:
            entry_dt = datetime.strptime(record["entry_time"], _ISO_FORMAT)
        except (KeyError, ValueError) as exc:
            logger.error(
                "log_exit: could not parse entry_time for plate %s: %s", plate, exc
            )
            return None

        exit_dt = datetime.now()
        peak_min, offpeak_min, total_bill = self._calculate_bill(entry_dt, exit_dt)

        total_min = peak_min + offpeak_min
        hours, minutes = divmod(int(total_min), 60)

        # Print itemised receipt
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
            offpeak_hrs = offpeak_min / 60.0
            print(
                f"  Off-peak  {offpeak_hrs:6.2f} hr x Rs.{_RATE_OFFPEAK:>5.0f}/hr"
                f"  = Rs. {offpeak_hrs * _RATE_OFFPEAK:>8.2f}"
            )
        if peak_min > 0:
            peak_hrs = peak_min / 60.0
            print(
                f"  Peak      {peak_hrs:6.2f} hr x Rs.{_RATE_PEAK:>5.0f}/hr"
                f"  = Rs. {peak_hrs * _RATE_PEAK:>8.2f}"
            )
        print("-" * 46)
        print(f"  TOTAL BILL   :              Rs. {total_bill:>8.2f}")
        print("=" * 46 + "\n")

        logger.info(
            "Exit logged — Plate: %s  Slot: %d  Duration: %dh %02dm  Bill: Rs. %.2f",
            plate, slot_freed, hours, minutes, total_bill,
        )

        # Remove the vehicle record and persist
        del db[plate]
        self._save(db)

        return plate

    # ------------------------------------------------------------------
    # Billing calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_bill(
        entry_dt: datetime, exit_dt: datetime
    ) -> Tuple[float, float, float]:
        """
        Split the parked interval into peak and off-peak minutes and
        return the itemised and total charges.

        The algorithm iterates over every calendar day that overlaps with
        [entry_dt, exit_dt] and accumulates the minutes that fall inside the
        daily peak window (``_PEAK_START_HOUR``–``_PEAK_END_HOUR``).  This
        correctly handles stays that span midnight or multiple days.

        Parameters
        ----------
        entry_dt : datetime
        exit_dt  : datetime

        Returns
        -------
        (peak_minutes, offpeak_minutes, total_bill) : Tuple[float, float, float]
        """
        if exit_dt <= entry_dt:
            return 0.0, 0.0, 0.0

        total_seconds = (exit_dt - entry_dt).total_seconds()
        total_minutes = total_seconds / 60.0

        # Accumulate minutes that overlap with the peak window on each day.
        peak_minutes = 0.0
        current_day = entry_dt.date()
        end_day = exit_dt.date()

        while current_day <= end_day:
            peak_start = datetime.combine(current_day, time(_PEAK_START_HOUR, 0))
            peak_end = datetime.combine(current_day, time(_PEAK_END_HOUR, 0))
            overlap_start = max(entry_dt, peak_start)
            overlap_end = min(exit_dt, peak_end)
            if overlap_end > overlap_start:
                peak_minutes += (overlap_end - overlap_start).total_seconds() / 60.0
            current_day += timedelta(days=1)

        offpeak_minutes = total_minutes - peak_minutes
        peak_charge = (peak_minutes / 60.0) * _RATE_PEAK
        offpeak_charge = (offpeak_minutes / 60.0) * _RATE_OFFPEAK
        total_bill = peak_charge + offpeak_charge

        return peak_minutes, offpeak_minutes, total_bill
