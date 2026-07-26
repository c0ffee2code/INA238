# INA238 — MicroPython driver for Raspberry Pi Pico 2

Driver for the Adafruit INA238 breakout, a 16-bit precision power monitor
(current, bus voltage, power, temperature) over I2C.

## Project layout

```
src/ina238.py        — the driver
tools/read_power.py  — host script: sample power draw, print table + min/avg/max
references/          — INA237 datasheet (SBOSA20A, Rev A, May 2022) — see note below
README.md            — public-facing docs: wiring, API reference, gotchas
CLAUDE.md            — this file: rig specifics, diagnosis history, design rationale
LICENSE              — GPL-3.0
```

`README.md` and this file have different audiences and both need updating when
the driver's surface changes. README covers the API and the two hardware gotchas
(common ground for VBUS, conversion-cycle pacing); CLAUDE.md carries the
rig-specific detail, datasheet citations and the debugging history that a public
readme shouldn't.

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

## RESOLVED — VBUS read 0 because the assembly had no common ground

**Do not re-debug this, and do not suspect the breakout.** Between 2026-07-18
and 2026-07-26, `bus_voltage_v()` read a flat `0x0000` while
`shunt_voltage_v()`/`current_a()` worked and agreed with each other. Root cause,
identified by the user on 2026-07-26: **the ESC's supply and the Pico/INA did
not share a ground.** The Pico powered the INA, but the 12V rail being measured
floated relative to the INA's own GND.

That asymmetry is exactly what the register map predicts:

- `VSHUNT` is a **differential** measurement across `VIN+`/`VIN-`, so it stays
  valid no matter where the pair sits relative to the INA's ground — which is
  why current read correctly (~60mA) the whole time.
- `VBUS` is **single-ended against the INA's GND** (Datasheet §7.6.1.5 Table
  7-9 p.23), so with a floating supply it has no meaningful reference and
  reports `0x0000`. `power_w()` is derived from VBUS internally, so it was
  equally invalid.

Tying the whole assembly to a common ground fixed it: VBUS now reads 12.2469V,
matching the multimeter's 12.15–12.27V at the `VIN+` terminal.

The earlier diagnosis in this file — a bad `VBUS`-to-`VIN+` jumper/trace on this
physical unit, possibly warranting an Adafruit RMA — **was wrong**. It is the
same physical breakout, and it is fine. Generalizable lesson: on this part, a
plausible-looking current reading is *not* evidence that the ground reference is
sound, because only VBUS/POWER depend on it.

## Rig topology — what "power draw" actually includes

```
PSU(+) → INA VIN+ → shunt → VIN- → ESC(+) → 12V pass-through
       → buck converter → 5V → Pico          (common ground throughout)
```

Every measurement is of the **whole downstream assembly** — ESC + buck converter
+ the Pico itself — not the ESC alone, because all PSU current crosses the shunt.
This accounts for the rise from ~60mA (2026-07-18, ESC only, no common ground)
to ~72mA now.

The 12V ESC pass-through feeding the buck converter is what destroyed the
previous Pico (12V into a 5V rail). The buck converter exists to prevent a
repeat — do not suggest wiring the Pico to that rail directly.

## Key design notes

- `INA238.__init__` takes `shunt_ohms`, `max_current_a`, `adc_range` and runs
  in a deliberate order: **validate every argument and compute SHUNT_CAL first,
  then identify the device, and only then write anything.**

  1. Validate `adc_range in (0, 1)`, `shunt_ohms > 0`, `max_current_a > 0`
  2. Check `max_current_a × shunt_ohms` fits the ADCRANGE full scale
     (§8.2.2.1 Eq. 5 p.33) — catches silent saturation the register-field
     check cannot, e.g. 5A × 15mΩ = 75mV against ADCRANGE=1's ±40.96mV
  3. Compute SHUNT_CAL and require `1 <= shunt_cal <= 0x7FFF`
  4. Verify MANUFACTURER_ID (fails loudly on mismatch)
  5. Soft reset (CONFIG.RST) → set CONFIG.ADCRANGE → write SHUNT_CAL
  6. Check DIAG_ALRT.MEMSTAT for a trim-memory checksum error

  **The WHO_AM_I-before-write ordering is load-bearing, not stylistic.** This
  I2C bus is shared with an ADC at `0x2a` and an RTC at `0x68`. Configuring
  before identifying meant a wrong `addr` blind-wrote `RST=0x8000` and
  SHUNT_CAL into an unrelated device — on a DS3231-class RTC that is the
  control/timekeeping area — and only *then* "failed loudly", after the damage.
  Read-only ID registers are valid pre-reset, so there is no reason to write
  first. Don't reorder this back.

  `ADC_CONFIG` is left at its POR default (continuous VBUS+VSHUNT+temp
  conversion) rather than rewritten — see the module-level comment in
  `src/ina238.py` for the decoded reset value.
- `adc_range` is used as an **index** into `VSHUNT_LSB_V` / `VSHUNT_FULL_SCALE_V`
  rather than truth-tested, so the validated 0/1 domain is structural. The old
  code masked it on the register write (`(adc_range & 1) << 4`) but truth-tested
  it for the LSB and the ×4 calibration, so `adc_range=2` configured the wide
  range while scaling for the narrow one — 4× wrong, silently.
- `SHUNT_CAL`'s lower bound is 1, not 0: §8.1.2 p.29 says a zero SHUNT_CAL makes
  CURRENT report zero (§8.2.2.3 p.33 likewise for power), so 0 would read a flat
  `0.0A` forever instead of erroring. Reachable by rounding, verified on
  hardware — `shunt_ohms=1e-6` (→0.125) and `max_current_a=0.001` (→0.375) both
  round to 0 and now raise, while `shunt_ohms=1e-5` gives 1.25→1 and is
  legitimately accepted.
- Diagnostics are exposed via `diag_alrt()` / `math_overflow()` /
  `conversion_ready()` / `memory_ok()` (DIAG_ALRT 0x0B, §7.6.1.9 Table 7-13
  p.24-26). `math_overflow()` matters because MATHOF is the datasheet's own
  "current and power data may be invalid" flag and nothing else surfaces it —
  `current_a()`/`power_w()` otherwise return overflowed values that look fine.
  `memory_ok()` has inverted sense (1 = healthy). Note `conversion_ready()` is
  weak for pacing in continuous mode: ALATCH stays at its POR default 0h
  (Transparent), so CNVRF reads 1 almost always.
- Default calibration (`shunt_ohms=0.015`, `max_current_a=5.0`, `adc_range=0`)
  matches this project's setup: Adafruit's onboard 15mΩ shunt, calibrated for
  a 12V/5A drone ESC + bench supply rig rather than the board's full 10A rating,
  which roughly doubles current/power resolution.
- **Known bug in Adafruit's reference driver, not replicated here:**
  `adafruit_ina23x.py`'s `power` property reads only 16 of the POWER register's
  24 bits and multiplies by `20.0 × current_lsb`; the datasheet (§8.1.2 Eq. 4,
  verified against its own worked example in §8.2.2.5) says `0.2 × current_lsb`
  on the full 24-bit value. `power_w()` here follows the datasheet.
  **Confirmed on hardware 2026-07-26 — both halves, fully settled.**
  `power_w()` matches VBUS × CURRENT to within 0.03% (raw POWER 29301 → 0.8942W
  vs 12.2375V × 73.09mA = 0.8944W).
  Adafruit's net error is **2.56× low**, not 100× high. Confirmed against
  Adafruit's actual source (not just this repo's comment):
  [`adafruit_ina23x.py`](https://github.com/adafruit/Adafruit_CircuitPython_INA23x)
  line 40 `_raw_power = ROUnaryStruct(0x08, ">H")`, lines 82-84
  `return self._raw_power * 20.0 * self._current_lsb`.
  Reads are MSB-first (verified: a 1-byte read of DIETEMP `0x0D30` returns
  `0x0D`), so `>H` on the 24-bit POWER register takes bits[23:8] = `raw >> 8` —
  *not* the low 16 bits, and that holds **at any magnitude**. So
  `20.0 × (raw>>8)` against the datasheet's `0.2 × raw` is a ratio of
  100/256 = 0.3906.
  This also settles the read width: a 2-byte read would give `raw>>8` = 114 →
  0.0035W under `0.2×` or 0.3479W under Adafruit's `20.0×`, neither of which is
  0.894W. **No higher-current test is needed** — an earlier revision of this
  file wrongly claimed one was, on the false premise that a 16-bit read
  truncates to the low bits.
- Default I2C address is `0x40`, corresponding to A0=GND, A1=GND (Datasheet
  §7.5.1, Table 7-2, p.18). The device exposes 16 pin-selectable addresses
  (0x40–0x4F) via A0/A1 tied to GND/VS/SDA/SCL.

## Device info

- I2C address: `0x40` (configurable via A0/A1 pins — Datasheet §7.5.1 Table 7-2 p.18)
- Target wiring: SDA=GP4, SCL=GP5 (I2C0), 400 kHz — this Pico sits on an
  [Adafruit PicoWbell Proto Under Plate](https://learn.adafruit.com/adafruit-proto-under-plate-picowbell),
  which hardwires its STEMMA QT connector to IO4 (SDA) / IO5 (SCL), not the
  GP0/GP1 pair used by this workspace's other single-sensor repos
- Verify with: `i2c.scan()` → returns `[42, 64, 104]` (`0x2a`, `0x40`, `0x68`).
  Only `0x40` is the INA238. `0x2a` is an ADC and `0x68` an RTC that share the
  bus for a later stage of the user's project — they are **expected**, stable
  across repeated scans, and not this driver's concern.
- Pico is on **COM10** (RP2350, MicroPython 1.28.0). The COM7 board was
  destroyed by 12V on its 5V rail; this is its replacement.
- Default calibration: shunt_ohms=0.015 (Adafruit onboard shunt), max_current_a=5.0
  (this project's 12V/5A bench supply) → current_lsb≈152.6µA/LSB, SHUNT_CAL=1875 (0x753)
- `ADC_CONFIG` POR default verified on real silicon as `0xFB68`, which decodes
  bit-for-bit to the documented reset state (MODE=Fh, VBUSCT=VSHCT=VTCT=5h,
  AVG=0h) — so `__init__`'s choice to rely on the default is sound.

## Baseline measurement (2026-07-26, ESC idle, beeping)

12 samples at AVG=64, whole assembly (see topology above):

| | vshunt | vbus | current | power | die temp |
|---|---|---|---|---|---|
| avg | 1.084 mV | 12.2469 V | 72.3 mA | 0.886 W | 27.9 °C |

Later runs the same day read 12.2063–12.2094V rather than 12.2469V — that is the
bench supply sagging slightly over the session, not an inconsistency; VBUS is
rock-steady *within* any given run (often identical on all samples). Current and
power stayed at ~72–73mA / ~0.89W throughout.

Cross-checks: Ohm's law on VSHUNT (1.084mV / 15mΩ = 72.3mA) agrees with
`current_a()` independently of `SHUNT_CAL`, and `power_w()` agrees with
VBUS × CURRENT. **Do these on an AVG≥64 run only.** On a raw AVG=1 run,
`shunt_voltage_v()` and `current_a()` are separate register reads ~200µs apart
within a 3.16ms conversion cycle, so a fresh conversion can land between them
and individual rows disagree by >10% (e.g. an observed row of 1.0500mV → 70.0mA
alongside `current_a()` = 78.888mA). That is sampling skew, not a driver bug.

Note both cross-checks are *internal consistency* only — POWER is derived from
the same VSHUNT conversion, so neither tests absolute current accuracy.

**Open discrepancy (INA is probably right):** the bench PSU's own display reads
~0.09–0.12A / up to 1W, vs the INA's 72.3mA / 0.886W — a ratio of 1.25–1.67×.
Investigated as follows:

- *Pulsing load aliasing against sparse sampling* — ruled out. Hardware
  averaging did not close the gap; AVG=256 converges to 72.1mA with a spread of
  only 71.4–73.9mA.
- *Wrong `shunt_ohms`* — ruled out. A 10mΩ shunt read as 15mΩ would give
  exactly 1.5×, suspiciously central to the observed band, but Adafruit
  documents the onboard part as **15mΩ ±0.1%**
  ([learn guide](https://learn.adafruit.com/adafruit-ina237-dc-current-voltage-power-monitor),
  [product page](https://www.adafruit.com/product/6349)). A 0.1% part cannot
  produce a 25–67% error.
- *PSU ammeter imprecision at the low end of a 10A-range display* — the only
  remaining candidate, and now the likely one.

Confirming test if it ever matters: multimeter in DC millivolt mode straight
across the `VIN+`/`VIN-` terminals — ~1.08mV vindicates the INA, ~1.5mV would
mean the shunt is not what Adafruit documents. **Not yet performed.**

## Development

Tested on MicroPython for RP2350. Deploy with `mpremote`:

```bash
python -m mpremote connect COM10 cp src/ina238.py :ina238.py
```

Run host tools:

```bash
python tools/read_power.py            # 20 samples, no averaging
python tools/read_power.py 50         # 50 samples
python tools/read_power.py 12 64      # 12 samples, hardware AVG=64
```

`read_power.py` paces its sampling to the conversion cycle (~3.16ms × AVG) and
discards the first sample. Both matter: reading a continuously-converting
INA238 faster than it converts returns **latched values from the previous
conversion**, which shows up as each column repeating across consecutive
samples, staggered per channel — subtle enough to look like real jitter while
silently corrupting a min/avg/max summary. The first sample after init reads
`0x0000` on channels whose first conversion hasn't completed.
