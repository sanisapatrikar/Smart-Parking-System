# Smart Parking System

A modular, Raspberry Pi 4B–based Smart Parking System for a mall environment. The system uses computer vision (ALPR via EasyOCR), ultrasonic and IR sensors, a servo-controlled gate, and a 16×2 I2C LCD to automate vehicle entry, slot allocation, exit detection, and dynamic billing.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Hardware Components](#hardware-components)
3. [GPIO Pin Assignment](#gpio-pin-assignment)
4. [Wiring Instructions](#wiring-instructions)
   - [Power & Ground Rail](#power--ground-rail)
   - [I2C LCD Display](#i2c-lcd-display)
   - [Servo Motor (Gate)](#servo-motor-gate)
   - [Entrance Ultrasonic Sensor (HC-SR04)](#entrance-ultrasonic-sensor-hc-sr04)
   - [Slot 3 Ultrasonic Sensor (HC-SR04)](#slot-3-ultrasonic-sensor-hc-sr04)
   - [Slot 1 IR Sensor](#slot-1-ir-sensor)
   - [Slot 2 IR Sensor](#slot-2-ir-sensor)
   - [Raspberry Pi Camera Module](#raspberry-pi-camera-module)
5. [⚠️ Voltage Divider Warning (HC-SR04 Echo Pin)](#️-voltage-divider-warning-hc-sr04-echo-pin)
6. [Repository File Structure](#repository-file-structure)
7. [Core Logic Flow](#core-logic-flow)
8. [Dynamic Pricing](#dynamic-pricing)
9. [Software Setup](#software-setup)
   - [Prerequisites](#prerequisites)
   - [System Dependencies](#system-dependencies)
   - [Python Dependencies](#python-dependencies)
   - [Enable Required Interfaces](#enable-required-interfaces)
   - [Run the System](#run-the-system)
10. [database.json Schema](#databasejson-schema)

---

## Architecture Overview

The project is intentionally split into focused modules rather than a single monolithic script:

| File | Responsibility |
|---|---|
| `main.py` | State machine and core control loop |
| `hardware.py` | GPIO abstractions: Servo, IR sensors (debounced), Ultrasonic sensors, I2C LCD |
| `vision.py` | ALPR via EasyOCR with OpenCV preprocessing; returns `None` if recognition fails (no QR fallback) |
| `billing.py` | Entry/exit time tracking and dynamic cost calculation |
| `database.json` | Lightweight local file-store for active parked vehicles |

---

## Hardware Components

| Qty | Component | Notes |
|-----|-----------|-------|
| 1 | Raspberry Pi 4B | Brain of the system |
| 1 | Raspberry Pi Camera Module v2 | Connected via CSI ribbon cable |
| 1 | 16×2 I2C LCD Display (PCF8574 backpack) | Address typically `0x27` or `0x3F` |
| 1 | Standard Servo Motor (e.g., SG90 / MG996R) | Gate control |
| 2 | HC-SR04 Ultrasonic Sensor | Entrance trigger + Slot 3 occupancy |
| 2 | IR Sensor Module (digital output) | Slot 1 & Slot 2 occupancy |
| 1 | Breadboard + Jumper Wires | Prototyping |
| 1 | 1 kΩ Resistor | Voltage divider (Echo pin) — see warning |
| 1 | 2 kΩ Resistor | Voltage divider (Echo pin) — see warning |
| 1 | 5 V / 3 A USB-C Power Supply | For the Pi |
| 1 | Servo external 5 V supply (optional) | Prevents Pi brownout under load |

---

## GPIO Pin Assignment

All pin numbers use the **BCM (Broadcom) numbering** scheme unless otherwise stated.

| Signal | BCM GPIO | Physical Pin | Direction |
|---|---|---|---|
| I2C SDA (LCD) | GPIO 2 | Pin 3 | Bidirectional |
| I2C SCL (LCD) | GPIO 3 | Pin 5 | Bidirectional |
| Servo PWM | GPIO 18 | Pin 12 | Output (HW PWM0) |
| Entrance HC-SR04 TRIG | GPIO 23 | Pin 16 | Output |
| Entrance HC-SR04 ECHO | GPIO 24 | Pin 18 | Input *(via voltage divider)* |
| Slot 3 HC-SR04 TRIG | GPIO 20 | Pin 38 | Output |
| Slot 3 HC-SR04 ECHO | GPIO 21 | Pin 40 | Input *(via voltage divider)* |
| IR Sensor — Slot 1 | GPIO 17 | Pin 11 | Input |
| IR Sensor — Slot 2 | GPIO 27 | Pin 13 | Input |

> **Tip:** All GND pins are interchangeable; use any of the Pi's GND pins (e.g., Pins 6, 9, 14, 20, 25, 30, 34, 39) to build a shared ground rail on the breadboard.

---

## Wiring Instructions

### Power & Ground Rail

1. Connect **Pin 2 (5 V)** and **Pin 4 (5 V)** from the Pi to the **positive (+) rail** of the breadboard.
2. Connect **Pin 6 (GND)** from the Pi to the **negative (−) rail** of the breadboard.
3. All sensor VCC and GND connections described below reference these rails.

---

### I2C LCD Display

The LCD uses the PCF8574 I2C backpack. Only 4 wires are needed.

```
LCD Backpack Pin  →  Raspberry Pi
─────────────────────────────────
VCC               →  5 V rail (Pin 2 or Pin 4)
GND               →  GND rail
SDA               →  GPIO 2  (Pin 3)
SCL               →  GPIO 3  (Pin 5)
```

> After wiring, run `sudo i2cdetect -y 1` to confirm the device address (commonly `0x27`).
> Update `LCD_ADDRESS` in `hardware.py` if your module shows a different address.

---

### Servo Motor (Gate)

```
Servo Wire   →  Connection
──────────────────────────
Brown/Black  →  GND rail
Red          →  5 V rail  (use external 5 V supply if servo draws > 500 mA)
Orange/White →  GPIO 18  (Pin 12)  — Hardware PWM
```

> `hardware.py` uses **pigpio** to drive Hardware PWM on GPIO 18 for jitter-free servo control.
> Ensure `pigpiod` daemon is running before starting the application (`sudo pigpiod`).

---

### Entrance Ultrasonic Sensor (HC-SR04)

```
HC-SR04 Pin  →  Connection
──────────────────────────
VCC          →  5 V rail
GND          →  GND rail
TRIG         →  GPIO 23  (Pin 16)
ECHO         →  Voltage divider mid-point  (see ⚠️ warning below)
               Voltage divider output  →  GPIO 24  (Pin 18)
```

#### Voltage Divider for ECHO Pin

```
ECHO (5 V) ──── 1 kΩ ──── GPIO 24 (3.3 V safe)
                     |
                   2 kΩ
                     |
                    GND
```

---

### Slot 3 Ultrasonic Sensor (HC-SR04)

```
HC-SR04 Pin  →  Connection
──────────────────────────
VCC          →  5 V rail
GND          →  GND rail
TRIG         →  GPIO 20  (Pin 38)
ECHO         →  Voltage divider mid-point  (see ⚠️ warning below)
               Voltage divider output  →  GPIO 21  (Pin 40)
```

Use an identical 1 kΩ / 2 kΩ voltage divider as described above.

---

### Slot 1 IR Sensor

```
IR Module Pin  →  Connection
─────────────────────────────
VCC            →  5 V rail
GND            →  GND rail
OUT            →  GPIO 17  (Pin 11)
```

> IR sensor modules with on-board LDO regulators typically output a 3.3 V–compatible signal.
> Verify your module's datasheet; if the output swings to 5 V, add a voltage divider.

---

### Slot 2 IR Sensor

```
IR Module Pin  →  Connection
─────────────────────────────
VCC            →  5 V rail
GND            →  GND rail
OUT            →  GPIO 27  (Pin 13)
```

---

### Raspberry Pi Camera Module

1. Gently lift the locking tab on the Pi's **CAM/DISPLAY** CSI connector.
2. Insert the ribbon cable with the **blue backing facing the USB ports**.
3. Push the locking tab back down.
4. Enable the camera interface (see [Enable Required Interfaces](#enable-required-interfaces)).

---

## ⚠️ Voltage Divider Warning (HC-SR04 Echo Pin)

> **CRITICAL — READ BEFORE POWERING ON**
>
> The HC-SR04 ultrasonic sensor operates at **5 V logic**. Its **ECHO pin outputs up to 5 V**,
> which **exceeds the 3.3 V maximum** that the Raspberry Pi's GPIO pins can safely accept.
>
> Connecting the ECHO pin directly to a GPIO pin **WILL permanently damage** the Pi's I/O
> circuitry and may render the entire board unusable.
>
> **You MUST use a resistor voltage divider on EVERY HC-SR04 ECHO pin before connecting it to the Pi.**
>
> Recommended divider (scales 5 V → ~3.33 V):
>
> ```
> HC-SR04 ECHO (5 V) ──── R1 (1 kΩ) ──┬──── GPIO pin (3.3 V input)
>                                       │
>                                      R2 (2 kΩ)
>                                       │
>                                      GND
> ```
>
> Output voltage = 5 V × R2 / (R1 + R2) = 5 × 2000 / 3000 ≈ **3.33 V** ✓
>
> This applies to **both** HC-SR04 sensors used in this project:
> - **Entrance sensor** ECHO → GPIO 24
> - **Slot 3 sensor** ECHO → GPIO 21

---

## Repository File Structure

```
Smart-Parking-System/
├── main.py           # State machine & main control loop
├── hardware.py       # GPIO hardware abstractions (Servo, Ultrasonic, IR, LCD)
├── vision.py         # ALPR (EasyOCR) with OpenCV preprocessing; returns None on failure
├── billing.py        # Entry/exit time tracking & dynamic cost calculation
├── database.json     # Local JSON store for active parked vehicles
└── README.md         # This file
```

---

## Core Logic Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  STATE: IDLE                                                        │
│  LCD: "Welcome!  Slots Left: X"                                     │
│  Loop: poll entrance ultrasonic                                     │
└───────────────────────────┬─────────────────────────────────────────┘
                            │  Object detected < 10 cm
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STATE: TRIGGERED                                                   │
│  LCD: "Please stop for scanning"                                    │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
              ┌─────────────▼─────────────┐
              │   Capacity Check          │
              │   IR1 + IR2 + Ultrasonic3 │
              └───────┬───────────────────┘
                      │ All 3 slots occupied?
             Yes ◄────┤
              │       │ No
              ▼       ▼
   LCD: "Sorry,  ┌─────────────────────────────────────────────────────┐
   Parking Full" │  STATE: SCANNING                                    │
   → RESET       │  Capture frame from Camera Module                  │
                 │  1. Attempt ALPR (EasyOCR on number plate region)  │
                 │  2. If OCR returns None → system resets to IDLE    │
                 └───────────────────────┬─────────────────────────────┘
                                         │ plate_id obtained
                                         ▼
                 ┌─────────────────────────────────────────────────────┐
                 │  STATE: ENTRY & ALLOCATION                          │
                 │  Find lowest free slot (1, 2, or 3)                │
                 │  Write {plate_id, entry_time, slot} → database.json│
                 │  LCD: "Welcome [Plate] / Go to Slot [X]"           │
                 │  Open servo gate → wait 5 s → close gate           │
                 └───────────────────────┬─────────────────────────────┘
                                         │
                                         ▼
                 ┌─────────────────────────────────────────────────────┐
                 │  STATE: PARKED (background polling)                 │
                 │  Continuously poll all slot sensors (debounced)    │
                 │  IR sensors: 3-second stable state before update   │
                 └───────────────────────┬─────────────────────────────┘
                                         │ Slot sensor: Occupied → Empty
                                         ▼
                 ┌─────────────────────────────────────────────────────┐
                 │  STATE: EXIT & BILLING                              │
                 │  Retrieve entry_time + slot from database.json     │
                 │  Calculate bill (see Dynamic Pricing below)        │
                 │  Print final bill to terminal                      │
                 │  Remove vehicle record from database.json          │
                 │  Update LCD: "Welcome!  Slots Left: X"             │
                 └─────────────────────────────────────────────────────┘
```

---

## Dynamic Pricing

| Time Period | Rate |
|---|---|
| Off-peak (all hours except 17:00–22:00) | **Rs. 50 / hour** |
| Peak hours (17:00–22:00) | **Rs. 75 / hour** |

The billing engine (`billing.py`) splits the parked duration across off-peak and peak hour windows and charges each segment at the appropriate rate. Partial hours are billed proportionally (per-minute billing).

**Example:**

```
Entry:  2026-04-15 16:30
Exit:   2026-04-15 18:45
Total:  2h 15m

Off-peak segment:  16:30 → 17:00  =  30 min  →  Rs. 50/hr  →  Rs. 25.00
Peak segment:      17:00 → 18:45  =  1h 45m  →  Rs. 75/hr  →  Rs. 131.25
                                                              ───────────
                                                 Total Bill:  Rs. 156.25
```

---

## Software Setup

### Prerequisites

- Raspberry Pi OS (64-bit Lite or Desktop) — **Bullseye or Bookworm**
- Python 3.9+
- Internet connection for initial package installation

---

### Enable Required Interfaces

Run `sudo raspi-config` and enable:

1. **Interface Options → Camera** (legacy camera stack, if using libcamera-incompatible OpenCV builds)
2. **Interface Options → I2C** (for the LCD)
3. **Interface Options → SSH** (recommended for headless setup)

Then reboot:

```bash
sudo reboot
```

---

### System Dependencies

```bash
# Update package lists
sudo apt update && sudo apt upgrade -y

# OpenCV native libraries
sudo apt install -y python3-opencv libopencv-dev

# pigpio daemon (Hardware PWM for servo)
sudo apt install -y pigpio python3-pigpio

# I2C tools (scan for LCD address)
sudo apt install -y i2c-tools

# libcamera / picamera2 support
sudo apt install -y python3-picamera2

# EasyOCR native dependencies (BLAS/LAPACK for torch)
sudo apt install -y libatlas-base-dev
```

Enable and start the pigpio daemon so it auto-starts on boot:

```bash
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

Verify the I2C LCD address:

```bash
sudo i2cdetect -y 1
```

---

### Python Dependencies

```bash
# Create and activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install Python packages
pip install --upgrade pip
pip install RPi.GPIO pigpio
pip install RPLCD          # I2C LCD driver
pip install opencv-python-headless
pip install easyocr
pip install picamera2
```

> **Note:** `easyocr` pulls in PyTorch. On a Pi 4 this can take 10–20 minutes to install.
> Pre-download wheels on a faster machine if time is critical.

---

### Run the System

```bash
# Ensure pigpio daemon is running
sudo systemctl start pigpiod

# Activate virtual environment
source .venv/bin/activate

# Start the parking system
python main.py
```

To run as a system service that starts on boot, create `/etc/systemd/system/parking.service`:

```ini
[Unit]
Description=Smart Parking System
After=pigpiod.service
Requires=pigpiod.service

[Service]
ExecStart=/home/pi/Smart-Parking-System/.venv/bin/python /home/pi/Smart-Parking-System/main.py
WorkingDirectory=/home/pi/Smart-Parking-System
Restart=on-failure
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable parking.service
sudo systemctl start parking.service
```

---

## database.json Schema

The file is created automatically on first run if it does not exist. Its structure is:

```json
{
  "<plate_id>": {
    "entry_time": "<ISO-8601 timestamp>",
    "slot": <integer 1–3>
  }
}
```

**Example populated state:**

```json
{
  "MH12AB1234": {
    "entry_time": "2025-07-15T17:30:00",
    "slot": 2
  },
  "KA05XY9988": {
    "entry_time": "2025-07-15T18:05:00",
    "slot": 1
  }
}
```

An **empty object `{}`** means no vehicles are currently parked (the parking lot is fully vacant).

---

## Quick-Reference Wiring Diagram (Text)

```
Raspberry Pi 4B (BCM pin numbers)
──────────────────────────────────────────────────────────────
 3.3 V  [Pin 1 ]                       [ Pin 2 ] 5 V ──► rails
 GPIO2  [Pin 3 ] ◄──► LCD SDA          [ Pin 4 ] 5 V
 GPIO3  [Pin 5 ] ◄──► LCD SCL          [ Pin 6 ] GND ──► rail
 GPIO17 [Pin 11] ◄── IR Slot 1 OUT     [Pin 12 ] GPIO18 ──► Servo PWM
 GPIO27 [Pin 13] ◄── IR Slot 2 OUT     [Pin 14 ] GND
              ...
 GPIO23 [Pin 16] ──► Entrance TRIG     [Pin 18 ] GPIO24 ◄── Entrance ECHO*
              ...
 GPIO20 [Pin 38] ──► Slot3 TRIG        [Pin 40 ] GPIO21 ◄── Slot3 ECHO*

 * ECHO pins MUST go through a 1 kΩ / 2 kΩ voltage divider before the GPIO!
──────────────────────────────────────────────────────────────
```
