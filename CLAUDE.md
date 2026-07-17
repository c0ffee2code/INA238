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
- Primary source of truth is the TI datasheet. Adafruit's official
  `Adafruit_CircuitPython_INA23x` driver (vendor-maintained, not a random 3rd-party
  repo) is used as a cross-check, per pico2-micropython skill convention — but
  where it disagrees with the datasheet, the datasheet wins and the disagreement
  is called out with a `# DISAGREEMENT:` comment (see `power_w()` in `src/ina238.py`)

## Key design notes

- `INA238.__init__` takes `shunt_ohms`, `max_current_a`, `adc_range` and does a
  full calibration sequence: soft reset (CONFIG.RST) → set CONFIG.ADCRANGE →
  compute and write SHUNT_CAL → verify MANUFACTURER_ID (WHO_AM_I check, fails
  loudly on mismatch). `ADC_CONFIG` is left at its POR default (continuous
  VBUS+VSHUNT+temp conversion) rather than rewritten — see the module-level
  comment in `src/ina238.py` for the decoded reset value.
- Default calibration (`shunt_ohms=0.015`, `max_current_a=5.0`, `adc_range=0`)
  matches this project's setup: Adafruit's onboard 15mΩ shunt, calibrated for
  a 12V/5A drone ESC + bench supply rig rather than the board's full 10A rating,
  which roughly doubles current/power resolution.
- **Known bug in Adafruit's reference driver, not replicated here:**
  `adafruit_ina23x.py`'s `power` property reads only 16 of the POWER register's
  24 bits and multiplies by `20.0 × current_lsb`; the datasheet (§8.1.2 Eq. 4,
  verified against its own worked example in §8.2.2.5) says `0.2 × current_lsb`
  on the full 24-bit value. `power_w()` here follows the datasheet.
- Default I2C address is `0x40`, corresponding to A0=GND, A1=GND (Datasheet
  §7.5.1, Table 7-2, p.18). The device exposes 16 pin-selectable addresses
  (0x40–0x4F) via A0/A1 tied to GND/VS/SDA/SCL.

## Device info

- I2C address: `0x40` (configurable via A0/A1 pins — Datasheet §7.5.1 Table 7-2 p.18)
- Target wiring: SDA=GP4, SCL=GP5 (I2C0), 400 kHz — this Pico sits on an
  [Adafruit PicoWbell Proto Under Plate](https://learn.adafruit.com/adafruit-proto-under-plate-picowbell),
  which hardwires its STEMMA QT connector to IO4 (SDA) / IO5 (SCL), not the
  GP0/GP1 pair used by this workspace's other single-sensor repos
- Verify with: `i2c.scan()` → should return `[64]`
- Pico is on COM7
- Default calibration: shunt_ohms=0.015 (Adafruit onboard shunt), max_current_a=5.0
  (this project's 12V/5A bench supply) → current_lsb≈152.6µA/LSB, SHUNT_CAL=1875 (0x753)

## Development

Tested on MicroPython for RP2350. Deploy with `mpremote`:

```bash
python -m mpremote connect COM7 cp src/ina238.py :ina238.py
```

Run host tools:

```bash
# none yet — tools/ is empty
```
