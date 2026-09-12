# Tuya Local BLE (Customized)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A customized Home Assistant integration for controlling **Tuya Bluetooth Low Energy (BLE)** devices locally, with enhanced support for smart locks (such as Raykube / C501) and relay switches (`tdq`).

---

## ✨ Features

- **Direct Local BLE Communication**: Control and receive states directly via your Home Assistant Bluetooth adapter without cloud round-trips for routine operations.
- **Enhanced Smart Lock Support (`jtmspro` / `rppmvevx`)**:
  - **Lock / Unlock Control**: Lock and unlock commands over BLE (DP 6).
  - **Battery Reporting**: Accurate real-time battery sensor (DP 8).
  - **Granular Unlock Method Tracking**:
    - **Fingerprint Unlock** (DP 12): Updates last unlocked user ID and automatically updates lock state to unlocked.
    - **Keypad Password Unlock** (DP 13): Updates last unlocked user ID and automatically updates lock state to unlocked.
    - **NFC Tag / Card Unlock** (DP 15): Updates last unlocked user ID and automatically updates lock state to unlocked.
  - **Security Alarm Sensor** (DP 19): Detects tamper and abnormal security events.
  - **Lock Volume Configuration** (DP 31): Select entity allowing configuration between `Mute`, `Volume 1`, `Volume 2`, and `Volume 3`.
  - **Dynamic One-Time Password (OTP) Generation**:
    - Home Assistant service (`tuya_local_ble.get_one_time_password`) that dynamically queries the Tuya Cloud API to generate a temporary OTP.
    - Reuses authentication credentials from the standard Home Assistant Tuya integration automatically.
- **Relay Switch Support (`tdq` category)**:
  - Local BLE control for relay switches, channel toggles, inching/timer parameters, and backlight configurations.

---

## 📦 Installation

### Option 1: Via HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed in Home Assistant.
2. Open HACS → **Integrations** → click the three dots in the top-right corner → **Custom repositories**.
3. Add this repository URL:
   ```text
   https://github.com/icarome/tuya-local-ble
   ```
   - Category: **Integration**
4. Click **Add**, find **Tuya Local BLE**, and click **Download**.
5. Restart Home Assistant.

### Option 2: Manual Installation

1. Copy the `custom_components/tuya_local_ble` directory into your Home Assistant configuration directory (`<config_dir>/custom_components/tuya_local_ble`).
2. Restart Home Assistant.

---

## ⚙️ Configuration

1. Place your `devices.json` in `<config_dir>/tuya_local_ble/devices.json` (see [`devices.json.example`](devices.json.example) for reference schema).
2. Go to **Settings** → **Devices & Services** → **Add Integration** → search for **Tuya Local BLE**.
3. Follow the config flow to pair your Bluetooth device.

---

## 🔑 Services

### `tuya_local_ble.get_one_time_password`

Generates a dynamic temporary password (OTP) for supported smart locks using the Tuya Cloud API.

#### Example Call (Developer Tools -> Services):
```yaml
service: tuya_local_ble.get_one_time_password
data:
  device_id: "eb3137w0ypatrz0b"
```

#### Response Data:
```json
{
  "password": "12345678",
  "effective_time": 1726156800,
  "invalid_time": 1726157400
}
```

---

## 🛡️ License

Based on the [Tuya-BLE](https://github.com/ShonP40/Tuya-BLE) component with custom enhancements for locks and relays.
