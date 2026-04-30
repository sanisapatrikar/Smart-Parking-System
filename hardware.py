"""
hardware.py — GPIO hardware abstractions for the Smart Parking System.

Provides:
  - LCDDisplay      : 16×2 I2C LCD (PCF8574 backpack) via RPLCD
  - ServoGate       : Gate servo via pigpio hardware PWM on GPIO 18
  - UltrasonicSensor: HC-SR04 distance measurement with timeout handling
  - DebouncedIRSensor: IR proximity sensor with 3-second software debounce

All BCM GPIO pin numbers match the wiring described in README.md.

⚠️  HC-SR04 ECHO pins output 5 V.  Use a 1 kΩ / 2 kΩ voltage divider on
    EVERY Echo pin before connecting to the Pi GPIO or you will cause
    permanent hardware damage.  See README.md for details.
"""

import logging
import threading
import time
from typing import Optional

import pigpio
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPIO pin definitions (BCM numbering)
# ---------------------------------------------------------------------------
SERVO_PIN = 18          # Hardware PWM0
ENTRANCE_TRIG = 23      # Entrance HC-SR04 TRIG
ENTRANCE_ECHO = 24      # Entrance HC-SR04 ECHO  (via voltage divider!)
SLOT3_TRIG = 20         # Slot-3 HC-SR04 TRIG
SLOT3_ECHO = 21         # Slot-3 HC-SR04 ECHO    (via voltage divider!)
IR_SLOT1_PIN = 17       # IR sensor — Slot 1
IR_SLOT2_PIN = 27       # IR sensor — Slot 2

# LCD I2C settings
LCD_I2C_ADDRESS = 0x27  # Run 'sudo i2cdetect -y 1' to confirm; may be 0x3F
LCD_I2C_PORT = 1        # /dev/i2c-1
LCD_COLS = 16
LCD_ROWS = 2

# Servo pulse widths in microseconds (SG90 / MG996R compatible)
SERVO_OPEN_PW = 1500    # ~90° — gate open
SERVO_CLOSED_PW = 500   # ~0°  — gate closed

# Ultrasonic sensor constants
_TRIGGER_DURATION_S = 0.00001   # 10 µs trigger pulse
_ECHO_TIMEOUT_S = 0.04          # 40 ms max wait (≈ 6.8 m range)
_SPEED_OF_SOUND_CM_S = 34300    # cm/s at ~20 °C

# IR debounce window
IR_DEBOUNCE_SECONDS = 3


# ---------------------------------------------------------------------------
# GPIO initialisation / cleanup
# ---------------------------------------------------------------------------

def init_gpio() -> None:
    """Configure RPi.GPIO global settings.  Call exactly once at startup."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    logger.info("RPi.GPIO initialised in BCM mode")


def cleanup_gpio() -> None:
    """Release all RPi.GPIO resources.  Call on clean exit."""
    GPIO.cleanup()
    logger.info("RPi.GPIO cleaned up")


# ---------------------------------------------------------------------------
# LCD Display
# ---------------------------------------------------------------------------

class LCDDisplay:
    """
    Wrapper around a 16×2 I2C LCD with PCF8574 backpack.

    Usage::

        lcd = LCDDisplay()
        lcd.write("Welcome!", "Slots Left: 3")
        lcd.clear()
        lcd.close()
    """

    def __init__(
        self,
        address: int = LCD_I2C_ADDRESS,
        port: int = LCD_I2C_PORT,
    ) -> None:
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
        self.clear()
        logger.info("LCDDisplay initialised at I2C address 0x%02X", address)

    def write(self, line1: str = "", line2: str = "") -> None:
        """
        Write up to two lines to the display.

        Each line is padded or truncated to exactly 16 characters so that
        residual text from the previous message is always overwritten.
        """
        self._lcd.clear()
        self._lcd.cursor_pos = (0, 0)
        self._lcd.write_string(line1[:LCD_COLS].ljust(LCD_COLS))
        self._lcd.cursor_pos = (1, 0)
        self._lcd.write_string(line2[:LCD_COLS].ljust(LCD_COLS))

    def clear(self) -> None:
        """Clear the display."""
        self._lcd.clear()

    def close(self) -> None:
        """Clear the display and release the I2C handle."""
        self._lcd.close(clear=True)


# ---------------------------------------------------------------------------
# Servo Gate
# ---------------------------------------------------------------------------

class ServoGate:
    """
    Controls the entrance gate servo motor via pigpio hardware PWM.

    pigpio drives GPIO 18 (PWM0) with hardware-generated PWM, avoiding
    the timing jitter that software PWM libraries produce.  The pigpiod
    daemon must be running before this class is instantiated::

        sudo systemctl start pigpiod

    Usage::

        gate = ServoGate()
        gate.open_for(5.0)   # open gate, hold 5 s, then close
        gate.cleanup()
    """

    def __init__(self, pin: int = SERVO_PIN) -> None:
        self._pin = pin
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError(
                "Cannot connect to pigpio daemon. "
                "Ensure it is running: sudo systemctl start pigpiod"
            )
        self._pi.set_mode(self._pin, pigpio.OUTPUT)
        self.close()
        logger.info("ServoGate initialised on GPIO %d", self._pin)

    def open(self) -> None:
        """Rotate the servo to the open position (~90°)."""
        self._pi.set_servo_pulsewidth(self._pin, SERVO_OPEN_PW)
        logger.debug("ServoGate OPEN (pulse width %d µs)", SERVO_OPEN_PW)

    def close(self) -> None:
        """Rotate the servo to the closed position (~0°)."""
        self._pi.set_servo_pulsewidth(self._pin, SERVO_CLOSED_PW)
        logger.debug("ServoGate CLOSED (pulse width %d µs)", SERVO_CLOSED_PW)

    def open_for(self, seconds: float = 5.0) -> None:
        """Open the gate, hold for *seconds*, then close it."""
        self.open()
        time.sleep(seconds)
        self.close()

    def cleanup(self) -> None:
        """Stop PWM output and disconnect from the pigpio daemon."""
        self._pi.set_servo_pulsewidth(self._pin, 0)
        self._pi.stop()
        logger.info("ServoGate cleaned up")


# ---------------------------------------------------------------------------
# Ultrasonic Sensor (HC-SR04)
# ---------------------------------------------------------------------------

class UltrasonicSensor:
    """
    Distance measurement using an HC-SR04 ultrasonic sensor.

    ⚠️  The HC-SR04 ECHO pin outputs 5 V logic.  You MUST wire it through a
        1 kΩ / 2 kΩ resistor voltage divider before the Pi GPIO pin.
        See README.md for the exact circuit diagram.

    Usage::

        # init_gpio() must be called before instantiating this class
        sensor = UltrasonicSensor(trig_pin=ENTRANCE_TRIG, echo_pin=ENTRANCE_ECHO)
        dist = sensor.get_distance_cm()   # float or None
    """

    def __init__(self, trig_pin: int, echo_pin: int) -> None:
        self._trig = trig_pin
        self._echo = echo_pin
        GPIO.setup(self._trig, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self._echo, GPIO.IN)
        time.sleep(0.05)  # Allow sensor to stabilise after power-on
        logger.info(
            "UltrasonicSensor initialised (TRIG=GPIO%d, ECHO=GPIO%d)",
            self._trig,
            self._echo,
        )

    def get_distance_cm(self) -> Optional[float]:
        """
        Trigger a single measurement and return the distance in centimetres.

        Returns None if the echo response times out, which indicates either
        no object in range or a wiring problem.
        """
        # Send a 10 µs HIGH pulse on the TRIG pin
        GPIO.output(self._trig, GPIO.HIGH)
        time.sleep(_TRIGGER_DURATION_S)
        GPIO.output(self._trig, GPIO.LOW)

        # Wait for ECHO to go HIGH (start of reflected pulse)
        deadline = time.monotonic() + _ECHO_TIMEOUT_S
        while GPIO.input(self._echo) == GPIO.LOW:
            if time.monotonic() > deadline:
                logger.warning(
                    "UltrasonicSensor (TRIG=GPIO%d): echo-start timeout", self._trig
                )
                return None
        pulse_start = time.monotonic()

        # Wait for ECHO to go LOW (end of reflected pulse)
        deadline = pulse_start + _ECHO_TIMEOUT_S
        while GPIO.input(self._echo) == GPIO.HIGH:
            if time.monotonic() > deadline:
                logger.warning(
                    "UltrasonicSensor (TRIG=GPIO%d): echo-end timeout", self._trig
                )
                return None
        pulse_end = time.monotonic()

        distance_cm = (pulse_end - pulse_start) * _SPEED_OF_SOUND_CM_S / 2.0
        logger.debug(
            "UltrasonicSensor (TRIG=GPIO%d): %.1f cm", self._trig, distance_cm
        )
        return distance_cm


# ---------------------------------------------------------------------------
# IR Sensor with Software Debouncing
# ---------------------------------------------------------------------------

class DebouncedIRSensor:
    """
    IR proximity sensor with 3-second software debounce.

    The raw GPIO pin is sampled in a background daemon thread every
    POLL_INTERVAL seconds.  A raw reading must be **continuously stable
    for at least IR_DEBOUNCE_SECONDS (3 s)** before the publicly reported
    state (`is_occupied()`) changes.  This eliminates false triggers caused
    by transient reflections, mechanical vibration, or electrical noise.

    Pin polarity
    ------------
    Most IR sensor modules pull their output pin LOW when an obstacle is
    detected (active-low).  Pass ``active_low=False`` if your module uses
    active-high logic.

    Usage::

        sensor = DebouncedIRSensor(pin=IR_SLOT1_PIN)
        if sensor.is_occupied():
            print("Slot 1 is occupied")
        sensor.stop()
    """

    POLL_INTERVAL = 0.05  # seconds between raw GPIO reads

    def __init__(self, pin: int, active_low: bool = True) -> None:
        self._pin = pin
        self._active_low = active_low

        GPIO.setup(self._pin, GPIO.IN)

        # Bootstrap: treat the initial reading as the stable state so
        # is_occupied() returns a meaningful value immediately.
        initial_raw = GPIO.input(self._pin)
        initial_state = self._raw_to_occupied(initial_raw)

        self._stable_state: bool = initial_state
        self._pending_state: bool = initial_state
        self._pending_since: float = time.monotonic()

        self._lock = threading.Lock()
        self._running = True

        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"IRDebounce-GPIO{pin}",
        )
        self._thread.start()

        logger.info(
            "DebouncedIRSensor on GPIO %d started "
            "(active_low=%s, debounce=%d s, initial_state=occupied:%s)",
            self._pin,
            self._active_low,
            IR_DEBOUNCE_SECONDS,
            initial_state,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_to_occupied(self, raw_value: int) -> bool:
        """Map a raw GPIO level to the logical 'occupied' boolean."""
        if self._active_low:
            return raw_value == GPIO.LOW
        return raw_value == GPIO.HIGH

    def _poll_loop(self) -> None:
        """
        Background thread body.

        Algorithm:
          1. Read raw GPIO level.
          2. Convert to logical state.
          3. If the new reading differs from *pending*, reset the pending
             timer (the signal is still bouncing).
          4. If the new reading matches *pending* and has been stable for
             >= IR_DEBOUNCE_SECONDS, commit it as the new stable state.
        """
        while self._running:
            raw = GPIO.input(self._pin)
            new_state = self._raw_to_occupied(raw)
            now = time.monotonic()

            with self._lock:
                if new_state != self._pending_state:
                    # Signal changed direction — restart the stability timer
                    self._pending_state = new_state
                    self._pending_since = now
                elif (now - self._pending_since) >= IR_DEBOUNCE_SECONDS:
                    # Signal has been stable long enough — commit
                    if new_state != self._stable_state:
                        logger.debug(
                            "GPIO %d debounce committed: occupied=%s",
                            self._pin,
                            new_state,
                        )
                        self._stable_state = new_state

            time.sleep(self.POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_occupied(self) -> bool:
        """
        Return the debounced occupancy state.

        True  — an obstacle has been continuously detected for ≥ 3 s.
        False — no obstacle detected for ≥ 3 s (slot is free).
        """
        with self._lock:
            return self._stable_state

    def stop(self) -> None:
        """Signal the background thread to exit and wait for it to finish."""
        self._running = False
        self._thread.join(timeout=1.0)
        logger.info("DebouncedIRSensor on GPIO %d stopped", self._pin)
