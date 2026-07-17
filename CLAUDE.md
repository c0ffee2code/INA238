# INA238 — MicroPython driver for Raspberry Pi Pico 2

Driver for the Adafruit INA238 breakout, a 16-bit precision power monitor
(current, bus voltage, power, temperature) over I2C.

## Project layout

```
src/ina238.py       — the driver
tools/               — PC-side host scripts (run with Python, talk to Pico via mpremote)
references/          — INA237 datasheet (SBOSA20A, Rev A, May 2022) — see note below
```

## Datasheet note — INA237 vs INA238

`references/ina237.pdf` is TI's **INA237** datasheet, not INA238. It never
mentions the INA238 part number anywhere in its text. It is used here as the
register-map reference because Adafruit documents the INA238 as
code/register-compatible with the INA237 (per the user, who trusts Adafruit's
own characterization of their breakout). All register addresses, bit fields,
and sequencing citations in this driver are taken from this INA237 datasheet
under that compatibility assumption.

If any register-level behavior on real INA238 hardware ever looks wrong or
surprising, re-verify against TI's actual INA238 datasheet before assuming a
driver bug — the compatibility claim comes from Adafruit, not from TI
documentation in this repo.

## Driver conventions

- All names (register constants, methods, instance attributes) have NO underscore prefix
  (`self.i2c` not `self._i2c`, `read_reg()` not `_read_reg()`)
- MicroPython has no visibility enforcement — underscores add noise without benefit
- Every register value / mask / sequencing choice cites Datasheet §section p.page
- No 3rd-party unverified GitHub repos as reference — only the official TI datasheet

## Key design notes

- Driver currently a constructor-only stub (`src/ina238.py`); register-level
  read/write methods are not yet implemented — that's follow-up work for the
  `pico2-micropython` skill, not this scaffold.
- Default I2C address is `0x40`, corresponding to A0=GND, A1=GND (Datasheet
  §7.5.1, Table 7-2, p.18). The device exposes 16 pin-selectable addresses
  (0x40–0x4F) via A0/A1 tied to GND/VS/SDA/SCL.

## Device info

- I2C address: `0x40` (configurable via A0/A1 pins — Datasheet §7.5.1 Table 7-2 p.18)
- Target wiring: SDA=GP0, SCL=GP1 (I2C0), 400 kHz
- Verify with: `i2c.scan()` → should return `[64]`
- Pico is on COM7

## Development

Tested on MicroPython for RP2350. Deploy with `mpremote`:

```bash
python -m mpremote connect COM7 cp src/ina238.py :ina238.py
```

Run host tools:

```bash
# none yet — tools/ is empty
```
