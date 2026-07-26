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
REG_DIAG_ALRT = 0x0B
REG_MANUFACTURER_ID = 0x3E

MANUFACTURER_ID_TI = 0x5449  # "TI" in ASCII — Datasheet §7.6.1.16 Table 7-20 p.27

# DIAG_ALRT flag bits — Datasheet §7.6.1.9 Table 7-13 p.24-26.
DIAG_MATHOF = 1 << 9  # arithmetic overflow: "current and power data may be invalid"
DIAG_CNVRF = 1 << 1  # conversion complete
DIAG_MEMSTAT = 1 << 0  # reset 1h; reads 0 on a trim-memory checksum error

# Shunt-voltage LSB and full-scale range, indexed by CONFIG.ADCRANGE.
# LSB from Datasheet §7.6.1.4 Table 7-8 p.23; full scale is LSB x 2^15, which
# matches the +/-163.84mV and +/-40.96mV ranges quoted in §8.2.2.1 p.33.
# Indexed (not truth-tested) so adc_range's validated 0/1 domain is structural.
VSHUNT_LSB_V = (5e-6, 1.25e-6)
VSHUNT_FULL_SCALE_V = (0.16384, 0.04096)

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
        # Validate arguments and compute everything BEFORE touching the bus, so
        # a bad argument can't leave the device half-configured.
        #
        # adc_range must be exactly 0 or 1: it selects both the CONFIG.ADCRANGE
        # bit and the VSHUNT LSB / full-scale entries below, and those must not
        # be able to disagree. (A masked write plus a truthiness test elsewhere
        # would let adc_range=2 configure the wide range while scaling readings
        # for the narrow one — 4x wrong, silently.)
        if adc_range not in (0, 1):
            raise ValueError("adc_range must be 0 or 1, got %r" % (adc_range,))
        if shunt_ohms <= 0:
            raise ValueError("shunt_ohms must be positive, got %r" % (shunt_ohms,))
        if max_current_a <= 0:
            raise ValueError("max_current_a must be positive, got %r" % (max_current_a,))

        # Requested range must actually fit the ADC's full-scale shunt voltage —
        # Datasheet §8.2.2.1 Eq. 5 p.33 (RSHUNT < VSENSE_MAX / IMAX). The
        # SHUNT_CAL field check below only catches register overflow, not this:
        # e.g. 5A x 15mOhm = 75mV fits the field fine but saturates
        # ADCRANGE=1's +/-40.96mV at 2.73A, silently clipping every reading.
        full_scale_v = VSHUNT_FULL_SCALE_V[adc_range]
        if max_current_a * shunt_ohms > full_scale_v:
            raise ValueError(
                "max_current_a %gA x shunt_ohms %gohm = %gmV exceeds ADCRANGE=%d "
                "full scale %gmV (max measurable %gA)"
                % (max_current_a, shunt_ohms, max_current_a * shunt_ohms * 1000,
                   adc_range, full_scale_v * 1000, full_scale_v / shunt_ohms)
            )

        # Shunt calibration — Datasheet §8.1.2 Eq. 1-2 p.28, verified against
        # the worked example in §8.2.2.5 Table 8-4 p.36 (10A/16.2mOhm example
        # reproduces SHUNT_CAL=4050d exactly).
        current_lsb = max_current_a / (1 << 15)
        shunt_cal = round(819.2e6 * current_lsb * shunt_ohms)
        if adc_range:
            shunt_cal *= 4
        # SHUNT_CAL is a 15-bit field (bits 14-0, bit15 reserved/RO) —
        # Datasheet §7.6.1.3 Table 7-7 p.22. Unlike Adafruit's driver, which
        # silently clamps to 0xFFFF (exceeding the real field width), fail
        # loudly if the shunt_ohms/max_current_a combination doesn't fit.
        # The lower bound is 1, not 0: §8.1.2 p.29 states that a SHUNT_CAL of
        # zero makes the CURRENT register report zero (and §8.2.2.3 p.33 says
        # the same for power), so 0 would read a flat 0.0A forever rather than
        # erroring. Reachable by rounding, and verified on hardware:
        # shunt_ohms=1e-6 (-> 0.125) and max_current_a=0.001 (-> 0.375) both
        # round to 0 and now raise. shunt_ohms=1e-5 gives 1.25 -> 1, which is
        # legitimately accepted.
        if not 1 <= shunt_cal <= 0x7FFF:
            raise ValueError("shunt_cal %d out of range for SHUNT_CAL's 15-bit field" % shunt_cal)

        self.i2c = i2c
        self.addr = addr
        self.adc_range = adc_range
        self.current_lsb = current_lsb

        # WHO_AM_I check FIRST, before any write — Datasheet §7.6.1.16 Table
        # 7-20 p.27. Ordering matters: this bus is shared (an ADC and an RTC
        # sit alongside the INA238), so configuring before identifying would
        # let a wrong addr blind-write RST=0x8000 and SHUNT_CAL into an
        # unrelated device's registers and only then "fail loudly" — after the
        # damage. Read-only ID registers are valid pre-reset, so there is no
        # reason to write first. (references/error-handling.md: "fail loudly on
        # WHO_AM_I mismatch".) Note: the real INA238 may also expose a DEVICE_ID
        # register at 0x3F (present in Adafruit's INA228-derived base class,
        # checked against 0x237/0x238), but that register isn't documented in
        # this INA237 datasheet, so MANUFACTURER_ID is used here instead.
        manufacturer_id = self.manufacturer_id()
        if manufacturer_id != MANUFACTURER_ID_TI:
            raise RuntimeError(
                "MANUFACTURER_ID mismatch at addr 0x%02X: got 0x%04X, expected 0x%04X "
                "(wiring/address wrong?)" % (addr, manufacturer_id, MANUFACTURER_ID_TI)
            )

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
        self.write_reg16(REG_CONFIG, adc_range << 4)

        # ADC_CONFIG left untouched at its POR default (see module comment
        # above): continuous 3-channel conversion, which is what we want.

        self.write_reg16(REG_SHUNT_CAL, shunt_cal)

        # Trim-memory health — DIAG_ALRT.MEMSTAT, Datasheet §7.6.1.9 Table 7-13
        # p.26 (reset 1h; reads 0 on a checksum error in the device's trim
        # memory). MANUFACTURER_ID can read correctly on a device whose
        # factory trim is corrupt, so this covers a failure mode the WHO_AM_I
        # check cannot.
        if not self.memory_ok():
            raise RuntimeError(
                "DIAG_ALRT.MEMSTAT reports a trim-memory checksum error "
                "(DIAG_ALRT=0x%04X) — readings from this device are untrustworthy"
                % self.diag_alrt()
            )

    def read_reg(self, reg, nbytes=2):
        return self.i2c.readfrom_mem(self.addr, reg, nbytes)

    def write_reg16(self, reg, value):
        self.i2c.writeto_mem(self.addr, reg, value.to_bytes(2, "big"))

    def shunt_voltage_v(self):
        # VSHUNT — Datasheet §7.6.1.4 Table 7-8 p.23. Two's complement.
        raw = to_signed(int.from_bytes(self.read_reg(REG_VSHUNT), "big"), 16)
        return raw * VSHUNT_LSB_V[self.adc_range]

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
        #
        # Adafruit's NET error is 2.56x LOW, not 100x high. Verified against the
        # source 2026-07-26 (adafruit_ina23x.py, github.com/adafruit/
        # Adafruit_CircuitPython_INA23x): line 40 `_raw_power =
        # ROUnaryStruct(0x08, ">H")` and lines 82-84 `return self._raw_power *
        # 20.0 * self._current_lsb`.
        # Reads are MSB-first (verified on hardware: a 1-byte read of DIETEMP
        # 0x0D30 returns 0x0D), so `>H` on this 24-bit register takes
        # bits[23:8] == raw>>8, NOT the low 16 bits -- and that holds at any
        # magnitude. So 20.0 * (raw>>8) against the datasheet's 0.2 * raw is a
        # ratio of 100/256 = 0.3906, i.e. 2.56x low.
        #
        # Confirmed on hardware 2026-07-26, and this settles BOTH halves of the
        # disagreement at once: raw POWER 29301 -> 0.2x gives 0.8942W, matching
        # VBUS x CURRENT (12.2375V x 73.09mA = 0.8944W) to 0.03%. Since a
        # 2-byte read would yield raw>>8 = 114 -> 0.0035W under 0.2x or 0.3479W
        # under Adafruit's 20.0x, neither of which is 0.894W, the 3-byte read
        # width is confirmed too. No higher-current test is needed.
        raw = int.from_bytes(self.read_reg(REG_POWER, 3), "big")
        return 0.2 * self.current_lsb * raw

    def manufacturer_id(self):
        return int.from_bytes(self.read_reg(REG_MANUFACTURER_ID), "big")

    def diag_alrt(self):
        # DIAG_ALRT — Datasheet §7.6.1.9 Table 7-13 p.24-26. Raw flag word;
        # use the DIAG_* masks. ALATCH is left at its POR default 0h
        # (Transparent), so flags track live state rather than latching until
        # read — meaning a transient MATHOF can clear itself before you look.
        return int.from_bytes(self.read_reg(REG_DIAG_ALRT), "big")

    def math_overflow(self):
        # MATHOF — Datasheet §7.6.1.9 Table 7-13 p.25: "set to 1 if an
        # arithmetic operation resulted in an overflow error. It indicates that
        # current and power data may be invalid." Nothing else surfaces this:
        # current_a()/power_w() return overflowed values indistinguishable from
        # good ones, so check this when a reading looks impossible.
        return bool(self.diag_alrt() & DIAG_MATHOF)

    def conversion_ready(self):
        # CNVRF — Datasheet §7.6.1.9 Table 7-13 p.25. Set when a conversion
        # completes. Of limited use for pacing in continuous mode with the
        # default ALATCH=0: conversions land every cycle, so this reads 1 almost
        # always. Latched mode (ALATCH=1) would make it a true "new since last
        # read" edge, since reading DIAG_ALRT clears it.
        return bool(self.diag_alrt() & DIAG_CNVRF)

    def memory_ok(self):
        # MEMSTAT — Datasheet §7.6.1.9 Table 7-13 p.26. Reset 1h; reads 0 if a
        # checksum error is detected in the device's trim memory. Inverted
        # sense versus the other flags: 1 is healthy.
        return bool(self.diag_alrt() & DIAG_MEMSTAT)
