import serial
import time

s = serial.Serial('COM5', 115200, timeout=0.1)
s.dtr = False
s.rts = True
time.sleep(0.1)
s.rts = False
time.sleep(0.1)

t0 = time.time()
while time.time() - t0 < 8:
    data = s.read(1024)
    if data:
        print(data.decode('utf-8', errors='replace'), end='', flush=True)

s.close()
