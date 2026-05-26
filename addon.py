"""
Blender MCP Addon — TCP Socket Server for AI-driven Blender control.

This addon creates a TCP socket server inside Blender that listens for
JSON commands from the MCP server and executes them on Blender's main thread.

Install: Edit > Preferences > Add-ons > Install > select this file
"""

bl_info = {
    "name": "Blender MCP Server",
    "author": "Blender MCP",
    "description": "MCP (Model Context Protocol) server for AI-driven Blender control with sensor data visualization",
    "blender": (3, 6, 0),
    "version": (1, 0, 0),
    "location": "View3D > Sidebar > MCP",
    "category": "Interface",
}

import bpy
import json
import socket
import threading
import traceback
import base64
import os
import tempfile
import math
import struct
from mathutils import Vector, Euler, Matrix


# =============================================================================
# Global State
# =============================================================================

_server_socket = None
_server_thread = None
_running = False
_command_queue = []
_result_map = {}
_lock = threading.Lock()
_command_id = 0


# =============================================================================
# Command Handlers
# =============================================================================

def handle_get_scene_info(params):
    """Get comprehensive scene information."""
    scene = bpy.context.scene
    objects_info = []
    for obj in scene.objects:
        obj_data = {
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
            "rotation": list(obj.rotation_euler),
            "scale": list(obj.scale),
            "visible": obj.visible_get(),
        }
        if obj.active_material:
            obj_data["material"] = obj.active_material.name
        objects_info.append(obj_data)

    materials = [{"name": m.name} for m in bpy.data.materials]
    cameras = [{"name": c.name, "location": list(c.location)} for c in bpy.data.objects if c.type == 'CAMERA']
    lights = [{"name": l.name, "type": l.data.type, "energy": l.data.energy} for l in bpy.data.objects if l.type == 'LIGHT']

    return {
        "scene_name": scene.name,
        "frame_current": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "object_count": len(objects_info),
        "objects": objects_info,
        "materials": materials,
        "cameras": cameras,
        "lights": lights,
    }


def handle_get_object_info(params):
    """Get detailed information about a specific object."""
    name = params.get("name")
    if not name:
        return {"error": "Parameter 'name' is required"}

    obj = bpy.data.objects.get(name)
    if not obj:
        return {"error": f"Object '{name}' not found"}

    info = {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "visible": obj.visible_get(),
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
    }

    # Mesh info
    if obj.type == 'MESH' and obj.data:
        mesh = obj.data
        info["mesh"] = {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        }

    # Materials
    if obj.data and hasattr(obj.data, 'materials'):
        info["materials"] = [m.name for m in obj.data.materials if m]

    # Modifiers
    info["modifiers"] = [{"name": m.name, "type": m.type} for m in obj.modifiers]

    # Empty specific
    if obj.type == 'EMPTY':
        info["empty_display_type"] = obj.empty_display_type
        info["empty_display_size"] = obj.empty_display_size

    return info


def handle_create_object(params):
    """Create a primitive object."""
    obj_type = params.get("type", "cube").lower()
    location = params.get("location", [0, 0, 0])
    scale = params.get("scale", [1, 1, 1])
    name = params.get("name")

    # Deselect all
    bpy.ops.object.select_all(action='DESELECT')

    primitives = {
        "cube": lambda: bpy.ops.mesh.primitive_cube_add(size=2, location=location),
        "sphere": lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=location),
        "cylinder": lambda: bpy.ops.mesh.primitive_cylinder_add(radius=1, depth=2, location=location),
        "plane": lambda: bpy.ops.mesh.primitive_plane_add(size=2, location=location),
        "cone": lambda: bpy.ops.mesh.primitive_cone_add(radius1=1, depth=2, location=location),
        "torus": lambda: bpy.ops.mesh.primitive_torus_add(location=location),
        "monkey": lambda: bpy.ops.mesh.primitive_monkey_add(size=2, location=location),
        "ico_sphere": lambda: bpy.ops.mesh.primitive_ico_sphere_add(radius=1, location=location),
    }

    create_fn = primitives.get(obj_type)
    if not create_fn:
        return {"error": f"Unknown type '{obj_type}'. Available: {list(primitives.keys())}"}

    create_fn()
    obj = bpy.context.active_object
    obj.scale = scale

    if name:
        obj.name = name

    return {"name": obj.name, "type": obj.type, "location": list(obj.location)}


def handle_modify_object(params):
    """Modify an existing object's transform."""
    name = params.get("name")
    if not name:
        return {"error": "Parameter 'name' is required"}

    obj = bpy.data.objects.get(name)
    if not obj:
        return {"error": f"Object '{name}' not found"}

    if "location" in params:
        obj.location = params["location"]
    if "rotation" in params:
        obj.rotation_euler = [math.radians(a) if params.get("rotation_unit", "deg") == "deg" else a for a in params["rotation"]]
    if "scale" in params:
        obj.scale = params["scale"]
    if "new_name" in params:
        obj.name = params["new_name"]

    return {"name": obj.name, "location": list(obj.location), "rotation": list(obj.rotation_euler), "scale": list(obj.scale)}


def handle_delete_object(params):
    """Delete an object from the scene."""
    name = params.get("name")
    if not name:
        return {"error": "Parameter 'name' is required"}

    obj = bpy.data.objects.get(name)
    if not obj:
        return {"error": f"Object '{name}' not found"}

    bpy.data.objects.remove(obj, do_unlink=True)
    return {"deleted": name}


def handle_set_material(params):
    """Create or assign a material to an object."""
    obj_name = params.get("object_name")
    if not obj_name:
        return {"error": "Parameter 'object_name' is required"}

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    mat_name = params.get("material_name", f"Material_{obj_name}")
    color = params.get("color", [0.8, 0.8, 0.8, 1.0])
    metallic = params.get("metallic", 0.0)
    roughness = params.get("roughness", 0.5)

    # Create or get material
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        # Ensure color has 4 components
        if len(color) == 3:
            color = color + [1.0]
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness

    # Assign to object
    if obj.data and hasattr(obj.data, 'materials'):
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

    return {"object": obj_name, "material": mat_name, "color": color}


def handle_add_modifier(params):
    """Add a modifier to an object."""
    obj_name = params.get("object_name")
    if not obj_name:
        return {"error": "Parameter 'object_name' is required"}

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    mod_type = params.get("modifier_type", "SUBSURF").upper()
    mod_name = params.get("modifier_name", mod_type.title())

    modifier_types = {
        "SUBSURF": "SUBSURF",
        "SUBDIVISION": "SUBSURF",
        "BEVEL": "BEVEL",
        "SOLIDIFY": "SOLIDIFY",
        "MIRROR": "MIRROR",
        "ARRAY": "ARRAY",
        "BOOLEAN": "BOOLEAN",
        "DECIMATE": "DECIMATE",
        "WIREFRAME": "WIREFRAME",
        "SMOOTH": "SMOOTH",
    }

    bl_type = modifier_types.get(mod_type)
    if not bl_type:
        return {"error": f"Unknown modifier '{mod_type}'. Available: {list(modifier_types.keys())}"}

    mod = obj.modifiers.new(name=mod_name, type=bl_type)

    # Apply modifier-specific params
    mod_params = params.get("params", {})
    if bl_type == "SUBSURF":
        mod.levels = mod_params.get("levels", 2)
        mod.render_levels = mod_params.get("render_levels", 2)
    elif bl_type == "BEVEL":
        mod.width = mod_params.get("width", 0.1)
        mod.segments = mod_params.get("segments", 3)
    elif bl_type == "SOLIDIFY":
        mod.thickness = mod_params.get("thickness", 0.1)
    elif bl_type == "ARRAY":
        mod.count = mod_params.get("count", 3)
    elif bl_type == "MIRROR":
        mod.use_axis = [mod_params.get("x", True), mod_params.get("y", False), mod_params.get("z", False)]

    return {"object": obj_name, "modifier": mod.name, "type": bl_type}


def handle_set_camera(params):
    """Set camera position, rotation, and properties."""
    cam_name = params.get("name", "Camera")
    cam = bpy.data.objects.get(cam_name)

    if not cam or cam.type != 'CAMERA':
        # Create camera
        cam_data = bpy.data.cameras.new(name=cam_name)
        cam = bpy.data.objects.new(name=cam_name, object_data=cam_data)
        bpy.context.scene.collection.objects.link(cam)

    if "location" in params:
        cam.location = params["location"]
    if "rotation" in params:
        cam.rotation_euler = [math.radians(a) for a in params["rotation"]]
    if "focal_length" in params:
        cam.data.lens = params["focal_length"]
    if "target" in params:
        # Point camera at target
        target = Vector(params["target"])
        direction = target - cam.location
        rot_quat = direction.to_track_quat('-Z', 'Y')
        cam.rotation_euler = rot_quat.to_euler()

    # Set as active camera
    if params.get("set_active", True):
        bpy.context.scene.camera = cam

    return {"name": cam.name, "location": list(cam.location), "rotation": list(cam.rotation_euler)}


def handle_set_light(params):
    """Create or modify a light."""
    light_name = params.get("name", "Light")
    light_type = params.get("type", "POINT").upper()
    obj = bpy.data.objects.get(light_name)

    valid_types = {"POINT", "SUN", "SPOT", "AREA"}
    if light_type not in valid_types:
        return {"error": f"Unknown light type '{light_type}'. Available: {list(valid_types)}"}

    if not obj or obj.type != 'LIGHT':
        light_data = bpy.data.lights.new(name=light_name, type=light_type)
        obj = bpy.data.objects.new(name=light_name, object_data=light_data)
        bpy.context.scene.collection.objects.link(obj)
    else:
        obj.data.type = light_type

    if "location" in params:
        obj.location = params["location"]
    if "rotation" in params:
        obj.rotation_euler = [math.radians(a) for a in params["rotation"]]
    if "energy" in params:
        obj.data.energy = params["energy"]
    if "color" in params:
        obj.data.color = params["color"][:3]

    return {"name": obj.name, "type": light_type, "energy": obj.data.energy}


def handle_render_scene(params):
    """Render the scene and return as base64."""
    output_path = params.get("output_path", os.path.join(tempfile.gettempdir(), "blender_mcp_render.png"))
    resolution_x = params.get("resolution_x", 1920)
    resolution_y = params.get("resolution_y", 1080)
    samples = params.get("samples", 128)

    scene = bpy.context.scene
    scene.render.resolution_x = resolution_x
    scene.render.resolution_y = resolution_y
    scene.render.filepath = output_path
    scene.render.image_settings.file_format = 'PNG'

    if scene.render.engine == 'CYCLES':
        scene.cycles.samples = samples

    bpy.ops.render.render(write_still=True)

    # Read and encode
    if os.path.exists(output_path):
        with open(output_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode('utf-8')
        return {"image_base64": img_data, "path": output_path, "resolution": [resolution_x, resolution_y]}
    else:
        return {"error": "Render failed — output file not found"}


def handle_execute_code(params):
    """Execute arbitrary Python code in Blender."""
    code = params.get("code")
    if not code:
        return {"error": "Parameter 'code' is required"}

    # Capture output
    import io
    import sys

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    result = None
    try:
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        exec_globals = {"bpy": bpy, "math": math, "Vector": Vector, "Euler": Euler, "Matrix": Matrix}
        exec(code, exec_globals)
        result = {
            "success": True,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }
    except Exception as e:
        result = {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return result


def handle_get_polyhaven_asset(params):
    """Download and import a Poly Haven asset."""
    import urllib.request

    asset_name = params.get("name")
    asset_type = params.get("type", "hdri")  # hdri, texture, model
    resolution = params.get("resolution", "1k")

    if not asset_name:
        return {"error": "Parameter 'name' is required"}

    base_url = "https://dl.polyhaven.org/file/ph-assets"
    download_dir = os.path.join(tempfile.gettempdir(), "polyhaven_mcp")
    os.makedirs(download_dir, exist_ok=True)

    try:
        if asset_type == "hdri":
            url = f"{base_url}/HDRIs/{asset_name}/{resolution}/{asset_name}_{resolution}.hdr"
            filepath = os.path.join(download_dir, f"{asset_name}_{resolution}.hdr")
            urllib.request.urlretrieve(url, filepath)

            # Apply as world HDRI
            world = bpy.context.scene.world
            if not world:
                world = bpy.data.worlds.new("World")
                bpy.context.scene.world = world
            world.use_nodes = True
            nodes = world.node_tree.nodes
            links = world.node_tree.links

            nodes.clear()
            bg = nodes.new('ShaderNodeBackground')
            env = nodes.new('ShaderNodeTexEnvironment')
            output = nodes.new('ShaderNodeOutputWorld')
            env.image = bpy.data.images.load(filepath)
            links.new(env.outputs['Color'], bg.inputs['Color'])
            links.new(bg.outputs['Background'], output.inputs['Surface'])

            return {"success": True, "type": "hdri", "path": filepath}

        elif asset_type == "texture":
            suffixes = {"diffuse": "diff", "normal": "nor_gl", "roughness": "rough", "displacement": "disp"}
            files = {}
            for tex_type, suffix in suffixes.items():
                url = f"{base_url}/Textures/{asset_name}/{resolution}/{asset_name}_{suffix}_{resolution}.jpg"
                filepath = os.path.join(download_dir, f"{asset_name}_{suffix}_{resolution}.jpg")
                try:
                    urllib.request.urlretrieve(url, filepath)
                    files[tex_type] = filepath
                except Exception:
                    pass

            return {"success": True, "type": "texture", "files": files}

        else:
            return {"error": f"Asset type '{asset_type}' not yet supported. Use 'hdri' or 'texture'."}

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# =============================================================================
# Sensor Command Handlers
# =============================================================================

def handle_sensor_create_null(params):
    """Create a Null (Empty) object to represent a sensor."""
    name = params.get("name", "Sensor_IMU")
    display_type = params.get("display_type", "PLAIN_AXES")
    display_size = params.get("display_size", 1.0)
    location = params.get("location", [0, 0, 0])
    color = params.get("color", [1.0, 0.5, 0.0, 1.0])

    valid_display_types = {"PLAIN_AXES", "ARROWS", "SINGLE_ARROW", "CIRCLE", "CUBE", "SPHERE", "CONE"}
    if display_type not in valid_display_types:
        return {"error": f"Unknown display_type '{display_type}'. Available: {list(valid_display_types)}"}

    bpy.ops.object.empty_add(type=display_type, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.empty_display_size = display_size
    obj.color = color

    return {"name": obj.name, "display_type": display_type, "location": list(obj.location)}


def handle_sensor_import_log(params):
    """Import sensor log data and create keyframe animation on a Null object.
    
    Processes accelerometer/gyroscope/magnetometer data with 3 modes:
    - raw: direct mapping
    - double-integrate: double-integrate accel, single-integrate gyro
    - mix: complementary accel/gyro/mag filter
    """
    sensor_name = params.get("sensor_name")
    data = params.get("data")  # Pre-parsed data from MCP server

    if not sensor_name:
        return {"error": "Parameter 'sensor_name' is required"}
    if not data:
        return {"error": "Parameter 'data' is required (pre-parsed from MCP server)"}

    obj = bpy.data.objects.get(sensor_name)
    if not obj:
        return {"error": f"Object '{sensor_name}' not found"}

    fps = params.get("fps", 24)
    position_scale = params.get("position_scale", 1.0)

    # Clear existing animation
    if obj.animation_data:
        obj.animation_data_clear()

    frame_count = 0
    for point in data:
        frame = int(point["frame"])
        loc = point.get("location", [0, 0, 0])
        rot = point.get("rotation", [0, 0, 0])

        obj.location = (loc[0] * position_scale, loc[1] * position_scale, loc[2] * position_scale)
        obj.rotation_euler = rot
        obj.keyframe_insert(data_path="location", frame=frame)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
        frame_count += 1

    # Set scene frame range
    if data:
        scene = bpy.context.scene
        scene.frame_start = int(data[0]["frame"])
        scene.frame_end = int(data[-1]["frame"])

    return {
        "sensor": sensor_name,
        "frames_inserted": frame_count,
        "frame_range": [bpy.context.scene.frame_start, bpy.context.scene.frame_end],
    }


def handle_sensor_apply_data(params):
    """Apply sensor data for a single frame."""
    sensor_name = params.get("sensor_name")
    if not sensor_name:
        return {"error": "Parameter 'sensor_name' is required"}

    obj = bpy.data.objects.get(sensor_name)
    if not obj:
        return {"error": f"Object '{sensor_name}' not found"}

    location = params.get("location", [0, 0, 0])
    rotation = params.get("rotation", [0, 0, 0])
    frame = params.get("frame", bpy.context.scene.frame_current)
    insert_keyframe = params.get("insert_keyframe", True)
    position_scale = params.get("position_scale", 1.0)

    obj.location = (location[0] * position_scale, location[1] * position_scale, location[2] * position_scale)
    obj.rotation_euler = rotation

    if insert_keyframe:
        obj.keyframe_insert(data_path="location", frame=frame)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)

    # Read and clear reset/init flags requested from UI
    scene = bpy.context.scene
    reset_requested = getattr(scene, "mcp_reset_requested", False)
    init_requested = getattr(scene, "mcp_init_requested", False)

    if reset_requested:
        scene.mcp_reset_requested = False
    if init_requested:
        scene.mcp_init_requested = False

    res = {
        "sensor": sensor_name,
        "frame": frame,
        "location": list(obj.location),
        "rotation": list(obj.rotation_euler),
        "gravity": getattr(scene, "mcp_gravity", 9.80665),
        "deadband": getattr(scene, "mcp_deadband", 0.15),
        "damping": getattr(scene, "mcp_damping", 0.95),
        "mode": getattr(scene, "mcp_sensor_mode", "mix"),
    }
    if reset_requested:
        res["reset_requested"] = True
    if init_requested:
        res["init_requested"] = True
    return res


def handle_sensor_set_color_from_rgb(params):
    """Set material color from RGB sensor data (APDS-9960)."""
    obj_name = params.get("object_name")
    if not obj_name:
        return {"error": "Parameter 'object_name' is required"}

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    r = params.get("r", 128) / 255.0
    g = params.get("g", 128) / 255.0
    b = params.get("b", 128) / 255.0

    mat_name = f"SensorColor_{obj_name}"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)

    if obj.data and hasattr(obj.data, 'materials'):
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

    return {"object": obj_name, "color_rgb": [r, g, b]}


def handle_sensor_set_light_from_ambient(params):
    """Set Blender light energy from ambient light sensor value."""
    light_name = params.get("light_name", "Sensor_Light")
    ambient_value = params.get("ambient_value", 0)
    max_energy = params.get("max_energy", 1000.0)

    obj = bpy.data.objects.get(light_name)
    if not obj or obj.type != 'LIGHT':
        light_data = bpy.data.lights.new(name=light_name, type='POINT')
        obj = bpy.data.objects.new(name=light_name, object_data=light_data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (0, 0, 5)

    # Map ambient value (0-65535 typical for APDS-9960) to energy
    max_ambient = params.get("max_ambient", 65535)
    energy = (ambient_value / max_ambient) * max_energy
    obj.data.energy = energy

    return {"light": light_name, "energy": energy, "ambient_value": ambient_value}


def handle_sensor_set_altitude(params):
    """Set object Z location from barometric pressure."""
    sensor_name = params.get("sensor_name")
    if not sensor_name:
        return {"error": "Parameter 'sensor_name' is required"}

    obj = bpy.data.objects.get(sensor_name)
    if not obj:
        return {"error": f"Object '{sensor_name}' not found"}

    pressure_hpa = params.get("pressure_hpa", 1013.25)
    sea_level_pressure = params.get("sea_level_pressure", 1013.25)
    scale = params.get("scale", 1.0)

    # Barometric altitude formula
    altitude = 44330.0 * (1.0 - (pressure_hpa / sea_level_pressure) ** 0.1903)
    obj.location.z = altitude * scale

    frame = params.get("frame")
    if frame is not None:
        obj.keyframe_insert(data_path="location", frame=frame)

    return {"sensor": sensor_name, "altitude_m": altitude, "location_z": obj.location.z}


# =============================================================================
# Command Dispatcher
# =============================================================================

COMMAND_HANDLERS = {
    "get_scene_info": handle_get_scene_info,
    "get_object_info": handle_get_object_info,
    "create_object": handle_create_object,
    "modify_object": handle_modify_object,
    "delete_object": handle_delete_object,
    "set_material": handle_set_material,
    "add_modifier": handle_add_modifier,
    "set_camera": handle_set_camera,
    "set_light": handle_set_light,
    "render_scene": handle_render_scene,
    "execute_code": handle_execute_code,
    "get_polyhaven_asset": handle_get_polyhaven_asset,
    # Sensor commands
    "sensor_create_null": handle_sensor_create_null,
    "sensor_import_log": handle_sensor_import_log,
    "sensor_apply_data": handle_sensor_apply_data,
    "sensor_set_color_from_rgb": handle_sensor_set_color_from_rgb,
    "sensor_set_light_from_ambient": handle_sensor_set_light_from_ambient,
    "sensor_set_altitude": handle_sensor_set_altitude,
}


def execute_command(command, params):
    """Execute a command and return the result."""
    handler = COMMAND_HANDLERS.get(command)
    if not handler:
        return {"error": f"Unknown command '{command}'. Available: {list(COMMAND_HANDLERS.keys())}"}

    try:
        return handler(params or {})
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# =============================================================================
# TCP Socket Server
# =============================================================================

def process_timer():
    """Timer callback to process commands on main thread."""
    global _command_queue, _result_map

    with _lock:
        queue_copy = list(_command_queue)
        _command_queue.clear()

    for cmd_id, command, params in queue_copy:
        result = execute_command(command, params)
        with _lock:
            _result_map[cmd_id] = result

    if _running:
        return 0.01  # Run every 10ms (100Hz) for real-time responsiveness
    return None  # Stop timer


def handle_client(client_socket, addr):
    """Handle a connected client."""
    global _command_id

    print(f"[MCP Addon] Client connected: {addr}")
    buffer = b""

    try:
        while _running:
            data = client_socket.recv(65536)
            if not data:
                break

            buffer += data

            # Process complete messages (newline-delimited JSON)
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if not line.strip():
                    continue

                try:
                    request = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError as e:
                    response = {"error": f"Invalid JSON: {str(e)}"}
                    client_socket.sendall((json.dumps(response) + '\n').encode('utf-8'))
                    continue

                command = request.get("command")
                params = request.get("params", {})

                # Queue command for main thread execution
                with _lock:
                    _command_id += 1
                    cmd_id = _command_id
                    _command_queue.append((cmd_id, command, params))

                # Wait for result (with timeout)
                import time
                timeout = 60  # 60 second timeout (for renders)
                start_time = time.time()
                result = None

                while time.time() - start_time < timeout:
                    with _lock:
                        if cmd_id in _result_map:
                            result = _result_map.pop(cmd_id)
                            break
                    time.sleep(0.01)

                if result is None:
                    result = {"error": "Command execution timed out (60s)"}

                response_bytes = (json.dumps(result, default=str) + '\n').encode('utf-8')
                client_socket.sendall(response_bytes)

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[MCP Addon] Client disconnected: {addr} ({e})")
    finally:
        client_socket.close()
        print(f"[MCP Addon] Client closed: {addr}")


def server_loop(host, port):
    """Main server loop."""
    global _server_socket, _running

    _server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server_socket.settimeout(1.0)

    try:
        _server_socket.bind((host, port))
        _server_socket.listen(5)
        print(f"[MCP Addon] Server listening on {host}:{port}")

        while _running:
            try:
                client_socket, addr = _server_socket.accept()
                client_thread = threading.Thread(target=handle_client, args=(client_socket, addr), daemon=True)
                client_thread.start()
            except socket.timeout:
                continue
            except OSError:
                break
    except Exception as e:
        print(f"[MCP Addon] Server error: {e}")
    finally:
        if _server_socket:
            _server_socket.close()
        print("[MCP Addon] Server stopped")


# =============================================================================
# Blender Operators & UI
# =============================================================================

class MCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "mcp.start_server"
    bl_label = "Start MCP Server"
    bl_description = "Start the MCP TCP socket server"

    def execute(self, context):
        global _server_thread, _running

        if _running:
            self.report({'WARNING'}, "Server is already running")
            return {'CANCELLED'}

        host = context.scene.mcp_host
        port = context.scene.mcp_port

        _running = True
        _server_thread = threading.Thread(target=server_loop, args=(host, port), daemon=True)
        _server_thread.start()

        # Register timer for main thread command processing
        bpy.app.timers.register(process_timer, first_interval=0.1)

        self.report({'INFO'}, f"MCP Server started on {host}:{port}")
        return {'FINISHED'}


class MCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "mcp.stop_server"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the MCP TCP socket server"

    def execute(self, context):
        global _running, _server_socket

        if not _running:
            self.report({'WARNING'}, "Server is not running")
            return {'CANCELLED'}

        _running = False
        if _server_socket:
            try:
                _server_socket.close()
            except Exception:
                pass

        self.report({'INFO'}, "MCP Server stopped")
        return {'FINISHED'}


class MCP_OT_ResetSensor(bpy.types.Operator):
    bl_idname = "mcp.reset_sensor"
    bl_label = "Reset Sensor State"
    bl_description = "Reset integration state (velocity, position) to zero"

    def execute(self, context):
        context.scene.mcp_reset_requested = True
        self.report({'INFO'}, "Reset sensor request sent")
        return {'FINISHED'}


class MCP_OT_SetSensorInit(bpy.types.Operator):
    bl_idname = "mcp.set_sensor_init"
    bl_label = "Set Init Offset"
    bl_description = "Tare current position/rotation as zero offset"

    def execute(self, context):
        context.scene.mcp_init_requested = True
        self.report({'INFO'}, "Set initial offset request sent")
        return {'FINISHED'}


class MCP_PT_Panel(bpy.types.Panel):
    bl_label = "MCP Server"
    bl_idname = "MCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Connection settings
        box = layout.box()
        box.label(text="Connection", icon='LINKED')
        row = box.row()
        row.prop(scene, "mcp_host", text="Host")
        row = box.row()
        row.prop(scene, "mcp_port", text="Port")

        # Status
        layout.separator()
        if _running:
            layout.label(text="● Server Running", icon='CHECKMARK')
            layout.operator("mcp.stop_server", text="Stop Server", icon='CANCEL')
            
            # Real-time Sensor Controls
            layout.separator()
            ctrl_box = layout.box()
            ctrl_box.label(text="Sensor Control", icon='EMPTY_DATA')
            
            # Calibration parameters
            col = ctrl_box.column(align=True)
            col.prop(scene, "mcp_sensor_mode", text="Mode")
            col.prop(scene, "mcp_gravity", text="Gravity (m/s²)")
            col.prop(scene, "mcp_deadband", text="Deadband Threshold")
            col.prop(scene, "mcp_damping", text="Velocity Damping")
            
            layout.separator()
            row = ctrl_box.row(align=True)
            row.operator("mcp.set_sensor_init", text="Set Init", icon='CENTER_ONLY')
            row.operator("mcp.reset_sensor", text="Reset State", icon='FILE_REFRESH')
        else:
            layout.label(text="○ Server Stopped", icon='X')
            layout.operator("mcp.start_server", text="Start Server", icon='PLAY')

        # Info
        layout.separator()
        box = layout.box()
        box.label(text="Info", icon='INFO')
        box.label(text=f"Commands: {len(COMMAND_HANDLERS)}")
        box.label(text=f"Sensor tools: 6")


# =============================================================================
# Registration
# =============================================================================

classes = (
    MCP_OT_StartServer,
    MCP_OT_StopServer,
    MCP_OT_ResetSensor,
    MCP_OT_SetSensorInit,
    MCP_PT_Panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.mcp_host = bpy.props.StringProperty(
        name="Host",
        default="127.0.0.1",
        description="Server host address"
    )
    bpy.types.Scene.mcp_port = bpy.props.IntProperty(
        name="Port",
        default=9876,
        min=1024,
        max=65535,
        description="Server port number"
    )
    bpy.types.Scene.mcp_reset_requested = bpy.props.BoolProperty(
        name="Reset Requested",
        default=False
    )
    bpy.types.Scene.mcp_init_requested = bpy.props.BoolProperty(
        name="Init Requested",
        default=False
    )
    bpy.types.Scene.mcp_gravity = bpy.props.FloatProperty(
        name="Gravity Magnitude",
        default=9.80665,
        description="Gravity constant value to subtract from Z axis (m/s²)"
    )
    bpy.types.Scene.mcp_deadband = bpy.props.FloatProperty(
        name="Accel Deadband",
        default=0.15,
        min=0.0,
        max=2.0,
        description="Noise threshold below which acceleration is zeroed out to prevent drift"
    )
    bpy.types.Scene.mcp_damping = bpy.props.FloatProperty(
        name="Vel Damping",
        default=0.95,
        min=0.0,
        max=1.0,
        description="Velocity damping factor per frame to decay drift speed"
    )
    bpy.types.Scene.mcp_sensor_mode = bpy.props.EnumProperty(
        name="Processing Mode",
        items=[
            ('raw', 'Raw', 'Direct sensor mapping'),
            ('double-integrate', 'Double-Integrate', 'Double-integrate accel to position, single-integrate gyro to rotation'),
            ('mix', 'Mix', 'Complementary accel/gyro/mag mix for rotation, world-frame double-integration for position')
        ],
        default='mix',
        description="Sensor processing mode"
    )


def unregister():
    global _running
    _running = False

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.mcp_host
    del bpy.types.Scene.mcp_port
    del bpy.types.Scene.mcp_reset_requested
    del bpy.types.Scene.mcp_init_requested
    del bpy.types.Scene.mcp_gravity
    del bpy.types.Scene.mcp_deadband
    del bpy.types.Scene.mcp_damping
    del bpy.types.Scene.mcp_sensor_mode


if __name__ == "__main__":
    register()
