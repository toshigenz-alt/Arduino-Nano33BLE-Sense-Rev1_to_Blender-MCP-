#!/usr/bin/env python3
"""Poll InfluxDB v2 sensor data and publish the latest IMU sample to MQTT."""

import argparse
import csv
import json
import math
import sys
import time
from io import StringIO
from typing import Any

import requests

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt is not installed. Run `uv sync` or `pip install paho-mqtt`.")
    sys.exit(1)

from env_utils import env_bool, env_float, env_int, env_str, load_dotenv, parse_tag_filter


def flux_quote(value: str) -> str:
    return json.dumps(value)


def mqtt_client() -> mqtt.Client:
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    return mqtt.Client()


class InfluxQueryClient:
    def __init__(self, url: str, token: str = "", username: str = "", password: str = ""):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.token = token
        self.username = username
        self.password = password

    def authenticate(self) -> None:
        if self.token:
            self.session.headers.update({"Authorization": f"Token {self.token}"})
            return

        if not self.username or not self.password:
            raise RuntimeError("InfluxDB auth requires INFLUX_TOKEN or INFLUX_USERNAME/INFLUX_PASSWORD")

        response = self.session.post(
            f"{self.url}/api/v2/signin",
            auth=(self.username, self.password),
            timeout=10,
        )
        if response.status_code not in {204, 200}:
            raise RuntimeError(f"InfluxDB signin failed: {response.status_code} {response.text[:200]}")

    def auto_org(self) -> str:
        response = self.session.get(f"{self.url}/api/v2/orgs", timeout=10)
        response.raise_for_status()
        orgs = response.json().get("orgs", [])
        if len(orgs) == 1:
            return orgs[0]["name"]
        names = [org.get("name", "<unknown>") for org in orgs]
        raise RuntimeError(f"Set INFLUX_ORG. Auto-detect found {len(orgs)} orgs: {names}")

    def auto_bucket(self, org: str) -> str:
        response = self.session.get(f"{self.url}/api/v2/buckets", params={"org": org}, timeout=10)
        response.raise_for_status()
        buckets = [
            bucket for bucket in response.json().get("buckets", [])
            if bucket.get("name") not in {"_monitoring", "_tasks"}
        ]
        if len(buckets) == 1:
            return buckets[0]["name"]
        names = [bucket.get("name", "<unknown>") for bucket in buckets]
        raise RuntimeError(f"Set INFLUX_BUCKET. Auto-detect found {len(buckets)} candidate buckets: {names}")

    def query_csv(self, org: str, query: str) -> list[dict[str, str]]:
        response = self.session.post(
            f"{self.url}/api/v2/query",
            params={"org": org},
            data=query.encode("utf-8"),
            headers={
                "Accept": "application/csv",
                "Content-Type": "application/vnd.flux",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"InfluxDB query failed: {response.status_code} {response.text[:500]}")

        lines = [line for line in response.text.splitlines() if line and not line.startswith("#")]
        if not lines:
            return []
        return list(csv.DictReader(StringIO("\n".join(lines))))


def build_flux(args: argparse.Namespace, field_names: list[str]) -> str:
    if args.flux_query:
        return args.flux_query

    field_filter = " or ".join([f'r._field == {flux_quote(field)}' for field in field_names])
    tag_filters = "\n".join(
        f'  |> filter(fn: (r) => r[{flux_quote(key)}] == {flux_quote(value)})'
        for key, value in parse_tag_filter(args.influx_tag_filter).items()
    )
    return f'''from(bucket: {flux_quote(args.influx_bucket)})
  |> range(start: {args.influx_range})
  |> filter(fn: (r) => r._measurement == {flux_quote(args.influx_measurement)})
  |> filter(fn: (r) => {field_filter})
{tag_filters}
  |> last()
'''


def parse_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key)
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(row: dict[str, str], key: str) -> int | None:
    value = parse_float(row, key)
    if value is None:
        return None
    return int(value)


def collapse_query_rows(rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None
    if any("ax" in row or "accel" in row for row in rows) or len(rows) == 1 and "_field" not in rows[0]:
        return rows[0]

    collapsed: dict[str, str] = {}
    latest_time = ""
    for row in rows:
        field = row.get("_field")
        value = row.get("_value")
        if field and value not in {None, ""}:
            collapsed[field] = value
        row_time = row.get("_time") or ""
        if row_time > latest_time:
            latest_time = row_time
    if latest_time:
        collapsed["_time"] = latest_time
    return collapsed


def packet_from_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any] | None:
    ax = parse_float(row, args.field_ax)
    ay = parse_float(row, args.field_ay)
    az = parse_float(row, args.field_az)
    gx = parse_float(row, args.field_gx)
    gy = parse_float(row, args.field_gy)
    gz = parse_float(row, args.field_gz)
    if None in {ax, ay, az, gx, gy, gz}:
        return None

    mx = parse_float(row, args.field_mx)
    my = parse_float(row, args.field_my)
    mz = parse_float(row, args.field_mz)
    r = parse_int(row, args.field_r)
    g = parse_int(row, args.field_g)
    b = parse_int(row, args.field_b)
    ambient = parse_float(row, args.field_ambient)
    pressure = parse_float(row, args.field_pressure)

    if args.accel_unit == "g":
        ax *= 9.80665
        ay *= 9.80665
        az *= 9.80665
    if args.gyro_unit in {"deg", "dps"}:
        gx = math.radians(gx)
        gy = math.radians(gy)
        gz = math.radians(gz)
    if pressure is not None and args.pressure_unit == "pa":
        pressure /= 100.0

    packet: dict[str, Any] = {
        "source": "influxdb",
        "timestamp": row.get("_time") or time.time(),
        "sensor_name": args.sensor_name,
        "ax": ax,
        "ay": ay,
        "az": az,
        "gx": gx,
        "gy": gy,
        "gz": gz,
        "accel": [ax, ay, az],
        "gyro": [gx, gy, gz],
    }

    if None not in {mx, my, mz}:
        packet.update({"mx": mx, "my": my, "mz": mz, "mag": [mx, my, mz]})
    if None not in {r, g, b}:
        packet.update({"r": r, "g": g, "b": b})
        packet.setdefault("extra", {})["rgb"] = [r, g, b]
    if ambient is not None:
        packet["amb"] = ambient
        packet.setdefault("extra", {})["ambient"] = ambient
    if pressure is not None:
        packet["press"] = pressure
        packet.setdefault("extra", {})["pressure"] = pressure

    return packet


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="InfluxDB v2 to MQTT IMU publisher")
    parser.add_argument("--influx-url", default=env_str("INFLUX_URL", "http://10.0.11.125:8086"))
    parser.add_argument("--influx-token", default=env_str("INFLUX_TOKEN", ""))
    parser.add_argument("--influx-username", default=env_str("INFLUX_USERNAME", "admin"))
    parser.add_argument("--influx-password", default=env_str("INFLUX_PASSWORD", ""))
    parser.add_argument("--influx-org", default=env_str("INFLUX_ORG", ""))
    parser.add_argument("--influx-bucket", default=env_str("INFLUX_BUCKET", ""))
    parser.add_argument("--influx-measurement", default=env_str("INFLUX_MEASUREMENT", "imu"))
    parser.add_argument("--influx-tag-filter", default=env_str("INFLUX_TAG_FILTER", ""))
    parser.add_argument("--influx-range", default=env_str("INFLUX_RANGE", "-15s"))
    parser.add_argument("--flux-query", default=env_str("INFLUX_FLUX_QUERY", ""))
    parser.add_argument("--poll-interval", type=float, default=env_float("INFLUX_POLL_INTERVAL", 0.2))
    parser.add_argument("--publish-duplicates", action="store_true", default=env_bool("PUBLISH_DUPLICATES", False))
    parser.add_argument("--once", action="store_true", default=env_bool("PUBLISH_ONCE", False))

    parser.add_argument("--mqtt-host", default=env_str("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=env_int("MQTT_PORT", 1883))
    parser.add_argument("--mqtt-username", default=env_str("MQTT_USERNAME", ""))
    parser.add_argument("--mqtt-password", default=env_str("MQTT_PASSWORD", ""))
    parser.add_argument("--mqtt-topic", default=env_str("MQTT_TOPIC", "sensors/nano33/imu"))
    parser.add_argument("--mqtt-qos", type=int, default=env_int("MQTT_QOS", 0))
    parser.add_argument("--mqtt-retain", action="store_true", default=env_bool("MQTT_RETAIN", False))
    parser.add_argument("--sensor-name", default=env_str("SENSOR_NAME", "Sensor_IMU"))

    parser.add_argument("--field-ax", default=env_str("INFLUX_FIELD_AX", "ax"))
    parser.add_argument("--field-ay", default=env_str("INFLUX_FIELD_AY", "ay"))
    parser.add_argument("--field-az", default=env_str("INFLUX_FIELD_AZ", "az"))
    parser.add_argument("--field-gx", default=env_str("INFLUX_FIELD_GX", "gx"))
    parser.add_argument("--field-gy", default=env_str("INFLUX_FIELD_GY", "gy"))
    parser.add_argument("--field-gz", default=env_str("INFLUX_FIELD_GZ", "gz"))
    parser.add_argument("--field-mx", default=env_str("INFLUX_FIELD_MX", "mx"))
    parser.add_argument("--field-my", default=env_str("INFLUX_FIELD_MY", "my"))
    parser.add_argument("--field-mz", default=env_str("INFLUX_FIELD_MZ", "mz"))
    parser.add_argument("--field-r", default=env_str("INFLUX_FIELD_R", "r"))
    parser.add_argument("--field-g", default=env_str("INFLUX_FIELD_G", "g"))
    parser.add_argument("--field-b", default=env_str("INFLUX_FIELD_B", "b"))
    parser.add_argument("--field-ambient", default=env_str("INFLUX_FIELD_AMBIENT", "amb"))
    parser.add_argument("--field-pressure", default=env_str("INFLUX_FIELD_PRESSURE", "press"))
    parser.add_argument("--accel-unit", choices=["mps2", "g"], default=env_str("INFLUX_ACCEL_UNIT", "mps2"))
    parser.add_argument("--gyro-unit", choices=["rad", "deg", "dps"], default=env_str("INFLUX_GYRO_UNIT", "rad"))
    parser.add_argument("--pressure-unit", choices=["hpa", "pa"], default=env_str("INFLUX_PRESSURE_UNIT", "hpa"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    field_names = [
        args.field_ax, args.field_ay, args.field_az,
        args.field_gx, args.field_gy, args.field_gz,
        args.field_mx, args.field_my, args.field_mz,
        args.field_r, args.field_g, args.field_b,
        args.field_ambient, args.field_pressure,
    ]
    field_names = sorted({field for field in field_names if field})

    influx = InfluxQueryClient(
        args.influx_url,
        token=args.influx_token,
        username=args.influx_username,
        password=args.influx_password,
    )
    influx.authenticate()
    if not args.influx_org:
        args.influx_org = influx.auto_org()
    if not args.influx_bucket and not args.flux_query:
        args.influx_bucket = influx.auto_bucket(args.influx_org)

    client = mqtt_client()
    if args.mqtt_username:
        client.username_pw_set(args.mqtt_username, args.mqtt_password)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()

    print("[Influx->MQTT] Running")
    print(f"  Influx: {args.influx_url} org={args.influx_org} bucket={args.influx_bucket or '<custom query>'}")
    print(f"  MQTT:   {args.mqtt_host}:{args.mqtt_port} topic={args.mqtt_topic}")

    last_signature = None
    try:
        while True:
            rows = influx.query_csv(args.influx_org, build_flux(args, field_names))
            row = collapse_query_rows(rows)
            if row:
                packet = packet_from_row(row, args)
                if packet is None:
                    print("[Influx->MQTT] Latest row is missing required accel/gyro fields")
                else:
                    signature = json.dumps({
                        "timestamp": packet.get("timestamp"),
                        "accel": packet["accel"],
                        "gyro": packet["gyro"],
                    }, sort_keys=True)
                    if args.publish_duplicates or signature != last_signature:
                        payload = json.dumps(packet, separators=(",", ":"))
                        result = client.publish(
                            args.mqtt_topic,
                            payload,
                            qos=args.mqtt_qos,
                            retain=args.mqtt_retain,
                        )
                        result.wait_for_publish(timeout=5)
                        last_signature = signature
                        print(f"[Influx->MQTT] published {packet.get('timestamp')} accel={packet['accel']} gyro={packet['gyro']}")
                        if args.once:
                            return
            time.sleep(max(args.poll_interval, 0.01))
    except KeyboardInterrupt:
        print("\n[Influx->MQTT] Stopping")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
