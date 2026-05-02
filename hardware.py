# hardware.py — GPIO abstractions: LCD, ServoGate, DebouncedIRSensor
# ⚠ Power all IR sensors from the 3.3 V rail (not 5 V) to protect the Pi's GPIO.

import logging
import threading
import time

import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD

logger = logging.getLogger(__name__)

# BCM pin assignments
SERVO_PIN       = 18   # Hardware PWM0
IR_ENTRANCE_PIN = 17
IR_SLOT1_PIN    = 27
IR_SLOT2_PIN    = 22
IR_SLOT3_PIN    =  5
IR_SLOT4_PIN    =  6

# LCD — run 'sudo i2cdetect -y 1' to confirm address (may be 0x3F)
LCD_I2C_ADDRESS = 0x27
LCD_I2C_PORT    = 1
LCD_COLS        = 16
LCD_ROWS        = 2

# Servo pulse widths µs (SG90 / MG996R)
SERVO_OPEN_PW   = 1500  # ~90° open
SERVO_CLOSED_PW =  500  # ~0°  closed

IR_DEBOUNCE_SECONDS = 3  # reading must be stable this long before state commits


def init_gpio() -> None:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)


def cleanup_gpio() -> None:
    GPIO.cleanup()


class LCDDisplay:
    """16x2 I2C LCD wrapper with graceful fallback to terminal output.

    If the I2C display is absent or disconnects at any point, all messages
    are printed to stdout so the rest of the system keeps running without
    interruption.
    """

    def __init__(self, address: int = LCD_I2C_ADDRESS, port: int = LCD_I2C_PORT) -> None:
        self._available = False
        try:
            self._lcd = CharLCD(
                i2c_expander="PCF8574",
                address=address,
                port=port,
                cols=LCD_COLS,
                rows=LCD_ROWS,
                dotsize=8,
                charmap="A02",
                auto_linebreaks=False,
            )
            self._available = True
            self._lcd.clear()
        except Exception as exc:
            logger.warning(
                "LCD not available (I2C init failed: %s) — output will go to terminal.",
                exc,
            )

    def write(self, line1: str = "", line2: str = "") -> None:
        # Always echo to terminal so output is never silently lost
        print(f"[LCD] {line1} | {line2}")
        if not self._available:
            return
        try:
            self._lcd.clear()
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:LCD_COLS].ljust(LCD_COLS))
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(line2[:LCD_COLS].ljust(LCD_COLS))
        except Exception as exc:
            logger.warning(
                "LCD write failed: %s — switching to terminal-only output.", exc
            )
            self._available = False

    def clear(self) -> None:
        if not self._available:
            return
        try:
            self._lcd.clear()
        except Exception as exc:
            logger.warning("LCD clear failed: %s", exc)
            self._available = False

    def close(self) -> None:
        if not self._available:
            return
        try:
            self._lcd.close(clear=True)
        except Exception as exc:
            logger.warning("LCD close failed: %s", exc)


class ServoGate:
    def __init__(self, pin: int = SERVO_PIN) -> None:
        self._pin = pin
        GPIO.setup(self._pin, GPIO.OUT)
        # Set up Software PWM at 50Hz
        self._pwm = GPIO.PWM(self._pin, 50)
        self._pwm.start(0)

    def open(self) -> None:
        self._pwm.ChangeDutyCycle(7.5)  # ~90 degrees open

    def close(self) -> None:
        self._pwm.ChangeDutyCycle(2.5)  # ~0 degrees closed
        time.sleep(0.5)                 # Give it time to physically move
        self._pwm.ChangeDutyCycle(0)    # Stop sending power (prevents buzzing)

    def open_for(self, seconds: float = 5.0) -> None:
        self.open()
        time.sleep(seconds)
        self.close()

    def cleanup(self) -> None:
        self._pwm.stop()

class DebouncedIRSensor:
    # Active-low: OUT pin pulled LOW when obstacle detected.
    # State must be stable for IR_DEBOUNCE_SECONDS before it is committed.
    POLL_INTERVAL = 0.05  # s between GPIO reads

    def __init__(self, pin: int, active_low: bool = True) -> None:
        self._pin = pin
        self._active_low = active_low
        GPIO.setup(self._pin, GPIO.IN)

        # Seed state from the current reading so is_occupied() is valid immediately
        initial_state = self._raw_to_occupied(GPIO.input(self._pin))
        self._stable_state: bool  = initial_state
        self._pending_state: bool = initial_state
        self._pending_since: float = time.monotonic()

        self._lock    = threading.Lock()
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"IRDebounce-GPIO{pin}"
        )
        self._thread.start()

    def _raw_to_occupied(self, raw_value: int) -> bool:
        if self._active_low:
            return raw_value == GPIO.LOW
        return raw_value == GPIO.HIGH

    def _poll_loop(self) -> None:
        while self._running:
            new_state = self._raw_to_occupied(GPIO.input(self._pin))
            now = time.monotonic()
            with self._lock:
                if new_state != self._pending_state:
                    # Signal changed — restart stability timer
                    self._pending_state = new_state
                    self._pending_since = now
                elif (now - self._pending_since) >= IR_DEBOUNCE_SECONDS:
                    self._stable_state = new_state
            time.sleep(self.POLL_INTERVAL)

    def is_occupied(self) -> bool:
        with self._lock:
            return self._stable_state

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
