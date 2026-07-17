class INA238:
    def __init__(self, i2c, addr=0x40):
        self.i2c = i2c
        self.addr = addr
