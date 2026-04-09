# vessel-bridge

Hardware Abstraction Layer for ESP32 to Jetson to Cloud. Unified sensor/actuator API across vessel domains.

## Architecture

```
ESP32 (micro) ←→ UART/I2C/SPI ←→ Jetson (edge) ←→ MQTT/HTTP ←→ Cloud (cocapn)
```

## Domains

- **Marine** — GPS, compass, sonar, depth, thrusters, rudder, winch
- **Aerial** — GPS, IMU, barometer, lidar, motors, servos
- **Industrial** — Proximity, force, encoders, conveyor, robot arm
- **Home** — Temperature, humidity, light, speakers, displays
- **Medical** — Biometric, proximity, haptic, display

## Usage

```python
from bridge import create_marine_vessel, VesselDomain

vessel = create_marine_vessel("fishing-boat-01")

# Read sensors
gps = vessel.read_sensor("gps_0")
compass = vessel.read_sensor("compass_0")

# Command actuators (auto-clamped to safe range)
vessel.command_actuator("thruster_port", "set", 0.7)
vessel.command_actuator("rudder", "set", -15.0)

# Register callbacks
vessel.on_reading("gps_0", lambda r: print(f"GPS: {r.values}"))

# Health check
health = vessel.get_health()

# Publish state (JSON for MQTT)
vessel.get_state_json()
```

## ESP32 Protocol

Lightweight binary frame protocol for UART:

```
[0xAA][0x55][len_h][len_l][type][payload][crc8]
```

Sensor data uses compact binary encoding. Commands use JSON.

Part of the [Lucineer ecosystem](https://github.com/Lucineer/the-fleet).
