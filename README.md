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
- WHO_AM_I sanity check (`MANUFACTURER_ID`) on construction — fails loudly on
  a wiring/address mistake instead of silently returning garbage

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

Resets the device, sets the shunt voltage full-scale range (`adc_range=0` for
±163.84mV, `1` for ±40.96mV), computes and writes `SHUNT_CAL` from
`shunt_ohms`/`max_current_a`, and verifies `MANUFACTURER_ID`. Raises
`ValueError` if the shunt/current combination doesn't fit the calibration
register, or `RuntimeError` if the device doesn't respond as expected.

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

## Host tools

None yet.

## Deploy

```bash
python -m mpremote connect COM7 cp src/ina238.py :ina238.py
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
