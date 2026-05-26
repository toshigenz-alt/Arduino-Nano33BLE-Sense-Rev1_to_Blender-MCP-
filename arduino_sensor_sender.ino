/*
  Blender Real-time Sensor Sender - IMU Only
  
  Set SENSOR_BOARD_REV below to match your board:
    2 = Arduino Nano 33 BLE Sense Rev2 (BMI270 + BMM150)
    1 = Arduino Nano 33 BLE Sense Rev1 (LSM9DS1)
*/

#include <Arduino.h>

#define SENSOR_BOARD_REV 1

#if SENSOR_BOARD_REV == 2
  #include <Arduino_BMI270_BMM150.h>
  const char IMU_NAME[] = "BMI270 + BMM150 (Nano 33 BLE Sense Rev2)";
#elif SENSOR_BOARD_REV == 1
  #include <Arduino_LSM9DS1.h>
  const char IMU_NAME[] = "LSM9DS1 (Nano 33 BLE Sense Rev1)";
#else
  #error "SENSOR_BOARD_REV must be 1 or 2"
#endif

const bool USE_JSON = false;
const unsigned long SEND_INTERVAL_MS = 50; // ~20Hz updates; easier to read/debug over Serial Monitor
const unsigned long ERROR_INTERVAL_MS = 1000;
unsigned long lastSendTime = 0;
unsigned long lastErrorTime = 0;
unsigned long lastSampleWaitTime = 0;
bool imuReady = false;
bool gotAccel = false;
bool gotGyro = false;
bool gotMag = false;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
    // Wait up to 3 seconds for serial console to open
  }

  Serial.println("\n[Arduino] --- DIAGNOSTICS START ---");
  Serial.print("[Arduino] Initializing IMU: ");
  Serial.println(IMU_NAME);

  imuReady = IMU.begin();
  if (!imuReady) {
    Serial.println("[Arduino] ERROR: Failed to initialize IMU!");
    Serial.println("[Arduino] Check SENSOR_BOARD_REV at the top of arduino_sensor_sender.ino.");
    Serial.println("[Arduino] Rev2 needs Arduino_BMI270_BMM150. Rev1 needs Arduino_LSM9DS1.");
  } else {
    Serial.println("[Arduino] IMU initialized successfully!");
  }

  Serial.println("[Arduino] Startup complete.");
  Serial.println("DATA_HEADER,ax_mps,ay_mps,az_mps,gx_rad,gy_rad,gz_rad,mx_uT,my_uT,mz_uT,r,g,b,amb,press_hpa");
  lastSendTime = millis();
}

// Keep sensor values persistent so they don't drop to 0 when samples aren't ready
static float ax = 0.0, ay = 0.0, az = 1.0; // default Z to 1G
static float gx = 0.0, gy = 0.0, gz = 0.0;
static float mx = 0.0, my = 0.0, mz = 0.0;

void loop() {
  if (!imuReady) {
    unsigned long now = millis();
    if (now - lastErrorTime >= ERROR_INTERVAL_MS) {
      lastErrorTime = now;
      Serial.println("[Arduino] IMU not ready. Not streaming fake zero data.");
    }
    return;
  }

  // 1. Read sensors continuously to empty internal FIFO buffers and avoid lag
  if (IMU.accelerationAvailable()) {
    IMU.readAcceleration(ax, ay, az);
    gotAccel = true;
  }
  if (IMU.gyroscopeAvailable()) {
    IMU.readGyroscope(gx, gy, gz);
    gotGyro = true;
  }
  if (IMU.magneticFieldAvailable()) {
    IMU.readMagneticField(mx, my, mz);
    gotMag = true;
  }

  if (!gotAccel || !gotGyro) {
    unsigned long now = millis();
    if (now - lastSampleWaitTime >= ERROR_INTERVAL_MS) {
      lastSampleWaitTime = now;
      Serial.print("[Arduino] Waiting for IMU samples. accel=");
      Serial.print(gotAccel ? "yes" : "no");
      Serial.print(" gyro=");
      Serial.print(gotGyro ? "yes" : "no");
      Serial.print(" mag=");
      Serial.println(gotMag ? "yes" : "no");
    }
    return;
  }

  // 2. Send data over Serial at 100Hz (every 10ms)
  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    // Convert Accel from G to m/s^2
    float ax_mps = ax * 9.80665;
    float ay_mps = ay * 9.80665;
    float az_mps = az * 9.80665;
    
    // Convert Gyro from deg/s to rad/s
    float gx_rad = gx * DEG_TO_RAD;
    float gy_rad = gy * DEG_TO_RAD;
    float gz_rad = gz * DEG_TO_RAD;
    
    // Extra sensors are disabled in this minimal version, send default values
    int r_scaled = 128;
    int g_scaled = 128;
    int b_scaled = 128;
    int amb = 500;
    float pressure_hpa = 1013.25;

    // Send data over Serial
    if (USE_JSON) {
      Serial.print("{\"ax\":"); Serial.print(ax_mps, 4);
      Serial.print(",\"ay\":"); Serial.print(ay_mps, 4);
      Serial.print(",\"az\":"); Serial.print(az_mps, 4);
      Serial.print(",\"gx\":"); Serial.print(gx_rad, 4);
      Serial.print(",\"gy\":"); Serial.print(gy_rad, 4);
      Serial.print(",\"gz\":"); Serial.print(gz_rad, 4);
      Serial.print(",\"mx\":"); Serial.print(mx, 2);
      Serial.print(",\"my\":"); Serial.print(my, 2);
      Serial.print(",\"mz\":"); Serial.print(mz, 2);
      Serial.print(",\"r\":"); Serial.print(r_scaled);
      Serial.print(",\"g\":"); Serial.print(g_scaled);
      Serial.print(",\"b\":"); Serial.print(b_scaled);
      Serial.print(",\"amb\":"); Serial.print(amb);
      Serial.print(",\"press\":"); Serial.print(pressure_hpa, 2);
      Serial.println("}");
    } else {
      // CSV output
      Serial.print("DATA,");
      Serial.print(ax_mps, 4); Serial.print(",");
      Serial.print(ay_mps, 4); Serial.print(",");
      Serial.print(az_mps, 4); Serial.print(",");
      Serial.print(gx_rad, 4); Serial.print(",");
      Serial.print(gy_rad, 4); Serial.print(",");
      Serial.print(gz_rad, 4); Serial.print(",");
      Serial.print(mx, 2); Serial.print(",");
      Serial.print(my, 2); Serial.print(",");
      Serial.print(mz, 2); Serial.print(",");
      Serial.print(r_scaled); Serial.print(",");
      Serial.print(g_scaled); Serial.print(",");
      Serial.print(b_scaled); Serial.print(",");
      Serial.print(amb); Serial.print(",");
      Serial.println(pressure_hpa, 2);
    }
  }
}
