#!/usr/bin/env python3
"""Growjo Raspberry Pi test telemetry publisher."""

import json
import logging
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psutil
from flask import Flask, send_file
import paho.mqtt.client as mqtt


# MQTT configuration
MQTT_BROKER_HOST = "192.168.0.107"
MQTT_BROKER_PORT = 1883
MQTT_CLIENT_ID = "grow-test-pi01-publisher"
MQTT_KEEPALIVE_SECONDS = 60

# Growjo identity
SITE = "home"
ROOM = "testbench"
DEVICE_ID = "pi01"

# Topics
HEARTBEAT_TOPIC = "grow/test/pi01/heartbeat"
SYSTEM_TOPIC = "grow/test/pi01/system"
CAMERA_TOPIC = "grow/test/pi01/camera"

# Publish intervals
HEARTBEAT_INTERVAL_SECONDS = 30
SYSTEM_INTERVAL_SECONDS = 30
CAMERA_INTERVAL_SECONDS = 60

# Optional camera/web settings
ENABLE_CAMERA = True
IMAGE_PATH = Path(__file__).with_name("latest.jpg")
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
CAMERA_COMMAND_TIMEOUT_SECONDS = 20

# Logging
LOG_LEVEL = "INFO"


stop_event = threading.Event()
app = Flask(__name__)


@app.route("/latest.jpg")
def latest_jpg():
    if not IMAGE_PATH.exists():
        return ("latest.jpg has not been captured yet\n", 404)
    return send_file(IMAGE_PATH, mimetype="image/jpeg")


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload(
    measurement: str,
    metric: str,
    value: Any,
    unit: str,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    data = {
        "measurement": measurement,
        "timestamp": utc_timestamp(),
        "site": SITE,
        "room": ROOM,
        "device_id": DEVICE_ID,
        "metric": metric,
        "value": value,
        "unit": unit,
    }
    if extra:
        data.update(extra)
    return json.dumps(data, separators=(",", ":"))


def get_pi_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((MQTT_BROKER_HOST, MQTT_BROKER_PORT))
            return sock.getsockname()[0]
    except OSError as exc:
        logging.warning("Could not determine LAN IP address: %s", exc)
        return socket.gethostname()


def publish(client: mqtt.Client, topic: str, message: str, metric: str) -> None:
    try:
        info = client.publish(topic, message, qos=0, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            logging.info("Published metric=%s topic=%s payload=%s", metric, topic, message)
        else:
            logging.error("Publish failed metric=%s topic=%s rc=%s", metric, topic, info.rc)
    except Exception:
        logging.exception("Publish raised an exception metric=%s topic=%s", metric, topic)


def read_cpu_temp_c() -> Optional[float]:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = thermal_path.read_text(encoding="utf-8").strip()
        return round(float(raw) / 1000.0, 2)
    except Exception as exc:
        logging.warning("Could not read CPU temperature from %s: %s", thermal_path, exc)

    vcgencmd = shutil.which("vcgencmd")
    if not vcgencmd:
        return None

    try:
        result = subprocess.run(
            [vcgencmd, "measure_temp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip().replace("temp=", "").replace("'C", "")
        return round(float(value), 2)
    except Exception as exc:
        logging.warning("Could not read CPU temperature with vcgencmd: %s", exc)
        return None


def safe_metric(name: str, func, unit: str) -> Optional[tuple[str, Any, str]]:
    try:
        return (name, func(), unit)
    except Exception:
        logging.exception("Metric read failed metric=%s", name)
        return None


def system_metrics() -> list[tuple[str, Any, str]]:
    candidates = [
        safe_metric("cpu_temp_c", read_cpu_temp_c, "celsius"),
        safe_metric("cpu_percent", lambda: round(psutil.cpu_percent(interval=None), 2), "percent"),
        safe_metric("memory_percent", lambda: round(psutil.virtual_memory().percent, 2), "percent"),
        safe_metric("disk_percent", lambda: round(psutil.disk_usage("/").percent, 2), "percent"),
    ]
    metrics: list[tuple[str, Any, str]] = []
    for item in candidates:
        if item is None:
            continue
        name, value, unit = item
        if value is None:
            logging.warning("Skipping unavailable metric=%s", name)
            continue
        metrics.append((name, value, unit))
    return metrics


def heartbeat_loop(client: mqtt.Client) -> None:
    while not stop_event.is_set():
        publish(client, HEARTBEAT_TOPIC, payload("heartbeat", "heartbeat", 1, "status"), "heartbeat")
        stop_event.wait(HEARTBEAT_INTERVAL_SECONDS)


def system_loop(client: mqtt.Client) -> None:
    psutil.cpu_percent(interval=None)
    while not stop_event.is_set():
        for metric, value, unit in system_metrics():
            publish(client, SYSTEM_TOPIC, payload("system", metric, value, unit), metric)
        stop_event.wait(SYSTEM_INTERVAL_SECONDS)


def camera_command() -> Optional[list[str]]:
    for command_name in ("rpicam-still", "libcamera-still"):
        command = shutil.which(command_name)
        if command:
            return [
                command,
                "--nopreview",
                "--timeout",
                "1000",
                "--output",
                str(IMAGE_PATH),
            ]
    return None


def camera_available(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=CAMERA_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode == 0 and IMAGE_PATH.exists():
            logging.info("Camera detected and initial image captured at %s", IMAGE_PATH)
            return True
        logging.warning(
            "Camera capture test failed rc=%s stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )
    except Exception as exc:
        logging.warning("Camera capture test failed: %s", exc)
    return False


def flask_loop() -> None:
    logging.info("Serving latest camera image on http://%s:%s/latest.jpg", get_pi_ip(), FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT, threaded=True, use_reloader=False)


def camera_loop(client: mqtt.Client, command: list[str], image_url: str) -> None:
    while not stop_event.is_set():
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=CAMERA_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                publish(
                    client,
                    CAMERA_TOPIC,
                    payload("camera", "image_url", image_url, "url", {"image_url": image_url}),
                    "image_url",
                )
            else:
                logging.error(
                    "Camera capture failed rc=%s stderr=%s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except Exception:
            logging.exception("Camera capture raised an exception")
        stop_event.wait(CAMERA_INTERVAL_SECONDS)


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
        start_thread("system", system_loop, client),
    ]

    if ENABLE_CAMERA:
        command = camera_command()
        if command and camera_available(command):
            pi_ip = get_pi_ip()
            image_url = f"http://{pi_ip}:{FLASK_PORT}/latest.jpg"
            threads.append(start_thread("flask", flask_loop))
            threads.append(start_thread("camera", camera_loop, client, command, image_url))
            logging.info("Camera publishing enabled image_url=%s", image_url)
        else:
            logging.info("No supported camera detected; continuing without camera publishing")

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
        client.loop_stop()
        client.disconnect()
        logging.info("Stopped Growjo Pi test publisher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
