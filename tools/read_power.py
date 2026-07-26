"""Read power draw from the INA238 on the Pico and print a table + summary.

Host-side script: run with PC Python, talks to the Pico over mpremote.

    python tools/read_power.py            # 20 samples, no averaging
    python tools/read_power.py 50         # 50 samples
    python tools/read_power.py 20 64      # 20 samples, hardware AVG=64

Measurement boundary — the whole downstream assembly, not just the ESC:

    PSU(+) -> INA VIN+ -> shunt -> VIN- -> ESC(+) -> 12V pass-through
           -> buck converter -> 5V -> Pico          (common ground throughout)

All PSU current crosses the shunt, so these numbers include the ESC, the buck
converter and the Pico itself. A common ground across the whole assembly is
also required for VBUS to mean anything: VSHUNT is differential across
VIN+/VIN-, but VBUS is single-ended against the INA's GND, so a floating ESC
supply makes VBUS read 0x0000 while current still reads correctly.

Sampling is paced to the conversion cycle. With MODE=Fh the device
round-robins VBUS, VSHUNT and temperature at VBUSCT/VSHCT/VTCT = 1052us each
(Datasheet §7.6.1.2 Table 7-6 p.21-22), so a full cycle is ~3.16ms x AVG.
Reading faster than that returns latched values from the previous conversion:
each column repeats across consecutive samples, staggered per channel, which
silently corrupts a min/avg/max summary. One full read set is also taken and
discarded before the loop, because registers read 0x0000 until the first
post-reset conversion completes.

Pacing makes each *row* fresh relative to the previous row; it does NOT make
the five columns within one row simultaneous. A row is five separate
readfrom_mem calls plus a serial print, which at AVG=1 comfortably exceeds the
3.16ms cycle, so a new conversion routinely lands mid-row and that row's
columns come from different conversions. Individual AVG=1 rows can therefore
disagree with each other by >10% (an observed row: vshunt 1.0500mV -> 70.0mA by
Ohm's law, alongside current_a() = 78.888mA). That is sampling skew, not a
driver bug. Use AVG>=64 for anything that cross-checks columns against each
other; at AVG=64 the 202ms cycle dwarfs the read set and rows tie out.

Averaging matters for a pulsing load. A beeping ESC draws current in bursts;
with AVG=1 each sample is one ~1ms conversion that may land between bursts, so
point samples read low next to a bench supply's averaged ammeter. Hardware
averaging accumulates N conversions per reported value instead.
"""

import subprocess
import sys

PORT = "COM10"
SDA_PIN = 4  # PicoWbell Proto Under Plate hardwires STEMMA QT to IO4/IO5
SCL_PIN = 5
I2C_FREQ = 400000

# ADC_CONFIG AVG field (bits[2:0]) -> averaging count.
# Datasheet (TI INA237, SBOSA20A Rev A) §7.6.1.2 Table 7-6 p.22
AVG_CODES = {1: 0x0, 4: 0x1, 16: 0x2, 64: 0x3, 128: 0x4, 256: 0x5, 512: 0x6, 1024: 0x7}

# One full round-robin of the three channels at 1052us each, AVG=1.
CYCLE_MS_AVG1 = 3 * 1.052

COLUMNS = ("vshunt_mV", "vbus_V", "current_mA", "power_W", "temp_C")

PICO_CODE = """
from machine import Pin, I2C
import time, sys
try:
    i2c = I2C(0, sda=Pin({sda}), scl=Pin({scl}), freq={freq})
    from ina238 import INA238
    s = INA238(i2c)

    # Raise hardware averaging if asked. Read-modify-write of just the AVG
    # field leaves MODE/VBUSCT/VSHCT/VTCT at their POR defaults --
    # Datasheet §7.6.1.2 Table 7-6 p.21-22.
    if {avg} != 1:
        cfg = int.from_bytes(s.read_reg(0x01), 'big')
        s.write_reg16(0x01, (cfg & ~0x0007) | {avg_code})

    print('INFO ADC_CONFIG=0x%04X SHUNT_CAL=0x%04X current_lsb=%.4fuA cycle=%.1fms' % (
        int.from_bytes(s.read_reg(0x01), 'big'),
        int.from_bytes(s.read_reg(0x02), 'big'),
        s.current_lsb * 1e6, {cycle_ms}))

    # Actually discard one full read set rather than just sleeping: the
    # constructor's soft reset restarts conversion, and the first set can still
    # land on the very first conversion boundary and return 0x0000 on channels
    # that have not completed. Then pace every row by a full cycle so each row
    # reads fresh conversions rather than latched leftovers (see the module
    # docstring on why this does not make a single row's columns simultaneous).
    time.sleep_ms({sleep_ms})
    s.shunt_voltage_v()
    s.bus_voltage_v()
    s.current_a()
    s.power_w()
    s.temperature_c()
    time.sleep_ms({sleep_ms})
    for _ in range({samples}):
        print('DATA %.4f %.4f %.3f %.4f %.2f' % (
            s.shunt_voltage_v() * 1000.0,
            s.bus_voltage_v(),
            s.current_a() * 1000.0,
            s.power_w(),
            s.temperature_c()))
        time.sleep_ms({sleep_ms})
except Exception as e:
    sys.print_exception(e)
    sys.exit(1)
"""


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    avg = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    if avg not in AVG_CODES:
        sys.exit("avg must be one of %s" % sorted(AVG_CODES))

    cycle_ms = CYCLE_MS_AVG1 * avg
    code = PICO_CODE.format(
        sda=SDA_PIN,
        scl=SCL_PIN,
        freq=I2C_FREQ,
        samples=samples,
        avg=avg,
        avg_code=hex(AVG_CODES[avg]),
        cycle_ms=round(cycle_ms, 1),
        # Round up, plus a 5% margin (floor 2ms) so a sample never lands exactly
        # on a conversion boundary and re-reads the previous latched value. The
        # margin scales with AVG rather than being a fixed few ms, which would
        # get proportionally thinner as the cycle grows toward 3.2s at AVG=1024.
        sleep_ms=int(cycle_ms * 1.05) + 2,
    )

    result = subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", PORT, "exec", code],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit(1)

    rows = []
    print("  %9s %9s %10s %8s %7s" % COLUMNS)
    for line in result.stdout.splitlines():
        if line.startswith("DATA "):
            values = [float(f) for f in line.split()[1:]]
            rows.append(values)
            print("  %9.4f %9.4f %10.3f %8.4f %7.2f" % tuple(values))
        else:
            print(line)

    if not rows:
        sys.exit("no samples returned")

    print("\n  %d samples, AVG=%d" % (len(rows), avg))
    for i, name in enumerate(COLUMNS):
        column = [r[i] for r in rows]
        print("  %-11s min %9.4f  avg %9.4f  max %9.4f"
              % (name, min(column), sum(column) / len(column), max(column)))


if __name__ == "__main__":
    main()