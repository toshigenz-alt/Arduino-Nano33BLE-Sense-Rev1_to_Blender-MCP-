#!/usr/bin/env python3
import argparse
import os
import socket
import sys
import threading
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import serial_bridge as bridge


def main():
    parser = argparse.ArgumentParser(description="UDP-to-Blender real-time sensor bridge")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="UDP bind host (default: 0.0.0.0)")
    parser.add_argument("--udp-port", type=int, default=5005, help="UDP port to listen on (default: 5005)")
    parser.add_argument("--sensor-name", type=str, default="Sensor_IMU", help="Name of Null object in Blender")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["raw", "double-integrate", "mix", "cane", "integrate", "fusion"],
        default="raw",
        help="Processing mode: raw, double-integrate, mix, or cane (default: raw)",
    )
    parser.add_argument("--alpha", type=float, default=0.98, help="Complementary filter alpha (default: 0.98)")
    parser.add_argument("--latency", type=int, default=0, help="Latency delay in milliseconds (default: 0)")
    parser.add_argument("--position-scale", type=float, default=1.0, help="Position multiplier (default: 1.0)")
    parser.add_argument("--no-gravity", action="store_true", help="Do not subtract gravity from accelerometer")
    parser.add_argument("--record", action="store_true", help="Record keyframes in Blender in real-time")
    parser.add_argument("--format", type=str, choices=["csv", "json"], default="csv", help="UDP data format")
    parser.add_argument("--print-packets", type=int, default=5, help="Print the first N valid UDP packets")
    parser.add_argument("--status-interval", type=float, default=1.0, help="Seconds between UDP receive status logs")

    parser.add_argument("--sync-rgb", action="store_true", help="Sync RGB data to material color")
    parser.add_argument("--rgb-target", type=str, help="Object name to apply RGB color to")
    parser.add_argument("--sync-light", action="store_true", help="Sync ambient light to Blender light source")
    parser.add_argument("--light-target", type=str, default="Sensor_Light", help="Name of light source in Blender")
    parser.add_argument("--sync-altitude", action="store_true", help="Sync barometric pressure to Z height")

    args = parser.parse_args()

    print("====================================================")
    print("   Blender Real-time Sensor Bridge - UDP / UNO Q")
    print("====================================================")
    print(f" Listen:           {args.host}:{args.udp_port}")
    print(f" Target Object:    {args.sensor_name}")
    print(f" Processing Mode:  {args.mode.upper()}")
    print(f" Latency Delay:    {args.latency} ms")
    print(f" Record Keyframes: {args.record}")
    print(f" Formatting:       {args.format.upper()}")
    print("----------------------------------------------------")
    print(" Tip: If Received stays at 0, the UNO Q is not reaching this Mac/port.")
    print("      If Received increases but Blender does not move, check Blender MCP Start Server.")
    print("----------------------------------------------------")

    worker_thread = threading.Thread(target=bridge.queue_worker, args=(args,), daemon=True)
    worker_thread.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((args.host, args.udp_port))
    except OSError as exc:
        bridge.running = False
        print(f"[Error] Failed to bind UDP {args.host}:{args.udp_port}: {exc}")
        sys.exit(1)

    print("[Bridge] Listening for UDP sensor packets... Press Ctrl+C to stop.\n")

    last_bad_line_log = 0.0
    last_status_log = time.time()
    received_count = 0
    printed_count = 0
    try:
        while True:
            data, addr = sock.recvfrom(4096)
            line = data.decode("utf-8", errors="ignore").strip()
            received_count += 1

            now = time.time()
            if now - last_status_log >= args.status_interval:
                last_status_log = now
                print(
                    f"[UDP] Received={received_count} "
                    f"from={addr[0]}:{addr[1]} queue={len(bridge.data_queue)}"
                )

            if args.format == "csv":
                packet = bridge.parse_csv_line(line)
            else:
                packet = bridge.parse_json_line(line)

            if not packet:
                now = time.time()
                if now - last_bad_line_log >= 1.0:
                    last_bad_line_log = now
                    print(f"[Bad UDP Line] from={addr[0]}:{addr[1]} raw={line[:180]}")
                continue

            if printed_count < args.print_packets:
                printed_count += 1
                print(f"[UDP Packet {printed_count}] from={addr[0]}:{addr[1]} raw={line[:180]}")

            scheduled_time = time.time() + (args.latency / 1000.0)
            bridge.data_queue.append((scheduled_time, packet))
    except KeyboardInterrupt:
        print("\n[Bridge] Stopping...")
    finally:
        bridge.running = False
        sock.close()
        print("[Bridge] UDP socket closed. Goodbye!")


if __name__ == "__main__":
    main()
