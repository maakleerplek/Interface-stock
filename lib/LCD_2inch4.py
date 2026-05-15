"""
Waveshare 2.4inch LCD Module driver (ST7789, 240x320, SPI).
Interface: Init(), clear(), ShowImage(PIL.Image)
"""
import time
import RPi.GPIO as GPIO
from PIL import Image
from . import lcdconfig as config

WIDTH  = 240
HEIGHT = 320

# ST7789 commands
SWRESET = 0x01
SLPOUT  = 0x11
COLMOD  = 0x3A
MADCTL  = 0x36
CASET   = 0x2A
RASET   = 0x2B
RAMWR   = 0x2C
DISPON  = 0x29
INVON   = 0x21


class LCD_2inch4:
    def __init__(self):
        self.width  = WIDTH
        self.height = HEIGHT
        self.spi    = None

    def _cmd(self, cmd):
        GPIO.output(config.DC_PIN, GPIO.LOW)
        self.spi.writebytes([cmd])

    def _data(self, data):
        GPIO.output(config.DC_PIN, GPIO.HIGH)
        if isinstance(data, int):
            self.spi.writebytes([data])
        else:
            # Send in 4096-byte chunks to avoid SPI buffer limits
            for i in range(0, len(data), 4096):
                self.spi.writebytes(data[i:i + 4096])

    def _reset(self):
        GPIO.output(config.RST_PIN, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(config.RST_PIN, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(config.RST_PIN, GPIO.HIGH)
        time.sleep(0.01)

    def Init(self):
        self.spi = config.module_init()
        self._reset()
        self._cmd(SWRESET); time.sleep(0.15)
        self._cmd(SLPOUT);  time.sleep(0.5)
        self._cmd(COLMOD);  self._data(0x55)   # 16-bit color
        self._cmd(MADCTL);  self._data(0x00)
        self._cmd(INVON)
        self._cmd(DISPON)
        time.sleep(0.1)

    def _set_window(self, x0, y0, x1, y1):
        self._cmd(CASET)
        self._data([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self._cmd(RASET)
        self._data([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self._cmd(RAMWR)

    def clear(self, color=0x0000):
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        hi, lo = (color >> 8) & 0xFF, color & 0xFF
        self._data([hi, lo] * (WIDTH * HEIGHT))

    def ShowImage(self, image):
        img = image.convert('RGB').resize((WIDTH, HEIGHT))
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        pixels = list(img.getdata())
        buf = []
        for r, g, b in pixels:
            c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            buf.append((c >> 8) & 0xFF)
            buf.append(c & 0xFF)
        self._data(buf)


# Alias
LCD_2in4 = LCD_2inch4
