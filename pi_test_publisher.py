#!/usr/bin/env python3
"""Growjo Raspberry Pi soil-sensor MQTT publisher."""

import json
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any

import minimalmodbus
import paho.mqtt.client as mqtt
import serial


# MQTT configuration
MQTT_BROKER_HOST = "192.168.0.107"
MQTT_BROKER_PORT = 1883
MQTT_CLIENT_ID = "growjo-pi01-publisher"
MQTT_KEEPALIVE_SECONDS = 60

# Growjo identity
SITE = "home"
ROOM = "testbench"
PLANT_ID = "plant01"
SENSOR_ID = "soil01"

# THE01888S-RS485 Modbus configuration
SERIAL_PORT = "/dev/ttyUSB0"
MODBUS_ADDRESS = 1
MODBUS_BAUDRATE = 9600
MODBUS_TIMEOUT_SECONDS = 2.0

# Publishing
HEARTBEAT_TOPIC = f"grow/{SITE}/{ROOM}/heartbeat/status"
SOIL_TOPIC_PREFIX = f"grow/{SITE}/{ROOM}/soil"
HEARTBEAT_INTERVAL_SECONDS = 30
SENSOR_INTERVAL_SECONDS = 30
LOG_LEVEL = "INFO"

stop_event = threading.Event()


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload(measurement: str, metric: str, value: Any, unit: str) -> str:
    return json.dumps(
        {
            "measurement": measurement,
            "timestamp": utc_timestamp(),
            "site": SITE,
            "room": ROOM,
            "plant_id": PLANT_ID,
            "sensor_id": SENSOR_ID,
            "metric": metric,
            "value": value,
            "unit": unit,
        },
        separators=(",", ":"),
    )


def publish(client: mqtt.Client, topic: str, message: str, metric: str) -> None:
    info = client.publish(topic, message, qos=0, retain=False)
    if info.rc == mqtt.MQTT_ERR_SUCCESS:
        logging.info("Published metric=%s topic=%s payload=%s", metric, topic, message)
    else:
        logging.error("Publish failed metric=%s topic=%s rc=%s", metric, topic, info.rc)


def create_sensor() -> minimalmodbus.Instrument:
    instrument = minimalmodbus.Instrument(SERIAL_PORT, MODBUS_ADDRESS, mode=minimalmodbus.MODE_RTU)
    instrument.serial.baudrate = MODBUS_BAUDRATE
    instrument.serial.bytesize = 8
    instrument.serial.parity = serial.PARITY_NONE
    instrument.serial.stopbits = 1
    instrument.serial.timeout = MODBUS_TIMEOUT_SECONDS
    instrument.clear_buffers_before_each_transaction = True
    return instrument


def signed_16(value: int) -> int:
    return value - 65536 if value >= 32768 else value


def read_soil_metrics(instrument: minimalmodbus.Instrument) -> list[tuple[str, float, str]]:
    # THE01888S-RS485 exposes its eight live values in holding registers 0x0000-0x0007.
    registers = instrument.read_registers(0x0000, 8, functioncode=3)

    temperature_c = signed_16(registers[0]) / 10.0
    moisture_percent = registers[1] / 10.0
    ec_us_cm = float(registers[2])
    ph = registers[3] / 100.0
    nitrogen_mg_kg = float(registers[4])
    phosphorus_mg_kg = float(registers[5])
    potassium_mg_kg = float(registers[6])
    salinity_mg_kg = float(registers[7])

    return [
        ("temperature_c", temperature_c, "celsius"),
        ("moisture_percent", moisture_percent, "percent"),
        ("ec_us_cm", ec_us_cm, "uS/cm"),
        ("ph", ph, "pH"),
        ("nitrogen_mg_kg", nitrogen_mg_kg, "mg/kg"),
        ("phosphorus_mg_kg", phosphorus_mg_kg, "mg/kg"),
        ("potassium_mg_kg", potassium_mg_kg, "mg/kg"),
        ("salinity_mg_kg", salinity_mg_kg, "mg/kg"),
    ]


def heartbeat_loop(client: mqtt.Client) -> None:
    while not stop_event.is_set():
        publish(client, HEARTBEAT_TOPIC, payload("heartbeat", "heartbeat", 1, "status"), "heartbeat")
        stop_event.wait(HEARTBEAT_INTERVAL_SECONDS)


def sensor_loop(client: mqtt.Client) -> None:
    instrument = None
    while not stop_event.is_set():
        try:
            if instrument is None:
                instrument = create_sensor()
                logging.info(
                    "Opened soil sensor port=%s address=%s baud=%s",
                    SERIAL_PORT,
                    MODBUS_ADDRESS,
                    MODBUS_BAUDRATE,
                )

            for metric, value, unit in read_soil_metrics(instrument):
                topic = f"{SOIL_TOPIC_PREFIX}/{metric}"
                publish(client, topic, payload("soil", metric, value, unit), metric)
        except Exception:
            logging.exception("Soil sensor read failed; will retry")
            instrument = None

        stop_event.wait(SENSOR_INTERVAL_SECONDS)


def connect_mqtt() -> mqtt.Client:
    client = mqtt.Client(client_id=MQTT_CLIENT_ID)
    while not stop_event.is_set():
        try:
            client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_KEEPALIVE_SECONDS)
            client.loop_start()
            logging.info("Connected to MQTT broker %s:%s", MQTT_BROKER_HOST, MQTT_BROKER_PORT)
            return client
        except Exception:
            logging.exception("MQTT connection failed; retrying in 5 seconds")
            stop_event.wait(5)
    raise RuntimeError("Stopped before MQTT connection could be established")


def handle_signal(signum, _frame) -> None:
    logging.info("Received signal %s; stopping", signum)
    stop_event.set()


def start_thread(name: str, target, *args) -> threading.Thread:
    thread = threading.Thread(name=name, target=target, args=args, daemon=True)
    thread.start()
    return thread


def main() -> int:
    configure_logging()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    client = connect_mqtt()
    threads = [
        start_thread("heartbeat", heartbeat_loop, client),
        start_thread("soil-sensor", sensor_loop, client),
    ]

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
        client.loop_stop()
        client.disconnect()
        logging.info("Stopped Growjo Pi publisher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
