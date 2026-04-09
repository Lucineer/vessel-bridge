"""Vessel Bridge — Hardware Abstraction Layer for ESP32 to Jetson to Cloud.

Unified API for sensors, actuators, and power management across
vessel domains: marine, aerial, industrial, home, medical.

Architecture:
  ESP32 (micro) ←→ UART/I2C/SPI ←→ Jetson (edge) ←→ MQTT/HTTP ←→ Cloud (coapn)
  vessel-bridge normalizes all hardware behind a domain-agnostic API.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List
from enum import Enum, auto
import time
import struct
import json


# ─── Domain Types ────────────────────────────────────────────────

class VesselDomain(Enum):
    MARINE = "marine"
    AERIAL = "aerial"
    INDUSTRIAL = "industrial"
    HOME = "home"
    MEDICAL = "medical"


class SensorType(Enum):
    CAMERA = "camera"
    LIDAR = "lidar"
    SONAR = "sonar"
    GPS = "gps"
    IMU = "imu"
    COMPASS = "compass"
    THERMAL = "thermal"
    DEPTH = "depth"
    BAROMETER = "barometer"
    PROXIMITY = "proximity"
    CURRENT = "current"
    VOLTAGE = "voltage"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    LIGHT = "light"
    FORCE = "force"
    ENCODER = "encoder"
    FLOW = "flow"


class ActuatorType(Enum):
    MOTOR = "motor"
    SERVO = "servo"
    THRUSTER = "thruster"
    RUDDER = "rudder"
    ELEVATOR = "elevator"
    WINCH = "winch"
    LIGHT = "light"
    PUMP = "pump"
    VALVE = "valve"
    RELAY = "relay"
    SPEAKER = "speaker"
    DISPLAY = "display"


class TransportType(Enum):
    UART = "uart"
    I2C = "i2c"
    SPI = "spi"
    GPIO = "gpio"
    PWM = "pwm"
    ADC = "adc"
    MQTT = "mqtt"
    HTTP = "http"
    WEBSOCKET = "websocket"
    DDS = "dds"  # ROS 2 Data Distribution Service


class QoSLevel(Enum):
    SAFETY_CRITICAL = 0   # Must deliver, ordered, <1ms
    REALTIME = 1          # Best effort with deadline, <10ms
    BEST_EFFORT = 2       # Fire and forget, <100ms
    BACKGROUND = 3        # Bulk, no deadline


# ─── Data Primitives ─────────────────────────────────────────────

@dataclass
class SensorReading:
    sensor_id: str
    sensor_type: SensorType
    timestamp: float
    values: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    qos: QoSLevel = QoSLevel.BEST_EFFORT
    confidence: float = 1.0

    def to_json(self) -> dict:
        return {
            "sid": self.sensor_id,
            "type": self.sensor_type.value,
            "ts": self.timestamp,
            "v": self.values,
            "meta": self.metadata,
            "qos": self.qos.value,
            "conf": self.confidence,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SensorReading":
        return cls(
            sensor_id=data["sid"],
            sensor_type=SensorType(data["type"]),
            timestamp=data["ts"],
            values=data.get("v", {}),
            metadata=data.get("meta", {}),
            qos=QoSLevel(data.get("qos", 2)),
            confidence=data.get("conf", 1.0),
        )

    def to_binary(self) -> bytes:
        """Compact binary for UART transport (ESP32 compatible)."""
        # Format: type(1) + qos(1) + ts(8) + num_values(1) + [key_len(1) + key + value(4)]...
        buf = bytearray()
        buf.append(self.sensor_type.value)
        buf.append(self.qos.value)
        buf.extend(struct.pack(">d", self.timestamp))
        vals = list(self.values.items())
        buf.append(len(vals))
        for key, val in vals:
            key_bytes = key[:15].encode("ascii")
            buf.append(len(key_bytes))
            buf.extend(key_bytes)
            buf.extend(struct.pack(">f", float(val)))
        return bytes(buf)


@dataclass
class ActuatorCommand:
    actuator_id: str
    actuator_type: ActuatorType
    timestamp: float
    command: str  # "set", "stop", "calibrate"
    value: float = 0.0
    parameters: Dict[str, Any] = field(default_factory=dict)
    qos: QoSLevel = QoSLevel.REALTIME

    def to_json(self) -> dict:
        return {
            "aid": self.actuator_id,
            "type": self.actuator_type.value,
            "ts": self.timestamp,
            "cmd": self.command,
            "val": self.value,
            "params": self.parameters,
            "qos": self.qos.value,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ActuatorCommand":
        return cls(
            actuator_id=data["aid"],
            actuator_type=ActuatorType(data["type"]),
            timestamp=data["ts"],
            command=data["cmd"],
            value=data.get("val", 0.0),
            parameters=data.get("params", {}),
            qos=QoSLevel(data.get("qos", 1)),
        )


# ─── Sensor/Actuator Registration ───────────────────────────────

@dataclass
class SensorConfig:
    sensor_id: str
    sensor_type: SensorType
    transport: TransportType
    address: str  # UART port, I2C addr, SPI CS pin, MQTT topic
    frequency_hz: float = 10.0
    domain: VesselDomain = VesselDomain.MARINE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActuatorConfig:
    actuator_id: str
    actuator_type: ActuatorType
    transport: TransportType
    address: str
    min_value: float = 0.0
    max_value: float = 1.0
    domain: VesselDomain = VesselDomain.MARINE
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── Vessel Bridge Core ─────────────────────────────────────────

class VesselBridge:
    """Unified hardware abstraction for all vessel types.

    Provides domain-agnostic API:
      bridge.read_sensor("gps_0") -> SensorReading
      bridge.command_actuator("thruster_port", "set", 0.7)
      bridge.get_all_readings() -> Dict[str, SensorReading]
      bridge.publish_state()  # Send to fleet via MQTT
    """

    def __init__(self, vessel_id: str, domain: VesselDomain = VesselDomain.MARINE):
        self.vessel_id = vessel_id
        self.domain = domain
        self.sensors: Dict[str, SensorConfig] = {}
        self.actuators: Dict[str, ActuatorConfig] = {}
        self._sensor_cache: Dict[str, SensorReading] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._transport_drivers: Dict[str, Any] = {}
        self._command_log: List[ActuatorCommand] = []

    def register_sensor(self, config: SensorConfig):
        self.sensors[config.sensor_id] = config
        self._sensor_cache[config.sensor_id] = SensorReading(
            sensor_id=config.sensor_id,
            sensor_type=config.sensor_type,
            timestamp=0,
        )

    def register_actuator(self, config: ActuatorConfig):
        self.actuators[config.actuator_id] = config

    def on_reading(self, sensor_id: str, callback: Callable):
        """Register callback for sensor updates."""
        if sensor_id not in self._callbacks:
            self._callbacks[sensor_id] = []
        self._callbacks[sensor_id].append(callback)

    def read_sensor(self, sensor_id: str) -> Optional[SensorReading]:
        """Read latest cached value for a sensor."""
        return self._sensor_cache.get(sensor_id)

    def get_all_readings(self) -> Dict[str, SensorReading]:
        return dict(self._sensor_cache)

    def command_actuator(self, actuator_id: str, command: str,
                         value: float = 0.0,
                         parameters: Dict[str, Any] = None) -> bool:
        """Send command to an actuator with safety checks."""
        config = self.actuators.get(actuator_id)
        if not config:
            return False

        # Clamp value to configured range
        value = max(config.min_value, min(config.max_value, value))

        cmd = ActuatorCommand(
            actuator_id=actuator_id,
            actuator_type=config.actuator_type,
            timestamp=time.time(),
            command=command,
            value=value,
            parameters=parameters or {},
        )

        # Safety critical commands are logged
        if cmd.qos == QoSLevel.SAFETY_CRITICAL:
            self._command_log.append(cmd)
            if len(self._command_log) > 1000:
                self._command_log = self._command_log[-500:]

        # TODO: Route to transport driver
        # self._route_command(cmd)

        return True

    def update_reading(self, sensor_id: str, values: Dict[str, float],
                       confidence: float = 1.0):
        """Internal: update cached sensor reading and fire callbacks."""
        config = self.sensors.get(sensor_id)
        if not config:
            return

        reading = SensorReading(
            sensor_id=sensor_id,
            sensor_type=config.sensor_type,
            timestamp=time.time(),
            values=values,
            confidence=confidence,
            qos=config.metadata.get("qos", QoSLevel.BEST_EFFORT),
        )

        self._sensor_cache[sensor_id] = reading

        # Fire callbacks
        for cb in self._callbacks.get(sensor_id, []):
            try:
                cb(reading)
            except Exception:
                pass

    def get_state_json(self) -> str:
        """Export current vessel state as JSON (for MQTT publish)."""
        return json.dumps({
            "vessel_id": self.vessel_id,
            "domain": self.domain.value,
            "timestamp": time.time(),
            "sensors": {
                k: v.to_json() for k, v in self._sensor_cache.items()
            },
            "actuator_count": len(self.actuators),
        })

    def get_health(self) -> Dict[str, Any]:
        """Health summary of all registered hardware."""
        stale_threshold = 5.0  # seconds
        now = time.time()
        health = {
            "vessel_id": self.vessel_id,
            "domain": self.domain.value,
            "sensors": {},
            "actuators": {},
        }
        for sid, cfg in self.sensors.items():
            reading = self._sensor_cache.get(sid)
            age = now - reading.timestamp if reading else 999
            health["sensors"][sid] = {
                "type": cfg.sensor_type.value,
                "status": "ok" if age < stale_threshold else "stale",
                "age_seconds": round(age, 1),
            }
        for aid, cfg in self.actuators.items():
            health["actuators"][aid] = {
                "type": cfg.actuator_type.value,
                "range": [cfg.min_value, cfg.max_value],
                "status": "registered",
            }
        return health


# ─── ESP32 Serial Protocol ──────────────────────────────────────

class ESP32Protocol:
    """Lightweight binary protocol for ESP32 <-> Jetson UART.

    Frame format:
      [0xAA][0x55][len_h][len_l][type][payload...][crc8]
    """
    SYNC_0 = 0xAA
    SYNC_1 = 0x55

    FRAME_SENSOR = 0x01
    FRAME_COMMAND = 0x02
    FRAME_HEARTBEAT = 0x03
    FRAME_CONFIG = 0x04
    FRAME_ERROR = 0xFF

    @staticmethod
    def encode_sensor(reading: SensorReading) -> bytes:
        payload = reading.to_binary()
        length = len(payload)
        frame = bytearray()
        frame.append(ESP32Protocol.SYNC_0)
        frame.append(ESP32Protocol.SYNC_1)
        frame.extend(struct.pack(">H", length))
        frame.append(ESP32Protocol.FRAME_SENSOR)
        frame.extend(payload)
        frame.append(ESP32Protocol.crc8(frame))
        return bytes(frame)

    @staticmethod
    def encode_command(cmd: ActuatorCommand) -> bytes:
        payload = json.dumps(cmd.to_json()).encode()
        length = len(payload)
        frame = bytearray()
        frame.append(ESP32Protocol.SYNC_0)
        frame.append(ESP32Protocol.SYNC_1)
        frame.extend(struct.pack(">H", length))
        frame.append(ESP32Protocol.FRAME_COMMAND)
        frame.extend(payload)
        frame.append(ESP32Protocol.crc8(frame))
        return bytes(frame)

    @staticmethod
    def decode_frame(data: bytes) -> Optional[dict]:
        if len(data) < 6:
            return None
        if data[0] != ESP32Protocol.SYNC_0 or data[1] != ESP32Protocol.SYNC_1:
            return None
        length = struct.unpack(">H", data[2:4])[0]
        frame_type = data[4]
        payload = data[5:5 + length]
        expected_crc = data[5 + length]
        actual_crc = ESP32Protocol.crc8(data[:5 + length])
        if expected_crc != actual_crc:
            return None
        return {"type": frame_type, "payload": payload}

    @staticmethod
    def crc8(data: bytes) -> int:
        crc = 0
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x07
                else:
                    crc = crc << 1
                crc &= 0xFF
        return crc


# ─── Domain Presets ─────────────────────────────────────────────

MARINE_SENSORS = [
    SensorConfig("gps_0", SensorType.GPS, TransportType.UART, "/dev/ttyUSB0", 1.0, VesselDomain.MARINE),
    SensorConfig("compass_0", SensorType.COMPASS, TransportType.I2C, "0x1E", 10.0, VesselDomain.MARINE),
    SensorConfig("sonar_0", SensorType.SONAR, TransportType.SPI, "CS0", 5.0, VesselDomain.MARINE),
    SensorConfig("depth_0", SensorType.DEPTH, TransportType.I2C, "0x76", 5.0, VesselDomain.MARINE),
    SensorConfig("thermistor_0", SensorType.THERMAL, TransportType.ADC, "A0", 1.0, VesselDomain.MARINE),
    SensorConfig("current_0", SensorType.CURRENT, TransportType.ADC, "A1", 10.0, VesselDomain.MARINE),
    SensorConfig("voltage_0", SensorType.VOLTAGE, TransportType.ADC, "A2", 10.0, VesselDomain.MARINE),
]

MARINE_ACTUATORS = [
    ActuatorConfig("thruster_port", ActuatorType.THRUSTER, TransportType.PWM, "GPIO12", -1.0, 1.0, VesselDomain.MARINE),
    ActuatorConfig("thruster_stbd", ActuatorType.THRUSTER, TransportType.PWM, "GPIO13", -1.0, 1.0, VesselDomain.MARINE),
    ActuatorConfig("rudder", ActuatorType.RUDDER, TransportType.SERVO, "GPIO14", -45.0, 45.0, VesselDomain.MARINE),
    ActuatorConfig("light_nav", ActuatorType.LIGHT, TransportType.RELAY, "GPIO15", 0.0, 1.0, VesselDomain.MARINE),
    ActuatorConfig("winch", ActuatorType.WINCH, ActuatorType.RELAY, "GPIO16", 0.0, 1.0, VesselDomain.MARINE),
]

AERIAL_SENSORS = [
    SensorConfig("gps_0", SensorType.GPS, TransportType.UART, "/dev/ttyUSB0", 5.0, VesselDomain.AERIAL),
    SensorConfig("imu_0", SensorType.IMU, TransportType.I2C, "0x68", 100.0, VesselDomain.AERIAL),
    SensorConfig("baro_0", SensorType.BAROMETER, TransportType.I2C, "0x77", 50.0, VesselDomain.AERIAL),
    SensorConfig("lidar_0", SensorType.LIDAR, TransportType.UART, "/dev/ttyUSB1", 10.0, VesselDomain.AERIAL),
]

AERIAL_ACTUATORS = [
    ActuatorConfig("motor_fl", ActuatorType.MOTOR, TransportType.PWM, "GPIO4", 0.0, 1.0, VesselDomain.AERIAL),
    ActuatorConfig("motor_fr", ActuatorType.MOTOR, TransportType.PWM, "GPIO5", 0.0, 1.0, VesselDomain.AERIAL),
    ActuatorConfig("motor_bl", ActuatorType.MOTOR, TransportType.PWM, "GPIO6", 0.0, 1.0, VesselDomain.AERIAL),
    ActuatorConfig("motor_br", ActuatorType.MOTOR, TransportType.PWM, "GPIO7", 0.0, 1.0, VesselDomain.AERIAL),
    ActuatorConfig("servo_roll", ActuatorType.SERVO, TransportType.SERVO, "GPIO8", -30.0, 30.0, VesselDomain.AERIAL),
    ActuatorConfig("servo_pitch", ActuatorType.SERVO, TransportType.SERVO, "GPIO9", -30.0, 30.0, VesselDomain.AERIAL),
    ActuatorConfig("servo_yaw", ActuatorType.SERVO, TransportType.SERVO, "GPIO10", -180.0, 180.0, VesselDomain.AERIAL),
]


def create_marine_vessel(vessel_id: str) -> VesselBridge:
    bridge = VesselBridge(vessel_id, VesselDomain.MARINE)
    for s in MARINE_SENSORS:
        bridge.register_sensor(s)
    for a in MARINE_ACTUATORS:
        bridge.register_actuator(a)
    return bridge


def create_aerial_vessel(vessel_id: str) -> VesselBridge:
    bridge = VesselBridge(vessel_id, VesselDomain.AERIAL)
    for s in AERIAL_SENSORS:
        bridge.register_sensor(s)
    for a in AERIAL_ACTUATORS:
        bridge.register_actuator(a)
    return bridge
