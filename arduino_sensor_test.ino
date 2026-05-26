/*
  Blender Real-time Sensor TEST Sender (Dummy Data)
  
  This sketch does NOT initialize any real hardware sensors.
  It generates synthetic sine and cosine waves to test the serial connection,
  Python bridge, and Blender Addon.
  
  If this works, you will see numbers scrolling in your terminal and
  the Sensor_IMU object in Blender moving in a circle.
*/

#include <Arduino.h>

const unsigned long SEND_INTERVAL_MS = 10; // ~100Hz updates
unsigned long lastSendTime = 0;
float t = 0.0;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
    // Wait up to 3 seconds for serial console to open
  }
  Serial.println("[Arduino] Test Sender initialized. Sending dummy sine-wave data...");
  lastSendTime = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;
    
    // Increment time step
    t += 0.05;
    if (t > 2.0 * PI) {
      t -= 2.0 * PI;
    }

    // Generate dummy positions / accelerations
    // Moving in a circle on XY plane
    float ax_mps = sin(t) * 2.0; 
    float ay_mps = cos(t) * 2.0;
    float az_mps = 9.80665; // Gravity
    
    // Generate dummy angular velocities (rotation)
    float gx_rad = 0.0;
    float gy_rad = 0.0;
    float gz_rad = 0.5 * sin(t); // Rotate left and right on Z axis

    // Dummy Magnetometer
    float mx = 20.0 + sin(t) * 5.0;
    float my = -10.0 + cos(t) * 5.0;
    float mz = 40.0;

    // Dummy APDS-9960 extra sensors
    int r = (int)(128 + sin(t) * 127);
    int g = (int)(128 + cos(t) * 127);
    int b = (int)(128 - sin(t) * 127);
    int amb = 500;

    // Dummy Barometer
    float pressure_hpa = 1013.25 + sin(t) * 10.0;

    // CSV Output
    Serial.print(ax_mps, 4); Serial.print(",");
    Serial.print(ay_mps, 4); Serial.print(",");
    Serial.print(az_mps, 4); Serial.print(",");
    Serial.print(gx_rad, 4); Serial.print(",");
    Serial.print(gy_rad, 4); Serial.print(",");
    Serial.print(gz_rad, 4); Serial.print(",");
    Serial.print(mx, 2); Serial.print(",");
    Serial.print(my, 2); Serial.print(",");
    Serial.print(mz, 2); Serial.print(",");
    Serial.print(r); Serial.print(",");
    Serial.print(g); Serial.print(",");
    Serial.print(b); Serial.print(",");
    Serial.print(amb); Serial.print(",");
    Serial.println(pressure_hpa, 2);
  }
}
