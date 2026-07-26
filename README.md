# INA238 MicroPython Driver

MicroPython driver for the Adafruit INA238 breakout, a 16-bit precision power
monitor (current, bus voltage, power, temperature) over I2C, on
**Raspberry Pi Pico 2 (RP2350)**.

Tested with the [Adafruit INA238 STEMMA QT breakout](https://www.adafruit.com/product/6349),
which has an onboard 15mΩ, 0.1% shunt resistor (10A max rating).

Register-map reference: `references/ina237.pdf` (TI INA237, SBOSA20A Rev A) —
see the datasheet note in `CLAUDE.md` for why an INA237 datasheet is used for
an INA238 driver (Adafruit documents them as code-compatible).

## Features

- Shunt voltage, bus voltage, die temperature, current, and power reads,
  each converted to engineering units per the datasheet's conversion factors
- Calibrated current/power via `SHUNT_CAL`, configurable for any shunt
  resistance / max expected current (`shunt_ohms`, `max_current_a` constructor args)
- WHO_AM_I sanity check (`MANUFACTURER_ID`) **before any register write** — fails
  loudly on a wiring/address mistake instead of writing into whatever device
  actually lives at that address
- Constructor argument validation that fails loudly rather than silently
  mis-scaling: rejects an out-of-domain `adc_range`, a `SHUNT_CAL` that would
  round to zero (which makes the device report 0 A forever), and a
  shunt/current combination that exceeds the selected ADC full scale
- Device diagnostics exposed (`DIAG_ALRT`), including `MATHOF` — the
  datasheet's own "current and power data may be invalid" flag

## Gotchas

Two non-obvious failure modes, both found the hard way on real hardware:

**VBUS needs a common ground.** `shunt_voltage_v()` is a *differential*
measurement across `VIN+`/`VIN-` and stays valid however the sensed rail floats
relative to the INA. `bus_voltage_v()` is *single-ended against the INA's GND*.
So if the supply being measured doesn't share a ground with the INA, current
reads perfectly plausible values while `bus_voltage_v()` sits at exactly
`0x0000` and `power_w()` (derived from VBUS) is silently invalid. **A believable
current reading is not evidence your ground reference is sound.** Tie the whole
assembly to a common ground.

**Don't read faster than the device converts.** At the POR default the ADC
round-robins VBUS, VSHUNT and temperature at 1052 µs each, so a full cycle is
~3.16 ms × `AVG`. Read faster than that and registers return *latched values
from the previous conversion* — each field repeats across consecutive samples,
staggered per channel. It looks like ordinary jitter but quietly corrupts any
min/avg/max you compute. Registers also read `0x0000` until the first
conversion after construction completes. `tools/read_power.py` handles both.

## Wiring

This Pico sits on an [Adafruit PicoWbell Proto Under Plate](https://learn.adafruit.com/adafruit-proto-under-plate-picowbell),
which hardwires its STEMMA QT connector to IO4 (SDA) / IO5 (SCL) — I2C0's
default pins — rather than GP0/GP1.

| Pico 2        | INA238 breakout |
|---------------|-----------------|
| 3V3 (pin 36)  | 3V              |
| GND (pin 38)  | GND             |
| GP4    (SDA)  | SDA             |
| GP5    (SCL)  | SCL             |

Adafruit STEMMA QT boards typically ship with onboard I2C pull-ups, so no
external pull-up resistors should be needed — worth confirming against the
board if `i2c.scan()` doesn't find the device.

## Usage

```python
from machine import I2C, Pin
from ina238 import INA238

i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
# shunt_ohms/max_current_a default to this project's setup: Adafruit's
# onboard 15mOhm shunt, calibrated for a 12V/5A bench supply + ESC.
sensor = INA238(i2c)

print(sensor.bus_voltage_v(), sensor.current_a(), sensor.power_w())
```

## API

### `INA238(i2c, addr=0x40, shunt_ohms=0.015, max_current_a=5.0, adc_range=0)`

Validates arguments, identifies the device, then configures it — in that order,
so a wrong `addr` can't write into an unrelated device on a shared bus:

1. Validates `adc_range in (0, 1)`, `shunt_ohms > 0`, `max_current_a > 0`
2. Checks `max_current_a × shunt_ohms` against the selected ADC full scale
   (`adc_range=0` → ±163.84 mV, `1` → ±40.96 mV)
3. Computes `SHUNT_CAL` and requires it to land in `1 … 0x7FFF`
4. Verifies `MANUFACTURER_ID`
5. Soft-resets, writes `CONFIG.ADCRANGE`, writes `SHUNT_CAL`
6. Checks `DIAG_ALRT.MEMSTAT` for a trim-memory checksum error

`ADC_CONFIG` is deliberately left at its power-on default (continuous
VBUS + VSHUNT + temperature conversion).

Raises `ValueError` for any invalid argument — including a shunt/current
combination that overflows the calibration register, one that would saturate
the ADC full scale (e.g. 5 A × 15 mΩ = 75 mV against `adc_range=1`), or one
whose `SHUNT_CAL` rounds to zero. Raises `RuntimeError` if `MANUFACTURER_ID`
doesn't match or the device reports a trim-memory error.

### `sensor.shunt_voltage_v()` → `float`

Differential voltage across the shunt, in volts. Can be negative (bidirectional current).

### `sensor.bus_voltage_v()` → `float`

Bus voltage, in volts.

### `sensor.temperature_c()` → `float`

Internal die temperature, in °C. Can be negative.

### `sensor.current_a()` → `float`

Calculated current, in amperes, scaled per the `shunt_ohms`/`max_current_a`
calibration passed to the constructor. Can be negative (bidirectional current).

### `sensor.power_w()` → `float`

Calculated power, in watts. Always non-negative.

### `sensor.manufacturer_id()` → `int`

Raw `MANUFACTURER_ID` register value; expected `0x5449` ("TI" in ASCII).

### `sensor.math_overflow()` → `bool`

`DIAG_ALRT.MATHOF`. `True` means an internal arithmetic overflow occurred and,
per the datasheet, "current and power data may be invalid". Nothing else
surfaces this — `current_a()` and `power_w()` otherwise return overflowed
values that look indistinguishable from good ones. Check it when a reading
looks impossible.

### `sensor.memory_ok()` → `bool`

`DIAG_ALRT.MEMSTAT`. `True` is healthy; `False` means the device detected a
checksum error in its factory trim memory, so its readings can't be trusted.
Note the inverted sense relative to the other flags. Checked at construction.

### `sensor.conversion_ready()` → `bool`

`DIAG_ALRT.CNVRF`. Of limited use for pacing in continuous mode: `ALATCH` is
left at its default (Transparent), so conversions land every cycle and this
reads `True` almost always. Sleeping for a full conversion cycle is the
practical approach — see the gotcha above.

### `sensor.diag_alrt()` → `int`

Raw `DIAG_ALRT` register word, for flags the helpers above don't wrap.

## Host tools

### `tools/read_power.py`

Samples power draw and prints a per-sample table plus a min/avg/max summary.
Run with PC Python; it drives the Pico over `mpremote`.

```bash
python tools/read_power.py            # 20 samples, no averaging
python tools/read_power.py 50         # 50 samples
python tools/read_power.py 12 64      # 12 samples, hardware AVG=64
```

The optional second argument sets hardware averaging (1, 4, 16, 64, 128, 256,
512, 1024), which the device applies by accumulating that many conversions per
reported value. Prefer `AVG >= 64` for anything where you compare fields
against each other: a single row is five separate register reads, so at `AVG=1`
a new conversion can land mid-row and that row's fields come from different
conversions. Edit `PORT` at the top of the script to match your board.

## Deploy

```bash
python -m mpremote connect COM10 cp src/ina238.py :ina238.py
```

(`COM10` is this project's port — substitute your own.)

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
