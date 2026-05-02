"""
main.py — State machine and main control loop for the Smart Parking System.

Hardware used
-------------
  - Raspberry Pi 4B
  - 16x2 I2C LCD (PCF8574 backpack) on GPIO 2 (SDA) / GPIO 3 (SCL)
  - Servo gate on GPIO 18 (RPi.GPIO software PWM)
  - 5x IR proximity sensors (3.3 V rail):
        Entrance : GPIO 17
        Slot 1   : GPIO 27
        Slot 2   : GPIO 22
        Slot 3   : GPIO  5
        Slot 4   : GPIO  6

State machine
-------------
  Idle      → Poll entrance IR sensor and slot IR sensors for exits.
  Triggered → Car detected at entrance; check capacity, scan plate.
  Allocate  → Assign lowest free slot, open gate, return to Idle.

Run with:
    source .venv/bin/activate
    python main.py
"""

import logging
import signal
import sys
import time
import types
from typing import Dict, Optional, Set

import vision
from billing import BillingSystem
from hardware import (
    DebouncedIRSensor,
    IR_ENTRANCE_PIN,
    IR_SLOT1_PIN,
    IR_SLOT2_PIN,
    IR_SLOT3_PIN,
    IR_SLOT4_PIN,
    LCDDisplay,
    ServoGate,
    cleanup_gpio,
    init_gpio,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOTAL_SLOTS = 4
GATE_OPEN_SECONDS = 5.0
FULL_LOT_DISPLAY_SECONDS = 3.0
SCAN_FAIL_DISPLAY_SECONDS = 3.0
MAIN_LOOP_SLEEP = 0.1  # seconds — prevents CPU saturation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_free_slots(
    slot_sensors: Dict[int, DebouncedIRSensor],
    reserved: Set[int],
) -> int:
    """Return the number of slots that are neither occupied nor reserved."""
    return sum(
        1 for slot, sensor in slot_sensors.items()
        if not sensor.is_occupied() and slot not in reserved
    )


def _find_free_slot(
    slot_sensors: Dict[int, DebouncedIRSensor],
    reserved: Set[int],
) -> Optional[int]:
    """
    Return the lowest-numbered slot (1–4) that is neither occupied by a
    vehicle nor soft-reserved for an incoming vehicle.  Returns None if
    all slots are taken.
    """
    for slot_num in sorted(slot_sensors.keys()):
        if not slot_sensors[slot_num].is_occupied() and slot_num not in reserved:
            return slot_num
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # -----------------------------------------------------------------------
    # Hardware initialisation
    # -----------------------------------------------------------------------
    init_gpio()

    lcd = LCDDisplay()
    gate = ServoGate()
    billing = BillingSystem()

    entrance_sensor = DebouncedIRSensor(pin=IR_ENTRANCE_PIN)
    slot_sensors: Dict[int, DebouncedIRSensor] = {
        1: DebouncedIRSensor(pin=IR_SLOT1_PIN),
        2: DebouncedIRSensor(pin=IR_SLOT2_PIN),
        3: DebouncedIRSensor(pin=IR_SLOT3_PIN),
        4: DebouncedIRSensor(pin=IR_SLOT4_PIN),
    }

    # Snapshot of each slot's occupancy from the *previous* loop iteration.
    # Used to detect Occupied → Free transitions for exit billing.
    prev_slot_states: Dict[int, bool] = {
        slot: sensor.is_occupied()
        for slot, sensor in slot_sensors.items()
    }

    # Slots that have been allocated but whose IR sensor has not yet
    # confirmed occupancy.  Excluded from free-slot searches to prevent
    # double-booking during the vehicle's short transit time.
    reserved_slots: Set[int] = set()

    # -----------------------------------------------------------------------
    # Graceful shutdown handler (Ctrl-C / SIGTERM)
    # -----------------------------------------------------------------------
    def _shutdown(signum: int, frame: Optional[types.FrameType]) -> None:
        logger.info("Shutdown signal received — cleaning up.")
        lcd.write("Shutting down...", "")
        time.sleep(1)
        for s in slot_sensors.values():
            s.stop()
        entrance_sensor.stop()
        gate.cleanup()
        lcd.close()
        cleanup_gpio()
        logger.info("Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # -----------------------------------------------------------------------
    # Boot message
    # -----------------------------------------------------------------------
    lcd.write("System Booting", "Please wait...")
    logger.info("System booting — all hardware initialised.")
    time.sleep(2)

    logger.info("Entering main control loop.")

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    while True:
        # -------------------------------------------------------------------
        # Background exit check
        # Compare each slot's current debounced state to the previous
        # iteration.  An Occupied → Free transition means a vehicle has left.
        # -------------------------------------------------------------------
        for slot_num, sensor in slot_sensors.items():
            current_occupied = sensor.is_occupied()

            if prev_slot_states[slot_num] and not current_occupied:
                # Slot just became free — process exit and print bill
                logger.info("Slot %d vacated — processing exit.", slot_num)
                billing.log_exit(slot_num)
                reserved_slots.discard(slot_num)

            # Lift soft-reservation once the sensor confirms occupancy
            if current_occupied:
                reserved_slots.discard(slot_num)

            prev_slot_states[slot_num] = current_occupied

        # -------------------------------------------------------------------
        # State 0 — Idle
        # Display free-slot count and wait for a car at the entrance.
        # -------------------------------------------------------------------
        free_count = _count_free_slots(slot_sensors, reserved_slots)
        lcd.write("Welcome!", f"Slots Left: {free_count}")

        if not entrance_sensor.is_occupied():
            # No vehicle at entrance — stay in Idle
            time.sleep(MAIN_LOOP_SLEEP)
            continue

        # -------------------------------------------------------------------
        # Entry triggered — vehicle detected at entrance
        # -------------------------------------------------------------------
        logger.info("Entrance IR triggered — vehicle detected.")
        lcd.write("Please wait...", "")

        # -------------------------------------------------------------------
        # Capacity check
        # -------------------------------------------------------------------
        if _count_free_slots(slot_sensors, reserved_slots) == 0:
            logger.info("Parking lot is full — rejecting vehicle.")
            lcd.write("Sorry,", "Parking Full")
            time.sleep(FULL_LOT_DISPLAY_SECONDS)
            time.sleep(MAIN_LOOP_SLEEP)
            continue

        # -------------------------------------------------------------------
        # Vision — licence plate recognition
        # -------------------------------------------------------------------
        lcd.write("Scanning plate", "Please hold...")
        plate = vision.scan_plate()

        if plate is None:
            logger.warning("Plate scan failed — asking driver to retry.")
            lcd.write("Scan Failed.", "Try Again")
            time.sleep(SCAN_FAIL_DISPLAY_SECONDS)
            time.sleep(MAIN_LOOP_SLEEP)
            continue

        logger.info("Plate scanned: %s", plate)

        # -------------------------------------------------------------------
        # Slot allocation
        # -------------------------------------------------------------------
        slot = _find_free_slot(slot_sensors, reserved_slots)
        if slot is None:
            # Race condition: lot filled between the capacity check and now
            logger.warning("No free slot available after scan — rejecting.")
            lcd.write("Sorry,", "Parking Full")
            time.sleep(FULL_LOT_DISPLAY_SECONDS)
            time.sleep(MAIN_LOOP_SLEEP)
            continue

        # Soft-reserve the slot immediately to prevent double-booking
        reserved_slots.add(slot)
        # Seed the previous-state tracker so the exit check is not
        # incorrectly triggered before the vehicle pulls in
        prev_slot_states[slot] = True

        billing.log_entry(plate, slot)

        # Truncate plate to fit the 16-char LCD line (up to 12 chars shown)
        lcd.write(f"Hi {plate[:12]}", f"Go to Slot {slot}")
        logger.info("Allocated Slot %d to %s — opening gate.", slot, plate)

        # -------------------------------------------------------------------
        # Gate control — open for GATE_OPEN_SECONDS then close automatically
        # -------------------------------------------------------------------
        gate.open_for(GATE_OPEN_SECONDS)

        # Return to Idle
        time.sleep(MAIN_LOOP_SLEEP)


if __name__ == "__main__":
    main()
