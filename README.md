# INA238 MicroPython Driver

MicroPython driver for the Adafruit INA238 breakout, a 16-bit precision power
monitor (current, bus voltage, power, temperature) over I2C, on
**Raspberry Pi Pico 2 (RP2350)**.

Register-map reference: `references/ina237.pdf` (TI INA237, SBOSA20A Rev A) —
see the datasheet note in `CLAUDE.md` for why an INA237 datasheet is used for
an INA238 driver (Adafruit documents them as code-compatible).

## Status

This repo is currently scaffolding only. `src/ina238.py` is a
constructor-only stub — register-level read/write methods (current, bus
voltage, power, temperature) are not implemented yet.

## Wiring

| Pico 2        | INA238 breakout |
|---------------|-----------------|
| 3V3 (pin 36)  | 3V              |
| GND (pin 38)  | GND             |
| GP0    (SDA)  | SDA             |
| GP1    (SCL)  | SCL             |

Verify pull-up requirements against the actual Adafruit breakout — this has
not yet been confirmed against the board.

## Usage

```python
from machine import I2C, Pin
from ina238 import INA238

i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400_000)
sensor = INA238(i2c)  # default address 0x40 (A0=GND, A1=GND)
```

Read methods are not implemented yet — see Status above.

## API

### `INA238(i2c, addr=0x40)`

Stores the I2C bus handle and device address. No register access happens in
the constructor yet.

## Host tools

None yet.

## Deploy

```bash
python -m mpremote connect COM7 cp src/ina238.py :ina238.py
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
