/*
  Arduino Sensor Scanner

  Upload this sketch, then open Serial Monitor at 115200 baud.
  It scans I2C devices and prints likely onboard sensors.

  Notes:
    - Wire is the normal/external I2C bus on many boards.
    - Wire1 is the internal sensor I2C bus on Nano 33 BLE / BLE Sense boards.
*/

#include <Arduino.h>
#include <Wire.h>

struct KnownDevice {
  byte address;
  const char *name;
};

KnownDevice knownDevices[] = {
  {0x10, "BMM150 magnetometer (Nano 33 BLE/BLE Sense Rev2)"},
  {0x1E, "LSM9DS1 magnetometer (Nano 33 BLE/BLE Sense Rev1)"},
  {0x39, "APDS-9960 gesture/color/proximity sensor"},
  {0x44, "HS3003 temperature/humidity sensor (Rev2 Sense)"},
  {0x5C, "LPS22HB barometer/temperature sensor"},
  {0x5D, "LPS22HB barometer/temperature sensor"},
  {0x5F, "HTS221 temperature/humidity sensor (Rev1 Sense)"},
  {0x68, "BMI270 accel/gyro or other IMU at 0x68"},
  {0x69, "BMI270 accel/gyro or other IMU at 0x69"},
  {0x6A, "LSM6DS3/LSM6DSOX accel/gyro or other IMU at 0x6A"},
  {0x6B, "LSM9DS1 accel/gyro (Nano 33 BLE/BLE Sense Rev1)"},
};

const int knownDeviceCount = sizeof(knownDevices) / sizeof(knownDevices[0]);
bool seenAddress[128] = {false};
unsigned long lastScanTime = 0;

const char *knownNameFor(byte address) {
  for (int i = 0; i < knownDeviceCount; i++) {
    if (knownDevices[i].address == address) {
      return knownDevices[i].name;
    }
  }
  return "unknown I2C device";
}

int scanBus(TwoWire &bus, const char *busName) {
  int found = 0;

  Serial.print("\nScanning ");
  Serial.print(busName);
  Serial.println("...");

  bus.begin();
  delay(100);

  for (byte address = 1; address < 127; address++) {
    bus.beginTransmission(address);
    byte error = bus.endTransmission();

    if (error == 0) {
      found++;
      seenAddress[address] = true;
      Serial.print("  Found 0x");
      if (address < 16) {
        Serial.print("0");
      }
      Serial.print(address, HEX);
      Serial.print("  ");
      Serial.println(knownNameFor(address));
    }
  }

  if (found == 0) {
    Serial.println("  No I2C devices found on this bus.");
  }

  return found;
}

void printBoardHint() {
  Serial.println("Board compile macros:");

#if defined(ARDUINO_ARDUINO_NANO33BLE)
  Serial.println("  ARDUINO_ARDUINO_NANO33BLE is defined");
#endif
#if defined(ARDUINO_ARDUINO_NANO_RP2040_CONNECT)
  Serial.println("  ARDUINO_ARDUINO_NANO_RP2040_CONNECT is defined");
#endif
#if defined(ARDUINO_AVR_NANO)
  Serial.println("  ARDUINO_AVR_NANO is defined");
#endif
#if defined(ARDUINO_AVR_NANO_EVERY)
  Serial.println("  ARDUINO_AVR_NANO_EVERY is defined");
#endif
#if defined(ARDUINO_ARCH_MBED)
  Serial.println("  ARDUINO_ARCH_MBED is defined");
#endif
#if defined(ARDUINO_ARCH_AVR)
  Serial.println("  ARDUINO_ARCH_AVR is defined");
#endif
}

void printImuConclusion() {
  Serial.println("\nIMU conclusion:");

  if ((seenAddress[0x68] || seenAddress[0x69]) && seenAddress[0x10]) {
    Serial.println("  Looks like Nano 33 BLE/BLE Sense Rev2 IMU hardware.");
    Serial.println("  Use SENSOR_BOARD_REV 2 and Arduino_BMI270_BMM150.");
    return;
  }

  if (seenAddress[0x6B] && seenAddress[0x1E]) {
    Serial.println("  Looks like Nano 33 BLE/BLE Sense Rev1 IMU hardware.");
    Serial.println("  Use SENSOR_BOARD_REV 1 and Arduino_LSM9DS1.");
    return;
  }

  if (seenAddress[0x6A]) {
    Serial.println("  Found an accel/gyro-looking IMU at 0x6A.");
    Serial.println("  This is often Nano 33 IoT / RP2040 Connect style hardware.");
    Serial.println("  The current sender sketch does not support this IMU yet.");
    return;
  }

  if (seenAddress[0x68] || seenAddress[0x69]) {
    Serial.println("  Found an accel/gyro-looking IMU at 0x68/0x69, but no BMM150 magnetometer.");
    Serial.println("  This may be an external IMU module or a board variant.");
    return;
  }

  Serial.println("  No known onboard accel/gyro address was found.");
  Serial.println("  This may be a plain Nano/Nano Every, the wrong board target, or sensors on an unsupported bus.");
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
  }

  Serial.println("\n=== Arduino Sensor Scanner ===");
  printBoardHint();
}

void runScan() {
  for (int i = 0; i < 128; i++) {
    seenAddress[i] = false;
  }

  Serial.println("\n--- Scan cycle ---");
  int totalFound = 0;
  totalFound += scanBus(Wire, "Wire");

#if defined(ARDUINO_ARCH_MBED)
  totalFound += scanBus(Wire1, "Wire1");
#endif

  Serial.println("\nSummary:");
  if (totalFound == 0) {
    Serial.println("  No I2C sensors found.");
    Serial.println("  If this is a plain Nano/Nano Every, it probably has no onboard accel/gyro.");
  } else {
    Serial.print("  Total I2C devices found: ");
    Serial.println(totalFound);
    Serial.println("  If you see 0x68/0x69, try SENSOR_BOARD_REV 2.");
    Serial.println("  If you see 0x6B and 0x1E, try SENSOR_BOARD_REV 1.");
  }

  printImuConclusion();

  Serial.println("\nScan complete.");
}

void loop() {
  unsigned long now = millis();
  if (now - lastScanTime >= 5000 || lastScanTime == 0) {
    lastScanTime = now;
    runScan();
  }
}
