#!/usr/bin/env python3
import sys
import os
import time
import json
import argparse
import threading
import math
from collections import deque

# Import serial and helper tools
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: 'pyserial' is not installed. Please run 'uv sync' or 'pip install pyserial'")
    sys.exit(1)

# Ensure we can import from server.py in the same directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from server import sensor_apply_data, send_command
except ImportError:
    print("Error: Could not import 'sensor_apply_data' from 'server.py'. Make sure 'server.py' is in the same directory.")
    sys.exit(1)

# ANSI colors for premium terminal UI
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"

def list_serial_ports():
    ports = list(serial.tools.list_ports.comports())
    return [port.device for port in ports]

def parse_csv_line(line):
    # Expected format: ax,ay,az,gx,gy,gz,mx,my,mz,r,g,b,amb,press
    parts = line.strip().split(',')
    if len(parts) < 6:
        return None
    try:
        accel = [float(parts[0]), float(parts[1]), float(parts[2])]
        gyro = [float(parts[3]), float(parts[4]), float(parts[5])]
        
        # Magnetometer (optional, elements 6, 7, 8)
        mag = None
        if len(parts) >= 9:
            mag = [float(parts[6]), float(parts[7]), float(parts[8])]
        
        # Optional extra sensors
        extra = {}
        if len(parts) >= 12:
            extra['rgb'] = [int(parts[9]), int(parts[10]), int(parts[11])]
        if len(parts) >= 13:
            extra['ambient'] = float(parts[12])
        if len(parts) >= 14:
            extra['pressure'] = float(parts[13])
            
        return {
            "accel": accel,
            "gyro": gyro,
            "mag": mag,
            "extra": extra
        }
    except ValueError:
        return None

def parse_json_line(line):
    try:
        data = json.loads(line.strip())
        accel = [data.get('ax', 0.0), data.get('ay', 0.0), data.get('az', 0.0)]
        gyro = [data.get('gx', 0.0), data.get('gy', 0.0), data.get('gz', 0.0)]
        mag = None
        if 'mx' in data or 'my' in data or 'mz' in data:
            mag = [data.get('mx', 0.0), data.get('my', 0.0), data.get('mz', 0.0)]
            
        extra = {}
        if 'r' in data or 'g' in data or 'b' in data:
            extra['rgb'] = [data.get('r', 0), data.get('g', 0), data.get('b', 0)]
        if 'amb' in data:
            extra['ambient'] = data.get('amb', 0.0)
        if 'press' in data:
            extra['pressure'] = data.get('press', 1013.25)
            
        return {
            "accel": accel,
            "gyro": gyro,
            "mag": mag,
            "extra": extra
        }
    except json.JSONDecodeError:
        return None

# Global queue for latency buffering
data_queue = deque()
running = True

def send_to_blender(packet, args, dt):
    # Apply sensor data
    result = sensor_apply_data(
        sensor_name=args.sensor_name,
        accel=packet['accel'],
        gyro=packet['gyro'],
        mag=packet['mag'],
        mode=args.mode,
        alpha=args.alpha,
        dt=dt,
        frame=None,  # Let Blender use current frame
        insert_keyframe=args.record,
        subtract_gravity=not args.no_gravity,
        position_scale=args.position_scale
    )
    
    # Process extra sensors if available
    extra = packet.get('extra', {})
    if 'rgb' in extra and args.sync_rgb:
        rgb = extra['rgb']
        send_command("sensor_set_color_from_rgb", {
            "object_name": args.rgb_target or args.sensor_name,
            "r": rgb[0], "g": rgb[1], "b": rgb[2]
        })
    if 'ambient' in extra and args.sync_light:
        send_command("sensor_set_light_from_ambient", {
            "light_name": args.light_target,
            "ambient_value": extra['ambient']
        })
    if 'pressure' in extra and args.sync_altitude:
        send_command("sensor_set_altitude", {
            "sensor_name": args.sensor_name,
            "pressure_hpa": extra['pressure']
        })
        
    return result

def queue_worker(args):
    global running
    last_send_time = time.time()
    packet_count = 0
    hz = 0
    hz_timer = time.time()
    
    print(f"{GREEN}[Bridge] Latency queue worker started. Latency delay: {args.latency}ms{RESET}")
    
    while running:
        # If queue is backing up and we are in low-latency mode (latency=0), drop old frames
        if len(data_queue) > 3 and args.latency == 0:
            try:
                last_item = data_queue.pop()
                data_queue.clear()
                data_queue.append(last_item)
            except IndexError:
                pass

        if not data_queue:
            time.sleep(0.001)
            continue
            
        now = time.time()
        # Peek at the first element
        scheduled_time, packet = data_queue[0]
        
        if now >= scheduled_time:
            # Time to send!
            data_queue.popleft()
            
            # Calculate actual dt
            dt = now - last_send_time
            if dt <= 0:
                dt = 0.001
            last_send_time = now
            
            try:
                res = send_to_blender(packet, args, dt)
                if "error" in res:
                    print(f"\r{RED}[Blender Error] {res['error']}{RESET}", end="")
                else:
                    packet_count += 1
                    # Show premium status in terminal
                    if time.time() - hz_timer >= 1.0:
                        hz = packet_count
                        packet_count = 0
                        hz_timer = time.time()
                    
                    loc = res.get('location', [0,0,0])
                    rot = res.get('rotation', [0,0,0])
                    rot_deg = [math.degrees(r) for r in rot]
                    
                    # Print beautiful status line
                    print(
                        f"\r{BLUE}[Active]{RESET} {BOLD}{args.sensor_name}{RESET} | "
                        f"Rate: {GREEN}{hz} Hz{RESET} | "
                        f"Pos: [{loc[0]:.2f}, {loc[1]:.2f}, {loc[2]:.2f}] | "
                        f"Rot: [{rot_deg[0]:.1f}°, {rot_deg[1]:.1f}°, {rot_deg[2]:.1f}°] | "
                        f"Queue size: {YELLOW}{len(data_queue)}{RESET}      ", 
                        end="", flush=True
                    )
            except Exception as e:
                print(f"\r{RED}[Worker Exception] {str(e)}{RESET}", end="")
                
        else:
            # Sleep a tiny bit to prevent CPU spinning
            time.sleep(0.001)

def main():
    global running
    parser = argparse.ArgumentParser(description="Arduino Serial-to-Blender Real-time Sensor Bridge")
    parser.add_argument("--port", type=str, help="Serial port (e.g. /dev/cu.usbmodem14101 or COM3)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--sensor-name", type=str, default="Sensor_IMU", help="Name of Null object in Blender")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["raw", "double-integrate", "mix", "integrate", "fusion"],
        default="mix",
        help="Processing mode: raw, double-integrate, or mix (default: mix)",
    )
    parser.add_argument("--alpha", type=float, default=0.98, help="Complementary filter alpha (default: 0.98)")
    parser.add_argument("--latency", type=int, default=0, help="Latency compensation delay in milliseconds (default: 0)")
    parser.add_argument("--position-scale", type=float, default=1.0, help="Position multiplier (default: 1.0)")
    parser.add_argument("--no-gravity", action="store_true", help="Do not subtract gravity from accelerometer")
    parser.add_argument("--record", action="store_true", help="Record keyframes in Blender in real-time")
    parser.add_argument("--format", type=str, choices=["csv", "json"], default="csv", help="Serial data format (default: csv)")
    
    # Extra sensor options
    parser.add_argument("--sync-rgb", action="store_true", help="Sync APDS-9960 RGB data to material color")
    parser.add_argument("--rgb-target", type=str, help="Object name to apply RGB color to (defaults to sensor-name)")
    parser.add_argument("--sync-light", action="store_true", help="Sync APDS-9960 Ambient light to Blender light source")
    parser.add_argument("--light-target", type=str, default="Sensor_Light", help="Name of light source in Blender")
    parser.add_argument("--sync-altitude", action="store_true", help="Sync LPS22HB barometric pressure to Z height")
    
    args = parser.parse_args()

    # Automatically scan ports if not specified
    if not args.port:
        ports = list_serial_ports()
        if not ports:
            print(f"{RED}[Error] No serial ports found. Connect your Arduino Nano and try again.{RESET}")
            sys.exit(1)
        elif len(ports) == 1:
            args.port = ports[0]
            print(f"{GREEN}[Info] Auto-selected serial port: {args.port}{RESET}")
        else:
            print(f"\n{YELLOW}Multiple serial ports found:{RESET}")
            for idx, p in enumerate(ports):
                print(f"  {idx + 1}: {p}")
            try:
                choice = int(input("\nSelect port number: ")) - 1
                if 0 <= choice < len(ports):
                    args.port = ports[choice]
                else:
                    print(f"{RED}Invalid selection.{RESET}")
                    sys.exit(1)
            except (ValueError, KeyboardInterrupt):
                print(f"\n{RED}Aborted.{RESET}")
                sys.exit(1)

    print(f"\n{BLUE}{BOLD}===================================================={RESET}")
    print(f"{BLUE}{BOLD}   Blender Real-time Sensor Bridge - Arduino Nano   {RESET}")
    print(f"{BLUE}{BOLD}===================================================={RESET}")
    print(f" Port:             {GREEN}{args.port}{RESET}")
    print(f" Baudrate:         {GREEN}{args.baud}{RESET}")
    print(f" Target Object:    {GREEN}{args.sensor_name}{RESET}")
    print(f" Processing Mode:  {GREEN}{args.mode.upper()}{RESET}")
    print(f" Latency Delay:    {GREEN}{args.latency} ms{RESET}")
    print(f" Record Keyframes: {GREEN}{args.record}{RESET}")
    print(f" Formatting:       {GREEN}{args.format.upper()}{RESET}")
    print(f"----------------------------------------------------")

    # Start queue worker thread
    worker_thread = threading.Thread(target=queue_worker, args=(args,), daemon=True)
    worker_thread.start()

    # Initialize serial port
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1.0)
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:
            pass
        # Flush buffers
        ser.reset_input_buffer()
        time.sleep(0.5)  # Wait for serial reboot (some boards auto-reset on connect)
    except Exception as e:
        print(f"{RED}[Error] Failed to open serial port {args.port}: {str(e)}{RESET}")
        running = False
        sys.exit(1)

    print(f"{GREEN}[Bridge] Connected to serial. Streaming data... Press Ctrl+C to stop.{RESET}\n")

    try:
        while True:
            try:
                line_bytes = ser.readline()
                if not line_bytes:
                    continue
                line = line_bytes.decode('utf-8', errors='ignore').strip()
            except Exception:
                continue

            # Parse line
            packet = None
            if args.format == "csv":
                packet = parse_csv_line(line)
            else:
                packet = parse_json_line(line)

            if not packet:
                # Debug print for non-matching lines (e.g. Arduino logs or startup prints)
                if not line.startswith('{') and ',' not in line:
                    print(f"\n[Arduino Log] {line}")
                continue

            # Push packet into latency buffer queue
            scheduled_time = time.time() + (args.latency / 1000.0)
            data_queue.append((scheduled_time, packet))

    except KeyboardInterrupt:
        print(f"\n{YELLOW}[Bridge] Stopping...{RESET}")
    finally:
        running = False
        ser.close()
        print(f"{GREEN}[Bridge] Serial port closed. Goodbye!{RESET}")

if __name__ == "__main__":
    main()
