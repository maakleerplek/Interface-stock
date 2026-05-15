"""
Waveshare 2.4inch LCD Module driver (ILI9341, 240x320, SPI).
Interface: Init(), clear(), ShowImage(PIL.Image)
"""
import time
import RPi.GPIO as GPIO
import spidev
from PIL import Image

WIDTH  = 240
HEIGHT = 320


class LCD_2inch4:
    def __init__(self):
        self.width  = WIDTH
        self.height = HEIGHT
        self._spi   = None

        self.RST_PIN = 27
        self.DC_PIN  = 25
        self.BL_PIN  = 18

    def _module_init(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.RST_PIN, GPIO.OUT)
        GPIO.setup(self.DC_PIN,  GPIO.OUT)
        GPIO.setup(self.BL_PIN,  GPIO.OUT)
        GPIO.output(self.BL_PIN, GPIO.HIGH)
        self._spi = spidev.SpiDev()
        self._spi.open(0, 0)
        self._spi.max_speed_hz = 40000000
        self._spi.mode = 0b00

    def _cmd(self, cmd):
        GPIO.output(self.DC_PIN, GPIO.LOW)
        self._spi.writebytes([cmd])

    def _data(self, val):
        GPIO.output(self.DC_PIN, GPIO.HIGH)
        if isinstance(val, int):
            self._spi.writebytes([val])
        else:
            for i in range(0, len(val), 4096):
                self._spi.writebytes(val[i:i + 4096])

    def _reset(self):
        GPIO.output(self.RST_PIN, GPIO.HIGH); time.sleep(0.01)
        GPIO.output(self.RST_PIN, GPIO.LOW);  time.sleep(0.01)
        GPIO.output(self.RST_PIN, GPIO.HIGH); time.sleep(0.01)

    def Init(self):
        self._module_init()
        self._reset()

        self._cmd(0x11)  # Sleep out

        # ILI9341 init sequence (matches official Waveshare driver)
        self._cmd(0xCF); self._data([0x00, 0xC1, 0x30])
        self._cmd(0xED); self._data([0x64, 0x03, 0x12, 0x81])
        self._cmd(0xE8); self._data([0x85, 0x00, 0x79])
        self._cmd(0xCB); self._data([0x39, 0x2C, 0x00, 0x34, 0x02])
        self._cmd(0xF7); self._data([0x20])
        self._cmd(0xEA); self._data([0x00, 0x00])
        self._cmd(0xC0); self._data([0x1D])   # Power control
        self._cmd(0xC1); self._data([0x12])   # Power control
        self._cmd(0xC5); self._data([0x33, 0x3F])  # VCM control
        self._cmd(0xC7); self._data([0x92])   # VCM control
        self._cmd(0x3A); self._data([0x55])   # 16-bit color
        self._cmd(0x36); self._data([0x08])   # MADCTL: BGR
        self._cmd(0xB1); self._data([0x00, 0x12])
        self._cmd(0xB6); self._data([0x0A, 0xA2])
        self._cmd(0x44); self._data([0x02])
        self._cmd(0xF2); self._data([0x00])
        self._cmd(0x26); self._data([0x01])
        self._cmd(0xE0); self._data([0x0F,0x22,0x1C,0x1B,0x08,0x0F,0x48,0xB8,0x34,0x05,0x0C,0x09,0x0F,0x07,0x00])
        self._cmd(0xE1); self._data([0x00,0x23,0x24,0x07,0x10,0x07,0x38,0x47,0x4B,0x0A,0x13,0x06,0x30,0x38,0x0F])
        self._cmd(0x29)  # Display on

    def _set_window(self, x0, y0, x1, y1):
        self._cmd(0x2A)
        self._data([x0 >> 8, x0 & 0xFF, x1 >> 8, (x1 - 1) & 0xFF])
        self._cmd(0x2B)
        self._data([y0 >> 8, y0 & 0xFF, y1 >> 8, (y1 - 1) & 0xFF])
        self._cmd(0x2C)

    def clear(self):
        buf = [0xFF] * (WIDTH * HEIGHT * 2)
        time.sleep(0.02)
        self._set_window(0, 0, WIDTH, HEIGHT)
        GPIO.output(self.DC_PIN, GPIO.HIGH)
        for i in range(0, len(buf), 4096):
            self._spi.writebytes(buf[i:i + 4096])

    def ShowImage(self, image):
        imwidth, imheight = image.size
        img = image.convert('RGB')

        # Match official driver: landscape image → MADCTL 0x78, portrait → 0x08
        if imwidth == HEIGHT and imheight == WIDTH:
            self._cmd(0x36); self._data([0x78])
        else:
            self._cmd(0x36); self._data([0x08])

        self._set_window(0, 0, WIDTH, HEIGHT)
        GPIO.output(self.DC_PIN, GPIO.HIGH)

        pixels = list(img.getdata())
        buf = []
        for r, g, b in pixels:
            c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            buf.append((c >> 8) & 0xFF)
            buf.append(c & 0xFF)
        for i in range(0, len(buf), 4096):
            self._spi.writebytes(buf[i:i + 4096])


LCD_2in4 = LCD_2inch4
