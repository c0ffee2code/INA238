# Register map — Datasheet (TI INA237, SBOSA20A Rev A) §7.6.1 Table 7-3 p.20
# Used as the INA238 register reference — see CLAUDE.md "Datasheet note".
REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_VSHUNT = 0x04
REG_VBUS = 0x05
REG_DIETEMP = 0x06
REG_CURRENT = 0x07
REG_POWER = 0x08
REG_MANUFACTURER_ID = 0x3E

MANUFACTURER_ID_TI = 0x5449  # "TI" in ASCII — Datasheet §7.6.1.16 Table 7-20 p.27

# ADC_CONFIG reset value (Datasheet §7.6.1.2 Table 7-6 p.21-22): MODE=Fh
# (continuous VBUS+VSHUNT+temp), VBUSCT=VSHCT=VTCT=5h (1052us each), AVG=0h (1x).
# Decoded bit-by-bit and confirmed against the table's per-field reset values.
# We rely on this POR default rather than rewriting it — see reset() in __init__.


def to_signed(value, bits):
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


class INA238:
    def __init__(self, i2c, addr=0x40, shunt_ohms=0.015, max_current_a=5.0, adc_range=0):
        self.i2c = i2c
        self.addr = addr
        self.adc_range = adc_range

        # Soft reset — Datasheet §7.6.1.1 Table 7-5 p.21, CONFIG bit15 (RST,
        # self-clearing): resets all registers to POR defaults, so the writes
        # below don't depend on whatever state the device was left in by a
        # previous run (Pico stays powered across script re-uploads).
        # Sequenced as its own transaction, then ADCRANGE separately below —
        # cross-checked against adafruit_ina228.py's INA2XX.reset(), which
        # does the same (RST first, other CONFIG bits after). No post-RST
        # delay is specified in the datasheet or present in Adafruit's driver.
        self.write_reg16(REG_CONFIG, 0x8000)

        # ADCRANGE — Datasheet §7.6.1.1 Table 7-5 p.21, CONFIG bit4.
        # All other CONFIG bits are already 0 post-reset, so a plain write
        # (not read-modify-write) is sufficient here.
        self.write_reg16(REG_CONFIG, (adc_range & 1) << 4)

        # ADC_CONFIG left untouched at its POR default (see module comment
        # above): continuous 3-channel conversion, which is what we want.

        # Shunt calibration — Datasheet §8.1.2 Eq. 1-2 p.28, verified against
        # the worked example in §8.2.2.5 Table 8-4 p.36 (10A/16.2mOhm example
        # reproduces SHUNT_CAL=4050d exactly).
        self.current_lsb = max_current_a / (1 << 15)
        shunt_cal = round(819.2e6 * self.current_lsb * shunt_ohms)
        if adc_range:
            shunt_cal *= 4
        # SHUNT_CAL is a 15-bit field (bits 14-0, bit15 reserved/RO) —
        # Datasheet §7.6.1.3 Table 7-7 p.22. Unlike Adafruit's driver, which
        # silently clamps to 0xFFFF (exceeding the real field width), fail
        # loudly if the shunt_ohms/max_current_a combination doesn't fit.
        if not 0 <= shunt_cal <= 0x7FFF:
            raise ValueError("shunt_cal %d out of range for SHUNT_CAL's 15-bit field" % shunt_cal)
        self.write_reg16(REG_SHUNT_CAL, shunt_cal)

        # WHO_AM_I sanity check — Datasheet §7.6.1.16 Table 7-20 p.27.
        # Fail loudly rather than proceeding with a possibly-wrong device or
        # dead bus (references/error-handling.md: "fail loudly on WHO_AM_I
        # mismatch"). Note: the real INA238 may also expose a DEVICE_ID
        # register at 0x3F (present in Adafruit's INA228-derived base class,
        # checked against 0x237/0x238), but that register isn't documented in
        # this INA237 datasheet, so MANUFACTURER_ID is used here instead.
        manufacturer_id = self.manufacturer_id()
        if manufacturer_id != MANUFACTURER_ID_TI:
            raise RuntimeError(
                "MANUFACTURER_ID mismatch: got 0x%04X, expected 0x%04X (0x40 wiring/address wrong?)"
                % (manufacturer_id, MANUFACTURER_ID_TI)
            )

    def read_reg(self, reg, nbytes=2):
        return self.i2c.readfrom_mem(self.addr, reg, nbytes)

    def write_reg16(self, reg, value):
        self.i2c.writeto_mem(self.addr, reg, value.to_bytes(2, "big"))

    def shunt_voltage_v(self):
        # VSHUNT — Datasheet §7.6.1.4 Table 7-8 p.23. Two's complement.
        raw = to_signed(int.from_bytes(self.read_reg(REG_VSHUNT), "big"), 16)
        lsb = 1.25e-6 if self.adc_range else 5e-6
        return raw * lsb

    def bus_voltage_v(self):
        # VBUS — Datasheet §7.6.1.5 Table 7-9 p.23. Always positive in
        # practice, so no sign extension needed.
        raw = int.from_bytes(self.read_reg(REG_VBUS), "big")
        return raw * 3.125e-3

    def temperature_c(self):
        # DIETEMP — Datasheet §7.6.1.6 Table 7-10 p.23. Value is bits[15:4],
        # two's complement 12-bit, 125m*C/LSB.
        raw = int.from_bytes(self.read_reg(REG_DIETEMP), "big") >> 4
        raw = to_signed(raw, 12)
        return raw * 0.125

    def current_a(self):
        # CURRENT — Datasheet §7.6.1.7 Table 7-11 p.23, §8.1.2 Eq. 3.
        raw = to_signed(int.from_bytes(self.read_reg(REG_CURRENT), "big"), 16)
        return raw * self.current_lsb

    def power_w(self):
        # POWER — Datasheet §7.6.1.8 Table 7-12 p.23 (24-bit, unsigned),
        # §8.1.2 Eq. 4: Power[W] = 0.2 * CURRENT_LSB * POWER.
        # DISAGREEMENT: Adafruit's adafruit_ina23x.py reads POWER as a 16-bit
        # value (`ROUnaryStruct(0x08, ">H")` — only the top 2 of the
        # register's 3 bytes) and computes power = raw * 20.0 * current_lsb.
        # Both the register width and the 20.0 scale factor conflict with the
        # datasheet's Eq. 4 and its own worked example (§8.2.2.5 Table 8-4
        # p.36: POWER=4718604d, current_lsb=305.176uA/LSB -> 0.2x gives
        # exactly the table's stated 288.0W; Adafruit's 20.0x formula would
        # be 100x too large even before accounting for the truncated read).
        # We follow the datasheet here, not Adafruit.
        raw = int.from_bytes(self.read_reg(REG_POWER, 3), "big")
        return 0.2 * self.current_lsb * raw

    def manufacturer_id(self):
        return int.from_bytes(self.read_reg(REG_MANUFACTURER_ID), "big")
