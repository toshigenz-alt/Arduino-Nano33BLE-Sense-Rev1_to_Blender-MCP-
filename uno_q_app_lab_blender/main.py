"""
UNO Q App Lab -> Mac UDP forwarder.

Put this file in the Python tab of Arduino App Lab.
Set BLENDER_HOST to your Mac IP address, then run the app.
On the Mac, run:

    uv run udp_bridge.py --udp-port 5005 --mode raw

The UDP packet uses the same DATA CSV format as arduino_sensor_sender.ino.
"""

import os
import socket
import time

from arduino.app_utils import App, Bridge


BLENDER_HOST = os.getenv("BLENDER_HOST", "192.168.1.100")
BLENDER_UDP_PORT = int(os.getenv("BLENDER_UDP_PORT", "5005"))
PRINT_EVERY_SECONDS = 1.0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
last_print_time = 0.0
packet_count = 0


def make_data_line(ax, ay, az, gx, gy, gz):
    # Modulino Movement has accel + gyro only, so magnetometer/RGB/light/pressure
    # are sent as stable placeholders for compatibility with serial_bridge.py.
    return (
        f"DATA,{float(ax):.4f},{float(ay):.4f},{float(az):.4f},"
        f"{float(gx):.4f},{float(gy):.4f},{float(gz):.4f},"
        "0.00,0.00,0.00,128,128,128,500,1013.25"
    )


def on_motion_reading(ax: float, ay: float, az: float, gx: float, gy: float, gz: float):
    global last_print_time, packet_count

    line = make_data_line(ax, ay, az, gx, gy, gz)
    sock.sendto(line.encode("utf-8"), (BLENDER_HOST, BLENDER_UDP_PORT))
    packet_count += 1

    now = time.monotonic()
    if now - last_print_time >= PRINT_EVERY_SECONDS:
        last_print_time = now
        print(
            f"[UNO Q UDP] sent {packet_count} packets -> "
            f"{BLENDER_HOST}:{BLENDER_UDP_PORT} | {line}"
        )
        packet_count = 0


Bridge.provide("motion_reading", on_motion_reading)

print(f"[UNO Q UDP] forwarding to {BLENDER_HOST}:{BLENDER_UDP_PORT}")
App.run()
