import spidev
import RPi.GPIO as GPIO

# Pin definitions (BCM numbering)
RST_PIN  = 27
DC_PIN   = 25
BL_PIN   = 18
CS_PIN   = 8

def module_init():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(RST_PIN, GPIO.OUT)
    GPIO.setup(DC_PIN,  GPIO.OUT)
    GPIO.setup(BL_PIN,  GPIO.OUT)
    GPIO.output(BL_PIN, GPIO.HIGH)

    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 40000000
    spi.mode = 0b00
    return spi

def module_exit(spi):
    spi.close()
    GPIO.output(BL_PIN, GPIO.LOW)
    GPIO.cleanup()
