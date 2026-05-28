# Blender MQTT Sensor Bridge

This repo drives a Blender digital twin from Arduino/Nano IMU data stored in
InfluxDB v2.7.12. The live path is now MQTT based, not MCP:

```text
InfluxDB v2
  -> influx_mqtt_publisher.py
  -> local MQTT broker
  -> mqtt_blender_bridge.py
  -> Blender addon socket
  -> Sensor_IMU object in Blender
```

The Blender addon still exposes the same real-time tuning controls for sensor
mode, gravity subtraction, deadband, axis remap, presets, and keyframe capture.

## Requirements

- Python 3.10+
- Docker Desktop or Docker Engine
- Blender 3.6+
- InfluxDB v2.7.12 reachable at `http://10.0.11.125:8086`
- Python dependencies from `pyproject.toml`

Install Python dependencies:

```bash
python3 -m uv sync
```

If you do not use `uv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install paho-mqtt requests pyserial
```

## Configure

Create a local env file:

```bash
cp .env.example .env
```

Edit `.env` and set:

```text
INFLUX_PASSWORD=<your password>
INFLUX_ORG=<your org>
INFLUX_BUCKET=<your bucket>
INFLUX_MEASUREMENT=<your measurement>
```

The current database was detected as org `arduino`, bucket `arduinostorage`,
measurement `arduino`, with `A.*` and `B.*` sensor fields. The example config
uses the `A.*` stream. Change `A.` to `B.` in the `INFLUX_FIELD_*` entries if
you want the second stream.

The publisher supports optional fields:

| Sensor value | Default field |
|---|---|
| Magnetometer | `mx`, `my`, `mz` |
| RGB color | `r`, `g`, `b` |
| Ambient light | `amb` |
| Pressure | `press` |

Gyro fields ending in `_dps` are converted from degrees/second to radians/second
before publishing to MQTT.

## Start MQTT

Run a local MQTT broker:

```bash
docker compose up -d mqtt
```

The broker listens on:

```text
MQTT TCP: localhost:1883
MQTT WebSocket: localhost:9001
```

## Start Blender

1. Open Blender.
2. Install `addon.py` from `Edit > Preferences > Add-ons > Install...`.
3. Enable the addon.
4. In the viewport sidebar, open the `Sensor` panel.
5. Click `Start Socket`.
6. Create an Empty named `Sensor_IMU`, or use an existing object with that name.
7. Apply the `stable_rotation` preset if you want the current known-good rotation settings.

The Blender addon listens on `127.0.0.1:9876`. The MQTT bridge sends processed
sensor transforms to that socket.

## Run the Live Pipeline

Terminal 1: publish latest InfluxDB samples to MQTT:

```bash
python3 -m uv run python influx_mqtt_publisher.py
```

To publish one sample and exit for testing:

```bash
python3 -m uv run python influx_mqtt_publisher.py --once
```

Terminal 2: subscribe to MQTT and drive Blender:

```bash
python3 -m uv run python mqtt_blender_bridge.py
```

If Blender is running on another machine, either run `mqtt_blender_bridge.py`
on that Blender machine, or expose the Blender socket on the network and point
the bridge to it:

```bash
python3 -m uv run python mqtt_blender_bridge.py \
  --mqtt-host <mqtt-broker-ip> \
  --blender-host <blender-machine-ip> \
  --blender-port 9876
```

For remote socket access, set the Blender addon's Host field to `0.0.0.0`
before clicking `Start Socket`, and allow TCP port `9876` through the Blender
machine firewall.

The MQTT JSON payload contains both named fields and vectors:

```json
{
  "source": "influxdb",
  "timestamp": "2026-05-28T12:00:00Z",
  "sensor_name": "Sensor_IMU",
  "ax": 0.0,
  "ay": 0.0,
  "az": 9.8067,
  "gx": 0.0,
  "gy": 0.0,
  "gz": 0.0,
  "accel": [0.0, 0.0, 9.8067],
  "gyro": [0.0, 0.0, 0.0],
  "mag": [12.3, -3.2, 40.0]
}
```

## Useful Commands

Publish with explicit schema:

```bash
python3 -m uv run python influx_mqtt_publisher.py \
  --influx-org <org> \
  --influx-bucket <bucket> \
  --influx-measurement imu \
  --influx-tag-filter device_id=nano33
```

Run Blender bridge in mix mode:

```bash
python3 -m uv run python mqtt_blender_bridge.py --mode mix --sensor-name Sensor_IMU
```

Enable optional extra sensor sync:

```bash
python3 -m uv run python mqtt_blender_bridge.py --sync-rgb --rgb-target Cube
python3 -m uv run python mqtt_blender_bridge.py --sync-light --light-target Sensor_Light
python3 -m uv run python mqtt_blender_bridge.py --sync-altitude
```

Stop MQTT:

```bash
docker compose down
```

## Troubleshooting

If InfluxDB connects but no MQTT messages publish, check that `.env` has the
right org, bucket, measurement, field names, and tag filter.

If MQTT publishes but Blender does not move:

1. Confirm Blender addon server is started.
2. Confirm an object named `Sensor_IMU` exists.
3. Try `python3 -m uv run python mqtt_blender_bridge.py --mode raw`.
4. Check the bridge terminal for `[Blender Error]`.

If rotation works but position drifts, use the existing Blender sensor controls:
`Deadband`, `Velocity Damping`, `Gravity`, `Subtract Gravity`, and `Set Init`.

## Legacy Files

The old USB serial bridge and Arduino sketches are still present for reference
and hardware testing, but the supported live flow is now InfluxDB to MQTT to
Blender.
