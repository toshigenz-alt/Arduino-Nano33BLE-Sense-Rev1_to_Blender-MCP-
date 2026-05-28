#!/usr/bin/env python3
"""Subscribe to MQTT IMU JSON and drive the Blender sensor socket."""

import argparse
import json
import math
import queue
import sys
import time
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt is not installed. Run `uv sync` or `pip install paho-mqtt`.")
    sys.exit(1)

from env_utils import env_bool, env_float, env_int, env_str, load_dotenv
import server
from server import sensor_apply_data, send_command


BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def mqtt_client(on_connect, on_message) -> mqtt.Client:
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    else:
        client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    return client


def vector_from_payload(data: dict[str, Any], vector_key: str, keys: list[str]) -> list[float] | None:
    value = data.get(vector_key)
    if isinstance(value, list) and len(value) >= 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    if all(key in data for key in keys):
        return [float(data[keys[0]]), float(data[keys[1]]), float(data[keys[2]])]
    fields = data.get("fields")
    if isinstance(fields, dict) and all(key in fields for key in keys):
        return [float(fields[keys[0]]), float(fields[keys[1]]), float(fields[keys[2]])]
    return None


def packet_from_payload(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    accel = vector_from_payload(data, "accel", ["ax", "ay", "az"])
    gyro = vector_from_payload(data, "gyro", ["gx", "gy", "gz"])
    if accel is None or gyro is None:
        return None

    mag = vector_from_payload(data, "mag", ["mx", "my", "mz"])
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    if "rgb" not in extra and all(key in data for key in ["r", "g", "b"]):
        extra["rgb"] = [int(data["r"]), int(data["g"]), int(data["b"])]
    if "ambient" not in extra and "amb" in data:
        extra["ambient"] = float(data["amb"])
    if "pressure" not in extra and "press" in data:
        extra["pressure"] = float(data["press"])

    return {
        "sensor_name": data.get("sensor_name"),
        "timestamp": data.get("timestamp"),
        "accel": accel,
        "gyro": gyro,
        "mag": mag,
        "extra": extra,
    }


def send_to_blender(packet: dict[str, Any], args: argparse.Namespace, dt: float) -> dict[str, Any]:
    result = sensor_apply_data(
        sensor_name=packet.get("sensor_name") or args.sensor_name,
        accel=packet["accel"],
        gyro=packet["gyro"],
        mag=packet.get("mag"),
        mode=args.mode,
        alpha=args.alpha,
        dt=dt,
        frame=None,
        insert_keyframe=args.record,
        subtract_gravity=not args.no_gravity,
        position_scale=args.position_scale,
    )

    extra = packet.get("extra", {})
    target_name = packet.get("sensor_name") or args.sensor_name
    if "rgb" in extra and args.sync_rgb:
        rgb = extra["rgb"]
        send_command("sensor_set_color_from_rgb", {
            "object_name": args.rgb_target or target_name,
            "r": rgb[0],
            "g": rgb[1],
            "b": rgb[2],
        })
    if "ambient" in extra and args.sync_light:
        send_command("sensor_set_light_from_ambient", {
            "light_name": args.light_target,
            "ambient_value": extra["ambient"],
        })
    if "pressure" in extra and args.sync_altitude:
        send_command("sensor_set_altitude", {
            "sensor_name": target_name,
            "pressure_hpa": extra["pressure"],
        })

    return result


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="MQTT-to-Blender real-time sensor bridge")
    parser.add_argument("--mqtt-host", default=env_str("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=env_int("MQTT_PORT", 1883))
    parser.add_argument("--mqtt-username", default=env_str("MQTT_USERNAME", ""))
    parser.add_argument("--mqtt-password", default=env_str("MQTT_PASSWORD", ""))
    parser.add_argument("--mqtt-topic", default=env_str("MQTT_TOPIC", "sensors/nano33/imu"))
    parser.add_argument("--mqtt-qos", type=int, default=env_int("MQTT_QOS", 0))
    parser.add_argument("--sensor-name", default=env_str("SENSOR_NAME", "Sensor_IMU"))
    parser.add_argument(
        "--mode",
        choices=["raw", "double-integrate", "mix", "cane", "integrate", "fusion"],
        default=env_str("SENSOR_MODE", "double-integrate"),
    )
    parser.add_argument("--alpha", type=float, default=env_float("SENSOR_ALPHA", 0.98))
    parser.add_argument("--position-scale", type=float, default=env_float("POSITION_SCALE", 1.0))
    parser.add_argument("--no-gravity", action="store_true", default=env_bool("NO_GRAVITY", False))
    parser.add_argument("--record", action="store_true", default=env_bool("RECORD_KEYFRAMES", False))
    parser.add_argument("--sync-rgb", action="store_true", default=env_bool("SYNC_RGB", False))
    parser.add_argument("--rgb-target", default=env_str("RGB_TARGET", ""))
    parser.add_argument("--sync-light", action="store_true", default=env_bool("SYNC_LIGHT", False))
    parser.add_argument("--light-target", default=env_str("LIGHT_TARGET", "Sensor_Light"))
    parser.add_argument("--sync-altitude", action="store_true", default=env_bool("SYNC_ALTITUDE", False))
    parser.add_argument("--drop-backlog", action="store_true", default=env_bool("DROP_BACKLOG", True))
    parser.add_argument("--blender-host", default=env_str("BLENDER_HOST", "127.0.0.1"))
    parser.add_argument("--blender-port", type=int, default=env_int("BLENDER_PORT", 9876))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server.BLENDER_HOST = args.blender_host
    server.BLENDER_PORT = args.blender_port
    packets: queue.Queue[tuple[float, dict[str, Any]]] = queue.Queue(maxsize=200)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if int(reason_code) == 0:
            print(f"{GREEN}[MQTT] Connected. Subscribing to {args.mqtt_topic}{RESET}")
            client.subscribe(args.mqtt_topic, qos=args.mqtt_qos)
        else:
            print(f"{RED}[MQTT] Connection failed: {reason_code}{RESET}")

    def on_message(client, userdata, message):
        packet = packet_from_payload(message.payload)
        if packet is None:
            print(f"\n{YELLOW}[MQTT] Ignored invalid payload on {message.topic}{RESET}")
            return
        if args.drop_backlog and packets.full():
            try:
                while packets.qsize() > 1:
                    packets.get_nowait()
            except queue.Empty:
                pass
        try:
            packets.put_nowait((time.time(), packet))
        except queue.Full:
            pass

    client = mqtt_client(on_connect, on_message)
    if args.mqtt_username:
        client.username_pw_set(args.mqtt_username, args.mqtt_password)

    print(f"{BLUE}{BOLD}===================================================={RESET}")
    print(f"{BLUE}{BOLD}       MQTT Real-time Sensor Bridge for Blender     {RESET}")
    print(f"{BLUE}{BOLD}===================================================={RESET}")
    print(f" MQTT:             {GREEN}{args.mqtt_host}:{args.mqtt_port}{RESET}")
    print(f" Topic:            {GREEN}{args.mqtt_topic}{RESET}")
    print(f" Blender Socket:   {GREEN}{args.blender_host}:{args.blender_port}{RESET}")
    print(f" Target Object:    {GREEN}{args.sensor_name}{RESET}")
    print(f" Processing Mode:  {GREEN}{args.mode.upper()}{RESET}")
    print(f" Record Keyframes: {GREEN}{args.record}{RESET}")
    print("----------------------------------------------------")

    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()

    last_send_time = time.time()
    packet_count = 0
    hz = 0
    hz_timer = time.time()
    try:
        while True:
            received_at, packet = packets.get(timeout=0.25)
            now = time.time()
            dt = max(now - last_send_time, 0.001)
            last_send_time = now
            try:
                result = send_to_blender(packet, args, dt)
            except Exception as exc:
                print(f"\r{RED}[Bridge Exception] {exc}{RESET}", end="", flush=True)
                continue

            if "error" in result:
                print(f"\r{RED}[Blender Error] {result['error']}{RESET}", end="", flush=True)
                continue

            packet_count += 1
            if time.time() - hz_timer >= 1.0:
                hz = packet_count
                packet_count = 0
                hz_timer = time.time()

            loc = result.get("location", [0, 0, 0])
            rot = result.get("rotation", [0, 0, 0])
            rot_deg = [math.degrees(value) for value in rot]
            lag_ms = (now - received_at) * 1000.0
            print(
                f"\r{BLUE}[Active]{RESET} {BOLD}{packet.get('sensor_name') or args.sensor_name}{RESET} | "
                f"Rate: {GREEN}{hz} Hz{RESET} | "
                f"Pos: [{loc[0]:.2f}, {loc[1]:.2f}, {loc[2]:.2f}] | "
                f"Rot: [{rot_deg[0]:.1f}, {rot_deg[1]:.1f}, {rot_deg[2]:.1f}] deg | "
                f"Lag: {YELLOW}{lag_ms:.0f} ms{RESET}      ",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[Bridge] Stopping...{RESET}")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
