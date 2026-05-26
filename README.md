# Blender MCP Sensor Bridge

โปรเจกต์นี้เชื่อม **Arduino Nano 33 BLE Sense** เข้ากับ **Blender** แบบ real-time ผ่าน Serial และ MCP server เพื่อเอาค่า sensor เช่น IMU, สี, แสง และความดัน ไปควบคุม object, light, material หรือ animation ใน Blender

เหมาะกับงาน prototype เช่น motion tracking, physical controller, data visualization, sensor-driven animation และงานทดลองด้าน interactive media

## ภาพรวมระบบ

```text
Arduino Nano 33 BLE Sense
  -> USB Serial
  -> serial_bridge.py
  -> server.py (MCP tools + sensor processing)
  -> TCP/JSON
  -> addon.py ใน Blender
  -> Empty/Object/Light/Material ใน scene
```

ไฟล์หลักใน repo:

| ไฟล์ | หน้าที่ |
|---|---|
| `addon.py` | Blender addon สำหรับเปิด server, รับคำสั่ง และ apply sensor data เข้า scene |
| `server.py` | MCP server และ sensor processing engine |
| `serial_bridge.py` | อ่าน Serial จาก Arduino แล้วส่งต่อเข้า MCP/Blender |
| `arduino_sensor_sender/arduino_sensor_sender.ino` | sketch สำหรับส่งค่า sensor จริงจาก Arduino |
| `arduino_sensor_scanner/arduino_sensor_scanner.ino` | sketch สำหรับสแกน I2C sensor ว่าบอร์ดมีชิปอะไรบ้าง |
| `arduino_sensor_test.ino` | sketch ส่ง dummy sine-wave data สำหรับทดสอบ pipeline โดยไม่ใช้ hardware sensor |

## Hardware ที่ทดสอบแล้ว

ทดสอบกับ:

```text
Arduino Nano 33 BLE Sense Rev1
```

ผลจาก `arduino_sensor_scanner`:

```text
Scanning Wire1...
  Found 0x1E  LSM9DS1 magnetometer (Nano 33 BLE/BLE Sense Rev1)
  Found 0x39  APDS-9960 gesture/color/proximity sensor
  Found 0x5C  LPS22HB barometer/temperature sensor
  Found 0x6B  LSM9DS1 accel/gyro (Nano 33 BLE/BLE Sense Rev1)
```

## Hardware Spec และ Sensor Map

### Nano 33 BLE Sense Rev1

| I2C address | Sensor | ใช้วัดอะไรได้ | Arduino library |
|---|---|---|---|
| `0x6B` | `LSM9DS1` accel/gyro | acceleration 3 แกน, angular velocity 3 แกน | `Arduino_LSM9DS1` |
| `0x1E` | `LSM9DS1` magnetometer | magnetic field 3 แกน ใช้ช่วยหา yaw/heading | `Arduino_LSM9DS1` |
| `0x39` | `APDS-9960` | gesture, proximity, ambient light, RGB color | `Arduino_APDS9960` |
| `0x5C` | `LPS22HB` | barometric pressure, ใช้คำนวณ altitude โดยประมาณได้ | `Arduino_LPS22HB` |
| `0x5F` | `HTS221` | temperature, relative humidity | `Arduino_HTS221` |

### Nano 33 BLE Sense Rev2

| I2C address | Sensor | ใช้วัดอะไรได้ | Arduino library |
|---|---|---|---|
| `0x68` หรือ `0x69` | `BMI270` | acceleration 3 แกน, gyroscope 3 แกน | `Arduino_BMI270_BMM150` |
| `0x10` | `BMM150` | magnetometer 3 แกน | `Arduino_BMI270_BMM150` |
| `0x39` | `APDS-9960` | gesture, proximity, ambient light, RGB color | `Arduino_APDS9960` |
| `0x5C` | `LPS22HB` | barometric pressure, altitude โดยประมาณ | `Arduino_LPS22HB` |
| `0x44` | `HS3003` | temperature, relative humidity | `Arduino_HS300x` |

หมายเหตุ: address อาจต่างเล็กน้อยตาม board revision หรือ external module ที่ต่อเพิ่ม ให้ใช้ scanner ใน repo นี้เป็นตัวตัดสินก่อนเลือก library

## Software Requirements

- macOS, Windows หรือ Linux
- Blender 3.x/4.x
- Python 3.10+
- `uv` หรือ `pip`
- Arduino IDE 2.x
- Arduino board package: **Arduino Mbed OS Nano Boards**
- Python dependencies:
  - `mcp[cli]`
  - `pyserial`

## Installation

### 1. Clone repo และติดตั้ง Python dependencies

```bash
git clone <your-repo-url>
cd MCP
uv sync
```

ถ้าไม่ได้ใช้ `uv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]" pyserial
```

### 2. ติดตั้ง Blender addon

1. เปิด Blender
2. ไปที่ `Edit > Preferences > Add-ons`
3. กด `Install...`
4. เลือกไฟล์ `addon.py`
5. Enable addon ชื่อ `Blender MCP Server`
6. เปิด Sidebar ใน Viewport ด้วยปุ่ม `N`
7. ไปที่ tab `MCP`
8. กด `Start Server`

### 3. ติดตั้ง Arduino libraries

เปิด Arduino IDE แล้วติดตั้งจาก `Library Manager`

สำหรับ Rev1:

```text
Arduino_LSM9DS1
Arduino_APDS9960
Arduino_LPS22HB
Arduino_HTS221
```

สำหรับ Rev2:

```text
Arduino_BMI270_BMM150
Arduino_APDS9960
Arduino_LPS22HB
Arduino_HS300x
```

## เช็คก่อนว่าบอร์ดมี Sensor อะไร

แนะนำให้เริ่มจาก scanner ก่อนเสมอ

1. เปิด `arduino_sensor_scanner/arduino_sensor_scanner.ino`
2. เลือก board ใน Arduino IDE เป็น `Arduino Nano 33 BLE`
3. เลือก port ให้ถูก
4. Upload
5. เปิด Serial Monitor ที่ `115200 baud`

ถ้าเจอ:

```text
0x6B LSM9DS1 accel/gyro
0x1E LSM9DS1 magnetometer
```

ให้ตั้ง sender เป็น:

```cpp
#define SENSOR_BOARD_REV 1
```

ถ้าเจอ:

```text
0x68 BMI270 accel/gyro
0x10 BMM150 magnetometer
```

ให้ตั้ง sender เป็น:

```cpp
#define SENSOR_BOARD_REV 2
```

ถ้าไม่เจอ address กลุ่มนี้เลย บอร์ดอาจไม่ใช่รุ่นที่มี IMU ในตัว หรือเลือก board target ใน Arduino IDE ไม่ตรง

## Upload Arduino Sensor Sender

เปิดไฟล์:

```text
arduino_sensor_sender/arduino_sensor_sender.ino
```

ตั้ง board revision ด้านบนของไฟล์:

```cpp
#define SENSOR_BOARD_REV 1
```

จากนั้น Upload ลง Arduino แล้วเปิด Serial Monitor ที่ `115200 baud`

ตัวอย่าง CSV ที่ควรเห็น:

```csv
ax,ay,az,gx,gy,gz,mx,my,mz,r,g,b,amb,press
0.0000,0.0000,9.8067,0.0000,0.0000,0.0000,12.34,-3.21,40.00,128,128,128,500,1013.25
```

หน่วยที่ส่งออก:

| column | ความหมาย | หน่วย |
|---|---|---|
| `ax, ay, az` | accelerometer | m/s^2 |
| `gx, gy, gz` | gyroscope | rad/s |
| `mx, my, mz` | magnetometer | microtesla โดยประมาณ |
| `r, g, b` | color sensor | 0-255 |
| `amb` | ambient light | raw sensor value |
| `press` | pressure | hPa |

## Run Real-Time Bridge

ก่อนรัน bridge ให้ปิด Arduino Serial Monitor ก่อน เพราะ serial port ใช้พร้อมกันไม่ได้

ใน Blender:

1. เปิด addon panel
2. กด `Start Server`
3. สร้าง Empty/Null ชื่อ `Sensor_IMU` หรือใช้ MCP tool `sensor_create_null`

จาก terminal:

```bash
uv run serial_bridge.py --port /dev/cu.usbmodem101 --mode mix --sensor-name Sensor_IMU
```

ถ้าไม่รู้ port:

```bash
uv run serial_bridge.py --mode mix --sensor-name Sensor_IMU
```

bridge จะสแกน port ให้เลือก

## Processing Modes

เลือกได้ทั้งใน Blender addon และผ่าน `serial_bridge.py --mode`

| Mode | ทำอะไร | เหมาะกับ |
|---|---|---|
| `raw` | map accel เป็น location และ gyro เป็น rotation ตรง ๆ | debug ว่า sensor มีค่าเปลี่ยนไหม |
| `double-integrate` | integrate gyro เป็น rotation และ double-integrate accel เป็น position | movement สั้น ๆ, ทดลอง position จาก acceleration |
| `mix` | ใช้ complementary filter ผสม accel/gyro/mag สำหรับ rotation แล้ว integrate position ใน world frame | ใช้งานจริง แนะนำเริ่มที่ mode นี้ |

ชื่อเก่ายังใช้ได้เพื่อ backward compatibility:

```text
integrate -> double-integrate
fusion    -> mix
```

## Blender Sensor Controls

ใน Blender addon panel มีค่าหลัก:

| Control | มีผลกับ mode | คำอธิบาย |
|---|---|---|
| `Mode` | ทุก mode | เลือก `raw`, `double-integrate`, `mix` |
| `Gravity` | `double-integrate`, `mix` | ค่า gravity ที่หักออกจาก acceleration ค่าเริ่มต้น `9.80665` |
| `Deadband Threshold` | `double-integrate`, `mix` | ตัด noise ของ acceleration ค่าน้อยไวกว่า แต่ drift ง่าย |
| `Velocity Damping` | `double-integrate`, `mix` | ลดความเร็วสะสมเพื่อกัน drift ค่า `1.0` ไม่หน่วง, ค่าเล็กลงหยุดไวขึ้น |
| `Set Init` | ทุก mode | ตั้งตำแหน่ง/มุมปัจจุบันเป็น zero reference |
| `Reset State` | ทุก mode | reset velocity, position และ rotation state |

ค่าแนะนำเริ่มต้น:

```text
Mode: mix
Gravity: 9.80665
Deadband Threshold: 0.20
Velocity Damping: 0.92
```

ค่าพวกนี้เปลี่ยนระหว่างรันได้ bridge จะรับค่ากลับจาก Blender แล้วมีผลกับ sample ถัดไป

## Extra Sensor Sync

ถ้า sketch ส่งค่า extra sensor มา bridge สามารถ sync เข้า Blender ได้:

```bash
uv run serial_bridge.py --mode mix --sync-rgb --rgb-target Cube
uv run serial_bridge.py --mode mix --sync-light --light-target Sensor_Light
uv run serial_bridge.py --mode mix --sync-altitude
```

Options สำคัญ:

| Option | ทำอะไร |
|---|---|
| `--latency <ms>` | ใส่ latency buffer สำหรับ sync กับกล้องหรือ video |
| `--record` | insert keyframe ระหว่าง streaming |
| `--position-scale <n>` | ขยาย/ลด movement ใน Blender |
| `--no-gravity` | ไม่หัก gravity ออกจาก accelerometer |
| `--format csv/json` | เลือกรูปแบบ serial input |

## Troubleshooting

### Serial port busy

ถ้าเจอ:

```text
Resource busy: '/dev/cu.usbmodem101'
```

ให้ปิด Arduino Serial Monitor / Serial Plotter ก่อน แล้วรัน bridge ใหม่

ตรวจ process ที่ใช้ port:

```bash
lsof /dev/cu.usbmodem101
```

### ค่า sensor เป็น 0 ตลอด

1. Upload `arduino_sensor_scanner` ก่อน
2. ดูว่าเจอ `0x6B/0x1E` หรือ `0x68/0x10`
3. ตั้ง `SENSOR_BOARD_REV` ให้ตรง
4. Upload `arduino_sensor_sender` ใหม่
5. ลองเอียงบอร์ด 90 องศา ค่า `ax/ay/az` ควรเปลี่ยน
6. ค่า `gx/gy/gz` จะเปลี่ยนเฉพาะตอนหมุนหรือสะบัดบอร์ด

### Blender ไม่ขยับ

เช็คตามลำดับ:

1. Blender addon กด `Start Server` แล้ว
2. มี object ชื่อเดียวกับ `--sensor-name`
3. port ไม่ถูก Arduino IDE จับอยู่
4. terminal bridge ขึ้น `[Active]`
5. ลอง mode `raw` เพื่อดูค่าดิบก่อน

## MCP Client Config

ตัวอย่าง config สำหรับ Claude Desktop, Cursor หรือ client ที่รองรับ MCP:

```json
{
  "mcpServers": {
    "blender": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/MCP", "run", "server.py"]
    }
  }
}
```

## Development

```bash
# syntax check
python3 -m py_compile addon.py server.py serial_bridge.py

# MCP inspector
uv run mcp dev server.py
```

## References

- [Arduino Help Center: Use the built-in sensors on Nano 33 BLE Sense](https://support.arduino.cc/hc/en-us/articles/360014654820-Use-the-built-in-sensors-on-Nano-33-BLE-Sense)
- [Arduino Help Center: Test the sensors on Nano 33 BLE Sense](https://support.arduino.cc/hc/en-us/articles/4407057391506-Test-the-sensors-on-Nano-33-BLE-Sense)
- [Arduino Docs: Nano 33 BLE Sense Rev2](https://docs.arduino.cc/hardware/nano-33-ble-sense-rev2/)
- [Arduino Docs: Nano 33 BLE Sense Rev2 user manual](https://docs.arduino.cc/tutorials/nano-33-ble-sense-rev2/cheat-sheet)

## License

MIT
