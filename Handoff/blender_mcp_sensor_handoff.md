# Handoff: Blender MCP Sensor Bridge

Date: 2026-05-27

## Current State

The project is in `/Users/er/Blender_Dev/MCP`.

The user is building an Arduino/Nano 33 BLE Sense to Blender motion bridge. The current priority for the next session is **translation / position movement**, because **rotation is now working again**.

The known-good rotation setup is captured as a preset:

- Preset file: `/Users/er/Blender_Dev/MCP/sensor_presets.json`
- Preset name: `stable_rotation`
- Blender addon code: `/Users/er/Blender_Dev/MCP/addon.py`
- Processing engine: `/Users/er/Blender_Dev/MCP/server.py`
- Serial bridge: `/Users/er/Blender_Dev/MCP/serial_bridge.py`
- Arduino sender: `/Users/er/Blender_Dev/MCP/arduino_sensor_sender/arduino_sensor_sender.ino`

The user should reload/restart Blender after addon code changes. Blender scene properties can persist in `.blend` files, so old UI settings can override terminal flags such as `serial_bridge.py --mode ...`.

## Hardware

Tested board:

- Arduino Nano 33 BLE Sense Rev1

I2C scan found:

- `0x1E`: LSM9DS1 magnetometer
- `0x39`: APDS-9960 gesture/color/proximity/ambient sensor
- `0x5C`: LPS22HB barometer/temperature sensor
- `0x6B`: LSM9DS1 accel/gyro

The sender is configured for Rev1 with `Arduino_LSM9DS1`.

## Current Working Rotation Preset

Use the Blender addon preset `stable_rotation`. It stores all sensor control settings, including:

- mode
- gravity
- subtract_gravity
- deadband
- gyro_deadband
- accel_gain
- accel_dt_scale
- accel_position_mix
- velocity_damping
- position_scale
- axis remap and signs
- ZUPT/cane settings
- sensor_to_tip

Key stable rotation values:

- `mode`: `double-integrate`
- `gravity`: `-9.86`
- `gyro_deadband`: `0.01`
- axis remap: X to X, Y to Y, Z to Z

In Blender:

1. Start MCP server.
2. In `Sensor Control > Presets`, set name to `stable_rotation`.
3. Click `Apply` or `Apply stable_rotation`.
4. Click `Reset State` / `Set Init` if needed.

Run Nano bridge:

```bash
cd /Users/er/Blender_Dev/MCP
uv run serial_bridge.py --port /dev/cu.usbmodem101 --mode double-integrate --sensor-name Sensor_IMU
```

If the port changes, run without `--port` and select the detected port.

## Important Findings

Rotation broke after restart because Blender addon scene settings can override command-line mode/settings after the first sensor packet.

Another cause was experimental axis remapping. Previously the default became:

- Sensor X to Z
- Sensor Y to Y
- Sensor Z to X

That remapped gyro as well as accel/mag, so double-integrate rotation no longer behaved like the originally stable version. The current default and `stable_rotation` preset use identity axis mapping.

## Translation Problem To Continue

The remaining issue is position/translation. The user reports rotation is good now, but translation still does not behave usefully.

Context from prior debugging:

- Raw mode visibly moves because it maps accelerometer values directly to location.
- Double-integrate rotation is stable, but translation from accelerometer integration tends to drift, stick, or not visibly move depending on filtering and gravity handling.
- Gravity default currently remembered as `-9.86`.
- Tilt-compensated gravity subtraction exists in `server.py` via `gravity_in_body_frame(...)`.
- Double-integrate uses:
  - `velocity += acceleration * dt`
  - `position += velocity * dt`
  - damping after position update
- `Accel DT Scale`, `Accel Gain`, `Accel Position Mix`, `Velocity Damping`, `Deadband`, and `Position Scale` are exposed in the addon UI.
- The user wants variables visible and tunable, not hidden.

The next agent should focus on translation methods while preserving the working rotation behavior.

Suggested approach:

- Treat rotation path as stable; avoid changing gyro integration unless necessary.
- Add/adjust translation as a separate layer that can be toggled or blended.
- Consider a "visual translation" mode that uses gravity-compensated acceleration with high-pass/decay or short-window displacement rather than pure unbounded double integration.
- Keep all parameters surfaced in Blender UI and stored in presets.
- Add a reset/calibration workflow that clears velocity/position and offset.
- Make sure changes do not overwrite user changes outside the focused files.

## Files And Artifacts To Reference

Do not duplicate existing docs; reference these:

- Project README: `/Users/er/Blender_Dev/MCP/README.md`
- Presets: `/Users/er/Blender_Dev/MCP/sensor_presets.json`
- Blender addon: `/Users/er/Blender_Dev/MCP/addon.py`
- Sensor processing: `/Users/er/Blender_Dev/MCP/server.py`
- Serial bridge: `/Users/er/Blender_Dev/MCP/serial_bridge.py`
- Arduino Nano sender: `/Users/er/Blender_Dev/MCP/arduino_sensor_sender/arduino_sensor_sender.ino`
- Arduino scanner: `/Users/er/Blender_Dev/MCP/arduino_sensor_scanner/arduino_sensor_scanner.ino`
- Backup from earlier stable-ish point: `/Users/er/Blender_Dev/MCP/backups/rollback_2026-05-26_imu/`

## Suggested Skills

- No special skill is required for the core Python/Blender/Arduino work.
- Use `computer-use:computer-use` only if the next agent needs to inspect or operate Blender/Arduino IDE UI directly.
- Use `browser:browser` only if a local web/browser verification task appears; not needed for this Blender addon unless the workflow changes.

## Privacy / Redaction Notes

No API keys, passwords, or private credentials were present in the conversation. Paths include the local username and are necessary for continuing local work.
