/*
  UNO Q App Lab -> Blender sensor sender

  Hardware:
    - Arduino UNO Q
    - Arduino Modulino Movement connected via Qwiic

  App Lab setup:
    - Put this file in the Sketch tab.
    - Put main.py from this folder in the Python tab.

  Output format forwarded by main.py:
    DATA,ax_mps,ay_mps,az_mps,gx_rad,gy_rad,gz_rad,mx_uT,my_uT,mz_uT,r,g,b,amb,press_hpa
*/

#include <Arduino_RouterBridge.h>
#include <Arduino_Modulino.h>

ModulinoMovement imu;

const unsigned long SEND_INTERVAL_MS = 50;  // 20 Hz, same as the Nano sender
const float G_TO_MPS2 = 9.80665f;
const float DEG_TO_RAD_F = 0.017453292519943295f;

unsigned long lastSendTime = 0;

void setup() {
  Bridge.begin();
  Monitor.begin();

  Modulino.begin();
  imu.begin();

  Monitor.println("[UNO Q] Modulino Movement sender started.");
  Monitor.println("DATA_HEADER,ax_mps,ay_mps,az_mps,gx_rad,gy_rad,gz_rad,mx_uT,my_uT,mz_uT,r,g,b,amb,press_hpa");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSendTime < SEND_INTERVAL_MS) {
    return;
  }
  lastSendTime = now;

  imu.update();

  // Modulino Movement reports acceleration in g and gyro in deg/s.
  // Blender bridge expects acceleration in m/s^2 and gyro in rad/s.
  float ax_mps = imu.getX() * G_TO_MPS2;
  float ay_mps = imu.getY() * G_TO_MPS2;
  float az_mps = imu.getZ() * G_TO_MPS2;

  float gx_rad = imu.getRoll() * DEG_TO_RAD_F;
  float gy_rad = imu.getPitch() * DEG_TO_RAD_F;
  float gz_rad = imu.getYaw() * DEG_TO_RAD_F;

  Bridge.notify("motion_reading",
                ax_mps, ay_mps, az_mps,
                gx_rad, gy_rad, gz_rad);

  // App Lab console debug only. The Python side forwards the real UDP packet.
  Monitor.print("DATA,");
  Monitor.print(ax_mps, 4); Monitor.print(",");
  Monitor.print(ay_mps, 4); Monitor.print(",");
  Monitor.print(az_mps, 4); Monitor.print(",");
  Monitor.print(gx_rad, 4); Monitor.print(",");
  Monitor.print(gy_rad, 4); Monitor.print(",");
  Monitor.println(gz_rad, 4);
}
