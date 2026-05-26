"""
Blender MCP Server — Model Context Protocol server for AI-driven Blender control.

This server bridges AI clients (Claude, Cursor, VS Code) to Blender via the
MCP protocol, with full sensor data visualization support (IMU fusion).

Usage:
    uv run server.py                    # stdio transport (for Claude/Cursor)
    uv run server.py --transport http   # HTTP transport

Or with mcp CLI:
    uv run mcp dev server.py
"""

import json
import math
import socket
import csv
import io
from typing import Optional
from mcp.server.fastmcp import FastMCP

# =============================================================================
# FastMCP Server
# =============================================================================

mcp = FastMCP(
    "Blender MCP",
    instructions="""MCP server for controlling Blender and visualizing sensor data.
    
    GENERAL TOOLS: Create/modify/delete 3D objects, set materials, add modifiers,
    control camera/lighting, render scenes, and execute arbitrary Python code.
    
    SENSOR TOOLS: Import accelerometer/gyroscope/magnetometer data from 
    Arduino Nano 33 BLE Sense Rev2 (or similar IMU) and visualize it in Blender
    using Null (Empty) objects. Supports 3 processing modes:
    - raw: direct value mapping
    - double-integrate: double-integrate accel, single-integrate gyro
    - mix: complementary accel/gyro/mag filter (recommended)
    """
)


# =============================================================================
# Blender Connection Bridge
# =============================================================================

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876


def send_command(command: str, params: dict = None, timeout: float = 60.0) -> dict:
    """Send a command to the Blender addon via TCP socket."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((BLENDER_HOST, BLENDER_PORT))

        request = json.dumps({"command": command, "params": params or {}}) + "\n"
        sock.sendall(request.encode("utf-8"))

        # Read response
        buffer = b""
        while True:
            data = sock.recv(65536)
            if not data:
                break
            buffer += data
            if b"\n" in buffer:
                break

        sock.close()

        if buffer:
            response_line = buffer.split(b"\n")[0]
            return json.loads(response_line.decode("utf-8"))
        else:
            return {"error": "No response from Blender"}

    except ConnectionRefusedError:
        return {"error": "Cannot connect to Blender. Make sure the MCP Addon is running (View3D > Sidebar > MCP > Start Server)"}
    except socket.timeout:
        return {"error": f"Connection to Blender timed out ({timeout}s). The operation may still be running."}
    except Exception as e:
        return {"error": f"Connection error: {str(e)}"}


# =============================================================================
# Sensor Data Processing Engine
# =============================================================================

# Per-sensor state for real-time streaming
_sensor_states = {}


def normalize_sensor_mode(mode: str | None) -> str:
    """Map UI-friendly mode names to the internal processing modes."""
    aliases = {
        "raw": "raw",
        "double-integrate": "integrate",
        "double_integrate": "integrate",
        "doubleintegrate": "integrate",
        "integrate": "integrate",
        "mix": "fusion",
        "fusion": "fusion",
    }
    key = (mode or "fusion").strip().lower()
    return aliases.get(key, key)


class SensorState:
    """Track integration state for a sensor."""
    def __init__(self):
        self.velocity = [0.0, 0.0, 0.0]
        self.position = [0.0, 0.0, 0.0]
        self.pitch = 0.0
        self.roll = 0.0
        self.yaw = 0.0
        self.pos_offset = [0.0, 0.0, 0.0]
        self.rot_offset = [0.0, 0.0, 0.0]
        self.gravity_mag = 9.80665
        self.deadband = 0.15
        self.damping = 0.95
        self.mode_override = None

    def reset(self):
        self.velocity = [0.0, 0.0, 0.0]
        self.position = [0.0, 0.0, 0.0]
        self.pitch = 0.0
        self.roll = 0.0
        self.yaw = 0.0
        self.pos_offset = [0.0, 0.0, 0.0]
        self.rot_offset = [0.0, 0.0, 0.0]
        # Config parameters like gravity_mag, deadband, damping are retained on reset


def moving_average_filter(data: list[list[float]], window: int) -> list[list[float]]:
    """Apply moving average filter to 3-axis data."""
    if window <= 1:
        return data

    filtered = []
    half = window // 2
    n = len(data)
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        avg = [0.0, 0.0, 0.0]
        count = end - start
        for j in range(start, end):
            for k in range(3):
                avg[k] += data[j][k]
        filtered.append([avg[k] / count for k in range(3)])
    return filtered


def process_sensor_data_raw(
    accel_data: list[list[float]],
    gyro_data: list[list[float]],
    timestamps: list[float],
    gyro_unit: str = "rad",
) -> tuple[list[list[float]], list[list[float]]]:
    """Raw mode: use values directly."""
    locations = accel_data

    # Convert gyro to radians if needed
    if gyro_unit == "deg":
        rotations = [[math.radians(g) for g in row] for row in gyro_data]
    else:
        rotations = gyro_data

    return locations, rotations


def process_sensor_data_integrate(
    accel_data: list[list[float]],
    gyro_data: list[list[float]],
    timestamps: list[float],
    subtract_gravity: bool = True,
    gravity_vector: list[float] = None,
    filter_window: int = 5,
    gyro_unit: str = "rad",
) -> tuple[list[list[float]], list[list[float]]]:
    """Integrate mode: double-integrate accel, single-integrate gyro."""
    if gravity_vector is None:
        gravity_vector = [0.0, 0.0, 9.81]

    n = len(accel_data)
    if n == 0:
        return [], []

    # Subtract gravity
    if subtract_gravity:
        accel_corrected = [
            [accel_data[i][k] - gravity_vector[k] for k in range(3)]
            for i in range(n)
        ]
    else:
        accel_corrected = [list(a) for a in accel_data]

    # Filter
    accel_filtered = moving_average_filter(accel_corrected, filter_window)

    # Convert gyro units
    if gyro_unit == "deg":
        gyro_rad = [[math.radians(g) for g in row] for row in gyro_data]
    else:
        gyro_rad = [list(g) for g in gyro_data]

    # Double-integrate accelerometer (trapezoidal rule)
    velocity = [[0.0, 0.0, 0.0]]
    position = [[0.0, 0.0, 0.0]]

    for i in range(1, n):
        dt = timestamps[i] - timestamps[i - 1]
        if dt <= 0:
            dt = 0.01

        v = [0.0, 0.0, 0.0]
        p = [0.0, 0.0, 0.0]
        for k in range(3):
            # Trapezoidal integration
            v[k] = velocity[-1][k] + (accel_filtered[i][k] + accel_filtered[i - 1][k]) / 2.0 * dt
            p[k] = position[-1][k] + (v[k] + velocity[-1][k]) / 2.0 * dt
        velocity.append(v)
        position.append(p)

    # Single-integrate gyroscope
    angles = [[0.0, 0.0, 0.0]]
    for i in range(1, n):
        dt = timestamps[i] - timestamps[i - 1]
        if dt <= 0:
            dt = 0.01

        angle = [0.0, 0.0, 0.0]
        for k in range(3):
            angle[k] = angles[-1][k] + (gyro_rad[i][k] + gyro_rad[i - 1][k]) / 2.0 * dt
        angles.append(angle)

    return position, angles


def process_sensor_data_fusion(
    accel_data: list[list[float]],
    gyro_data: list[list[float]],
    mag_data: list[list[float]] | None,
    timestamps: list[float],
    alpha: float = 0.98,
    subtract_gravity: bool = True,
    gravity_vector: list[float] = None,
    filter_window: int = 5,
    gyro_unit: str = "rad",
) -> tuple[list[list[float]], list[list[float]]]:
    """Fusion mode: 9-axis complementary filter for orientation,
    double-integrate accel in world frame for position."""
    if gravity_vector is None:
        gravity_vector = [0.0, 0.0, 9.81]

    n = len(accel_data)
    if n == 0:
        return [], []

    # Convert gyro units
    if gyro_unit == "deg":
        gyro_rad = [[math.radians(g) for g in row] for row in gyro_data]
    else:
        gyro_rad = [list(g) for g in gyro_data]

    # Complementary filter for orientation
    pitch = 0.0
    roll = 0.0
    yaw = 0.0
    orientations = []
    positions = []
    velocity = [0.0, 0.0, 0.0]
    position = [0.0, 0.0, 0.0]

    for i in range(n):
        ax, ay, az = accel_data[i]
        gx, gy, gz = gyro_rad[i]

        if i == 0:
            dt = 0.01
        else:
            dt = timestamps[i] - timestamps[i - 1]
            if dt <= 0:
                dt = 0.01

        # Accel-based angles
        accel_pitch = math.atan2(ay, math.sqrt(ax * ax + az * az))
        accel_roll = math.atan2(-ax, az)

        # Complementary filter for pitch & roll
        pitch = alpha * (pitch + gx * dt) + (1.0 - alpha) * accel_pitch
        roll = alpha * (roll + gy * dt) + (1.0 - alpha) * accel_roll

        # Yaw from magnetometer (if available)
        if mag_data and i < len(mag_data) and mag_data[i]:
            mx, my, mz = mag_data[i]
            # Tilt compensation
            cos_pitch = math.cos(pitch)
            sin_pitch = math.sin(pitch)
            cos_roll = math.cos(roll)
            sin_roll = math.sin(roll)

            mx_comp = mx * cos_pitch + mz * sin_pitch
            my_comp = mx * sin_roll * sin_pitch + my * cos_roll - mz * sin_roll * cos_pitch

            mag_yaw = math.atan2(-my_comp, mx_comp)
            yaw = alpha * (yaw + gz * dt) + (1.0 - alpha) * mag_yaw
        else:
            yaw = yaw + gz * dt

        orientations.append([pitch, roll, yaw])

        # Rotate accel to world frame for better gravity subtraction
        cos_p = math.cos(pitch)
        sin_p = math.sin(pitch)
        cos_r = math.cos(roll)
        sin_r = math.sin(roll)
        cos_y = math.cos(yaw)
        sin_y = math.sin(yaw)

        # Simplified rotation matrix (ZYX convention)
        ax_world = (cos_y * cos_p) * ax + (cos_y * sin_p * sin_r - sin_y * cos_r) * ay + (cos_y * sin_p * cos_r + sin_y * sin_r) * az
        ay_world = (sin_y * cos_p) * ax + (sin_y * sin_p * sin_r + cos_y * cos_r) * ay + (sin_y * sin_p * cos_r - cos_y * sin_r) * az
        az_world = (-sin_p) * ax + (cos_p * sin_r) * ay + (cos_p * cos_r) * az

        # Subtract gravity in world frame
        if subtract_gravity:
            az_world -= gravity_vector[2]

        # Double-integrate for position
        if i > 0:
            velocity[0] += ax_world * dt
            velocity[1] += ay_world * dt
            velocity[2] += az_world * dt
            position[0] += velocity[0] * dt
            position[1] += velocity[1] * dt
            position[2] += velocity[2] * dt

        positions.append(list(position))

    # Apply filter to positions
    if filter_window > 1:
        positions = moving_average_filter(positions, filter_window)

    return positions, orientations


def parse_sensor_log(
    file_path: str,
    format: str = "auto",
    time_column: str = "timestamp",
    accel_columns: list[str] = None,
    gyro_columns: list[str] = None,
    mag_columns: list[str] = None,
) -> tuple[list[float], list[list[float]], list[list[float]], list[list[float]] | None]:
    """Parse sensor log file into structured data."""
    if accel_columns is None:
        accel_columns = ["accel_x", "accel_y", "accel_z"]
    if gyro_columns is None:
        gyro_columns = ["gyro_x", "gyro_y", "gyro_z"]
    if mag_columns is None:
        mag_columns = ["mag_x", "mag_y", "mag_z"]

    # Auto-detect format
    if format == "auto":
        if file_path.endswith(".json"):
            format = "json"
        else:
            format = "csv"

    timestamps = []
    accel_data = []
    gyro_data = []
    mag_data = None

    if format == "csv":
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                timestamps.append(float(row[time_column]))
                accel_data.append([float(row[c]) for c in accel_columns])
                gyro_data.append([float(row[c]) for c in gyro_columns])

                # Check if magnetometer columns exist
                if all(c in row for c in mag_columns):
                    if mag_data is None:
                        mag_data = []
                    mag_data.append([float(row[c]) for c in mag_columns])

    elif format == "json":
        with open(file_path, 'r') as f:
            data = json.load(f)

        # Support both array and object with 'data' key
        if isinstance(data, dict):
            data = data.get("data", [])

        # Auto-detect column names
        if data:
            sample = data[0]
            t_key = time_column if time_column in sample else "t"
            a_keys = accel_columns if accel_columns[0] in sample else ["ax", "ay", "az"]
            g_keys = gyro_columns if gyro_columns[0] in sample else ["gx", "gy", "gz"]
            m_keys = mag_columns if mag_columns[0] in sample else ["mx", "my", "mz"]

            for row in data:
                timestamps.append(float(row[t_key]))
                accel_data.append([float(row[k]) for k in a_keys])
                gyro_data.append([float(row[k]) for k in g_keys])

                if all(k in row for k in m_keys):
                    if mag_data is None:
                        mag_data = []
                    mag_data.append([float(row[k]) for k in m_keys])

    return timestamps, accel_data, gyro_data, mag_data


# =============================================================================
# General Blender Tools
# =============================================================================

@mcp.tool()
def get_scene_info() -> dict:
    """Get comprehensive information about the current Blender scene.
    Returns all objects, materials, cameras, and lights with their properties."""
    return send_command("get_scene_info")


@mcp.tool()
def get_object_info(name: str) -> dict:
    """Get detailed information about a specific object in the scene.
    
    Args:
        name: Name of the object to inspect
    """
    return send_command("get_object_info", {"name": name})


@mcp.tool()
def create_object(
    type: str = "cube",
    name: str = None,
    location: list[float] = None,
    scale: list[float] = None,
) -> dict:
    """Create a 3D primitive object in the scene.
    
    Args:
        type: Object type — cube, sphere, cylinder, plane, cone, torus, monkey, ico_sphere
        name: Optional name for the object
        location: XYZ position [x, y, z] (default: [0, 0, 0])
        scale: XYZ scale [x, y, z] (default: [1, 1, 1])
    """
    params = {"type": type}
    if name:
        params["name"] = name
    if location:
        params["location"] = location
    if scale:
        params["scale"] = scale
    return send_command("create_object", params)


@mcp.tool()
def modify_object(
    name: str,
    location: list[float] = None,
    rotation: list[float] = None,
    scale: list[float] = None,
    new_name: str = None,
    rotation_unit: str = "deg",
) -> dict:
    """Modify an object's transform (location, rotation, scale).
    
    Args:
        name: Name of the object to modify
        location: New XYZ position [x, y, z]
        rotation: New rotation [rx, ry, rz] in degrees (or radians if rotation_unit='rad')
        scale: New XYZ scale [x, y, z]
        new_name: Rename the object
        rotation_unit: 'deg' for degrees, 'rad' for radians
    """
    params = {"name": name, "rotation_unit": rotation_unit}
    if location:
        params["location"] = location
    if rotation:
        params["rotation"] = rotation
    if scale:
        params["scale"] = scale
    if new_name:
        params["new_name"] = new_name
    return send_command("modify_object", params)


@mcp.tool()
def delete_object(name: str) -> dict:
    """Delete an object from the scene.
    
    Args:
        name: Name of the object to delete
    """
    return send_command("delete_object", {"name": name})


@mcp.tool()
def set_material(
    object_name: str,
    color: list[float] = None,
    material_name: str = None,
    metallic: float = 0.0,
    roughness: float = 0.5,
) -> dict:
    """Create and assign a material to an object.
    
    Args:
        object_name: Name of the object
        color: RGBA color [r, g, b, a] where values are 0.0-1.0
        material_name: Optional name for the material
        metallic: Metallic value 0.0-1.0
        roughness: Roughness value 0.0-1.0
    """
    params = {"object_name": object_name, "metallic": metallic, "roughness": roughness}
    if color:
        params["color"] = color
    if material_name:
        params["material_name"] = material_name
    return send_command("set_material", params)


@mcp.tool()
def add_modifier(
    object_name: str,
    modifier_type: str = "SUBSURF",
    modifier_name: str = None,
    params: dict = None,
) -> dict:
    """Add a modifier to an object.
    
    Args:
        object_name: Name of the object
        modifier_type: SUBSURF, BEVEL, SOLIDIFY, MIRROR, ARRAY, BOOLEAN, DECIMATE, WIREFRAME, SMOOTH
        modifier_name: Optional custom name
        params: Modifier-specific parameters (e.g., {"levels": 3} for subdivision)
    """
    cmd_params = {"object_name": object_name, "modifier_type": modifier_type}
    if modifier_name:
        cmd_params["modifier_name"] = modifier_name
    if params:
        cmd_params["params"] = params
    return send_command("add_modifier", cmd_params)


@mcp.tool()
def set_camera(
    name: str = "Camera",
    location: list[float] = None,
    rotation: list[float] = None,
    target: list[float] = None,
    focal_length: float = None,
    set_active: bool = True,
) -> dict:
    """Set camera position, rotation, and properties.
    
    Args:
        name: Camera name (creates new if not found)
        location: XYZ position [x, y, z]
        rotation: Rotation in degrees [rx, ry, rz]
        target: Point the camera at this XYZ position [x, y, z]
        focal_length: Focal length in mm
        set_active: Set as the active camera
    """
    params = {"name": name, "set_active": set_active}
    if location:
        params["location"] = location
    if rotation:
        params["rotation"] = rotation
    if target:
        params["target"] = target
    if focal_length is not None:
        params["focal_length"] = focal_length
    return send_command("set_camera", params)


@mcp.tool()
def set_light(
    name: str = "Light",
    type: str = "POINT",
    location: list[float] = None,
    rotation: list[float] = None,
    energy: float = None,
    color: list[float] = None,
) -> dict:
    """Create or modify a light in the scene.
    
    Args:
        name: Light name (creates new if not found)
        type: POINT, SUN, SPOT, or AREA
        location: XYZ position [x, y, z]
        rotation: Rotation in degrees [rx, ry, rz]
        energy: Light power/intensity in watts
        color: RGB color [r, g, b]
    """
    params = {"name": name, "type": type}
    if location:
        params["location"] = location
    if rotation:
        params["rotation"] = rotation
    if energy is not None:
        params["energy"] = energy
    if color:
        params["color"] = color
    return send_command("set_light", params)


@mcp.tool()
def render_scene(
    resolution_x: int = 1920,
    resolution_y: int = 1080,
    samples: int = 128,
    output_path: str = None,
) -> dict:
    """Render the current scene and return the image as base64.
    
    Args:
        resolution_x: Image width in pixels
        resolution_y: Image height in pixels
        samples: Render samples (for Cycles engine)
        output_path: Optional file path to save the render
    """
    params = {"resolution_x": resolution_x, "resolution_y": resolution_y, "samples": samples}
    if output_path:
        params["output_path"] = output_path
    return send_command("render_scene", params, timeout=300.0)


@mcp.tool()
def execute_blender_code(code: str) -> dict:
    """Execute arbitrary Python code in Blender.
    
    WARNING: This runs code directly in Blender's Python environment.
    Use with caution in trusted environments only.
    
    Args:
        code: Python code to execute. Has access to bpy, math, Vector, Euler, Matrix.
    """
    return send_command("execute_code", {"code": code})


@mcp.tool()
def get_polyhaven_asset(
    name: str,
    type: str = "hdri",
    resolution: str = "1k",
) -> dict:
    """Download and import an asset from Poly Haven.
    
    Args:
        name: Asset name (e.g., "kloppenheim_06" for an HDRI)
        type: Asset type — 'hdri' or 'texture'
        resolution: Resolution — '1k', '2k', '4k'
    """
    return send_command("get_polyhaven_asset", {"name": name, "type": type, "resolution": resolution}, timeout=120.0)


# =============================================================================
# Sensor Tools
# =============================================================================

@mcp.tool()
def sensor_create_null(
    name: str = "Sensor_IMU",
    display_type: str = "PLAIN_AXES",
    display_size: float = 1.0,
    location: list[float] = None,
    color: list[float] = None,
) -> dict:
    """Create a Null (Empty) object in Blender to represent a sensor.
    The Null visually shows the sensor's position and orientation.
    
    Args:
        name: Name for the sensor object (e.g., "IMU_Sensor_01")
        display_type: Visual style — PLAIN_AXES, ARROWS, SINGLE_ARROW, CIRCLE, CUBE, SPHERE, CONE
        display_size: Size of the display (default: 1.0)
        location: Initial position [x, y, z]
        color: RGBA color [r, g, b, a] (default: orange)
    """
    params = {"name": name, "display_type": display_type, "display_size": display_size}
    if location:
        params["location"] = location
    if color:
        params["color"] = color
    return send_command("sensor_create_null", params)


@mcp.tool()
def sensor_import_log(
    sensor_name: str,
    file_path: str,
    mode: str = "mix",
    alpha: float = 0.98,
    format: str = "auto",
    fps: int = 24,
    position_scale: float = 1.0,
    gyro_unit: str = "rad",
    subtract_gravity: bool = True,
    gravity_vector: list[float] = None,
    filter_window: int = 5,
    accel_columns: list[str] = None,
    gyro_columns: list[str] = None,
    mag_columns: list[str] = None,
    time_column: str = "timestamp",
) -> dict:
    """Import sensor log data from a CSV/JSON file and create keyframe animation.
    
    Processes accelerometer and gyroscope data with 3 processing modes:
    - raw: use sensor values directly (accel→location, gyro→rotation)
    - double-integrate: double-integrate accel→position, single-integrate gyro→angle
    - mix: complementary accel/gyro/mag mix (recommended, requires magnetometer for best results)
    
    Args:
        sensor_name: Name of the Null object to animate
        file_path: Path to the sensor log file (CSV or JSON)
        mode: Processing mode — 'raw', 'double-integrate', or 'mix' (default: mix)
        alpha: Complementary filter weight (0.0-1.0). Higher = trust gyro more. Default: 0.98
        format: File format — 'csv', 'json', or 'auto' (auto-detect from extension)
        fps: Frame rate for mapping timestamp to Blender frames (default: 24)
        position_scale: Scale factor for position values (default: 1.0)
        gyro_unit: Gyroscope unit — 'rad' (radians/s) or 'deg' (degrees/s)
        subtract_gravity: Subtract gravity from accelerometer data (default: True)
        gravity_vector: Custom gravity vector [gx, gy, gz] (default: [0, 0, 9.81])
        filter_window: Moving average filter window size (1 = no filter, default: 5)
        accel_columns: CSV/JSON column names for accelerometer [x, y, z]
        gyro_columns: CSV/JSON column names for gyroscope [x, y, z]
        mag_columns: CSV/JSON column names for magnetometer [x, y, z]
        time_column: Column name for timestamps
    """
    if gravity_vector is None:
        gravity_vector = [0.0, 0.0, 9.81]

    # Parse the sensor log file
    try:
        timestamps, accel_data, gyro_data, mag_data = parse_sensor_log(
            file_path=file_path,
            format=format,
            time_column=time_column,
            accel_columns=accel_columns,
            gyro_columns=gyro_columns,
            mag_columns=mag_columns,
        )
    except Exception as e:
        return {"error": f"Failed to parse sensor log: {str(e)}"}

    if not timestamps:
        return {"error": "No data found in sensor log file"}

    active_mode = normalize_sensor_mode(mode)

    # Process data according to mode
    if active_mode == "raw":
        positions, rotations = process_sensor_data_raw(
            accel_data, gyro_data, timestamps, gyro_unit
        )
    elif active_mode == "integrate":
        positions, rotations = process_sensor_data_integrate(
            accel_data, gyro_data, timestamps,
            subtract_gravity=subtract_gravity,
            gravity_vector=gravity_vector,
            filter_window=filter_window,
            gyro_unit=gyro_unit,
        )
    elif active_mode == "fusion":
        positions, rotations = process_sensor_data_fusion(
            accel_data, gyro_data, mag_data, timestamps,
            alpha=alpha,
            subtract_gravity=subtract_gravity,
            gravity_vector=gravity_vector,
            filter_window=filter_window,
            gyro_unit=gyro_unit,
        )
    else:
        return {"error": f"Unknown mode '{mode}'. Use 'raw', 'double-integrate', or 'mix'."}

    # Convert to frame-based data for Blender
    frame_data = []
    for i in range(len(timestamps)):
        frame = int(timestamps[i] * fps)
        frame_data.append({
            "frame": frame,
            "location": list(positions[i]) if i < len(positions) else [0, 0, 0],
            "rotation": list(rotations[i]) if i < len(rotations) else [0, 0, 0],
        })

    # Send to Blender
    result = send_command("sensor_import_log", {
        "sensor_name": sensor_name,
        "data": frame_data,
        "fps": fps,
        "position_scale": position_scale,
    })

    # Add processing info to result
    if not result.get("error"):
        max_disp = 0.0
        for p in positions:
            disp = math.sqrt(sum(x * x for x in p))
            max_disp = max(max_disp, disp)

        result["processing"] = {
            "mode": mode,
            "alpha": alpha if active_mode == "fusion" else None,
            "data_points": len(timestamps),
            "duration_seconds": timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0,
            "max_displacement": max_disp * position_scale,
            "has_magnetometer": mag_data is not None,
            "filter_window": filter_window,
        }

    return result


@mcp.tool()
def sensor_apply_data(
    sensor_name: str,
    accel: list[float],
    gyro: list[float],
    mag: list[float] = None,
    mode: str = "mix",
    alpha: float = 0.98,
    dt: float = 0.01,
    frame: int = None,
    insert_keyframe: bool = True,
    subtract_gravity: bool = True,
    position_scale: float = 1.0,
) -> dict:
    """Apply sensor data for a single frame (for real-time streaming).
    
    Maintains internal state (velocity, orientation) per sensor across calls.
    Call this repeatedly with new sensor readings to animate the Null in real-time.
    
    Args:
        sensor_name: Name of the Null object
        accel: Accelerometer reading [ax, ay, az] in m/s²
        gyro: Gyroscope reading [gx, gy, gz] in rad/s
        mag: Optional magnetometer reading [mx, my, mz] in µT (needed for mix mode)
        mode: Processing mode — 'raw', 'double-integrate', or 'mix'
        alpha: Complementary filter weight (mix mode only, default: 0.98)
        dt: Time delta from previous sample in seconds (default: 0.01)
        frame: Blender frame number (default: current frame)
        insert_keyframe: Whether to insert a keyframe (default: True)
        subtract_gravity: Subtract gravity from accelerometer (default: True)
        position_scale: Scale factor for position (default: 1.0)
    """
    # Get or create sensor state
    if sensor_name not in _sensor_states:
        _sensor_states[sensor_name] = SensorState()
    state = _sensor_states[sensor_name]

    active_mode = normalize_sensor_mode(state.mode_override if state.mode_override is not None else mode)

    if active_mode == "raw":
        location = accel
        rotation = gyro
    elif active_mode == "integrate":
        ax, ay, az = accel
        if subtract_gravity:
            az -= state.gravity_mag

        # Apply deadband threshold
        if abs(ax) < state.deadband: ax = 0.0
        if abs(ay) < state.deadband: ay = 0.0
        if abs(az) < state.deadband: az = 0.0

        # Integrate velocity with damping to prevent drift
        state.velocity[0] = (state.velocity[0] + ax * dt) * state.damping
        state.velocity[1] = (state.velocity[1] + ay * dt) * state.damping
        state.velocity[2] = (state.velocity[2] + az * dt) * state.damping

        # Integrate position
        state.position[0] += state.velocity[0] * dt
        state.position[1] += state.velocity[1] * dt
        state.position[2] += state.velocity[2] * dt

        # Integrate gyro → angle
        state.pitch += gyro[0] * dt
        state.roll += gyro[1] * dt
        state.yaw += gyro[2] * dt

        location = list(state.position)
        rotation = [state.pitch, state.roll, state.yaw]
    elif active_mode == "fusion":
        ax, ay, az = accel
        gx, gy, gz = gyro

        # Accel-based angles
        accel_pitch = math.atan2(ay, math.sqrt(ax * ax + az * az))
        accel_roll = math.atan2(-ax, az)

        # Complementary filter
        state.pitch = alpha * (state.pitch + gx * dt) + (1.0 - alpha) * accel_pitch
        state.roll = alpha * (state.roll + gy * dt) + (1.0 - alpha) * accel_roll

        if mag:
            mx, my, mz = mag
            cos_p = math.cos(state.pitch)
            sin_p = math.sin(state.pitch)
            cos_r = math.cos(state.roll)
            sin_r = math.sin(state.roll)
            mx_comp = mx * cos_p + mz * sin_p
            my_comp = mx * sin_r * sin_p + my * cos_r - mz * sin_r * cos_p
            mag_yaw = math.atan2(-my_comp, mx_comp)
            state.yaw = alpha * (state.yaw + gz * dt) + (1.0 - alpha) * mag_yaw
        else:
            state.yaw += gz * dt

        # Position from world-frame accel
        cos_p = math.cos(state.pitch)
        sin_p = math.sin(state.pitch)
        cos_r = math.cos(state.roll)
        sin_r = math.sin(state.roll)
        cos_y = math.cos(state.yaw)
        sin_y = math.sin(state.yaw)

        ax_w = (cos_y * cos_p) * ax + (cos_y * sin_p * sin_r - sin_y * cos_r) * ay + (cos_y * sin_p * cos_r + sin_y * sin_r) * az
        ay_w = (sin_y * cos_p) * ax + (sin_y * sin_p * sin_r + cos_y * cos_r) * ay + (sin_y * sin_p * cos_r - cos_y * sin_r) * az
        az_w = (-sin_p) * ax + (cos_p * sin_r) * ay + (cos_p * cos_r) * az

        if subtract_gravity:
            az_w -= state.gravity_mag

        # Apply deadband threshold
        if abs(ax_w) < state.deadband: ax_w = 0.0
        if abs(ay_w) < state.deadband: ay_w = 0.0
        if abs(az_w) < state.deadband: az_w = 0.0

        # Integrate velocity with damping to prevent drift
        state.velocity[0] = (state.velocity[0] + ax_w * dt) * state.damping
        state.velocity[1] = (state.velocity[1] + ay_w * dt) * state.damping
        state.velocity[2] = (state.velocity[2] + az_w * dt) * state.damping

        state.position[0] += state.velocity[0] * dt
        state.position[1] += state.velocity[1] * dt
        state.position[2] += state.velocity[2] * dt

        location = list(state.position)
        rotation = [state.pitch, state.roll, state.yaw]
    else:
        return {"error": f"Unknown mode '{active_mode}'. Use 'raw', 'double-integrate', or 'mix'."}

    # Apply offset calibrations
    calibrated_location = [location[i] - state.pos_offset[i] for i in range(3)]
    calibrated_rotation = [
        rotation[0] - state.rot_offset[0],
        rotation[1] - state.rot_offset[1],
        rotation[2] - state.rot_offset[2]
    ]

    # Send to Blender
    params = {
        "sensor_name": sensor_name,
        "location": calibrated_location,
        "rotation": calibrated_rotation,
        "insert_keyframe": insert_keyframe,
        "position_scale": position_scale,
    }
    if frame is not None:
        params["frame"] = frame

    res = send_command("sensor_apply_data", params)

    # Handle feedback from Blender UI (Reset / Calibrate offsets & sync parameters)
    if isinstance(res, dict):
        if "gravity" in res:
            state.gravity_mag = res["gravity"]
        if "deadband" in res:
            state.deadband = res["deadband"]
        if "damping" in res:
            state.damping = res["damping"]
        if "mode" in res:
            state.mode_override = res["mode"]

        if res.get("reset_requested"):
            state.reset()
            res["location"] = [0.0, 0.0, 0.0]
            res["rotation"] = [0.0, 0.0, 0.0]
        elif res.get("init_requested"):
            state.pos_offset = list(state.position)
            state.rot_offset = [state.pitch, state.roll, state.yaw]
            res["location"] = [0.0, 0.0, 0.0]
            res["rotation"] = [0.0, 0.0, 0.0]

    return res


@mcp.tool()
def sensor_set_color_from_rgb(
    object_name: str,
    r: int,
    g: int,
    b: int,
) -> dict:
    """Set an object's material color from APDS-9960 RGB sensor data.
    
    Args:
        object_name: Name of the object to color
        r: Red value (0-255)
        g: Green value (0-255) 
        b: Blue value (0-255)
    """
    return send_command("sensor_set_color_from_rgb", {
        "object_name": object_name,
        "r": r,
        "g": g,
        "b": b,
    })


@mcp.tool()
def sensor_set_light_from_ambient(
    ambient_value: float,
    light_name: str = "Sensor_Light",
    max_energy: float = 1000.0,
    max_ambient: float = 65535,
) -> dict:
    """Set Blender scene lighting from APDS-9960 ambient light sensor.
    
    Args:
        ambient_value: Ambient light sensor reading
        light_name: Name of the light object (creates new if not found)
        max_energy: Maximum light energy in Blender (default: 1000)
        max_ambient: Maximum expected ambient sensor value (default: 65535)
    """
    return send_command("sensor_set_light_from_ambient", {
        "light_name": light_name,
        "ambient_value": ambient_value,
        "max_energy": max_energy,
        "max_ambient": max_ambient,
    })


@mcp.tool()
def sensor_set_altitude(
    sensor_name: str,
    pressure_hpa: float,
    sea_level_pressure: float = 1013.25,
    scale: float = 1.0,
    frame: int = None,
) -> dict:
    """Set a sensor Null's Z position from barometric pressure (LPS22HB).
    
    Calculates altitude using the barometric formula and maps it to the
    object's Z location.
    
    Args:
        sensor_name: Name of the Null object
        pressure_hpa: Barometric pressure reading in hPa
        sea_level_pressure: Reference sea-level pressure in hPa (default: 1013.25)
        scale: Scale factor for the altitude value (default: 1.0)
        frame: Optional frame number for keyframe insertion
    """
    return send_command("sensor_set_altitude", {
        "sensor_name": sensor_name,
        "pressure_hpa": pressure_hpa,
        "sea_level_pressure": sea_level_pressure,
        "scale": scale,
        "frame": frame,
    })


@mcp.tool()
def sensor_reset_state(sensor_name: str) -> dict:
    """Reset the integration state for a sensor (velocity, position, angles).
    
    Call this when starting a new recording or when drift becomes too large.
    
    Args:
        sensor_name: Name of the sensor to reset
    """
    if sensor_name in _sensor_states:
        _sensor_states[sensor_name].reset()
        return {"sensor": sensor_name, "state": "reset"}
    else:
        return {"sensor": sensor_name, "state": "no_state_found"}


# =============================================================================
# Resource
# =============================================================================

@mcp.resource("blender://scene")
def get_scene_resource() -> str:
    """Get current Blender scene summary as a resource."""
    result = send_command("get_scene_info")
    if result.get("error"):
        return f"Error: {result['error']}"

    lines = [
        f"Scene: {result.get('scene_name', 'Unknown')}",
        f"Frame: {result.get('frame_current', 0)} ({result.get('frame_start', 0)}-{result.get('frame_end', 250)})",
        f"Objects: {result.get('object_count', 0)}",
        "",
    ]

    for obj in result.get("objects", []):
        lines.append(f"  - {obj['name']} ({obj['type']}) at {obj.get('location', [0,0,0])}")

    return "\n".join(lines)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import sys
    transport = "stdio"
    for i, arg in enumerate(sys.argv):
        if arg == "--transport" and i + 1 < len(sys.argv):
            transport = sys.argv[i + 1]

    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
