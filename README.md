# Growjo Raspberry Pi Test Publisher

Publishes Raspberry Pi test telemetry to the Growjo MQTT broker on Raspberry Pi OS Bookworm.

## MQTT Topics

- `grow/test/pi01/heartbeat`
- `grow/test/pi01/system`
- `grow/test/pi01/camera`

## Payload Shape

All telemetry is JSON:

```json
{
  "measurement": "system",
  "timestamp": "2026-04-25T12:00:00+00:00",
  "site": "home",
  "room": "testbench",
  "device_id": "pi01",
  "metric": "cpu_percent",
  "value": 12.5,
  "unit": "percent"
}
```

## Install

On the Raspberry Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip rpicam-apps
sudo mkdir -p /opt/growjo-pi-test-publisher
sudo cp pi_test_publisher.py requirements.txt growjo-pi-test-publisher.service /opt/growjo-pi-test-publisher/
sudo chown -R pi:pi /opt/growjo-pi-test-publisher
cd /opt/growjo-pi-test-publisher
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

If your Pi image uses `libcamera-still` instead of `rpicam-still`, install the camera package available for that image. The publisher supports both commands.

## Configure

Configuration variables are at the top of `pi_test_publisher.py`.

Default broker:

```python
MQTT_BROKER_HOST = "192.168.0.107"
MQTT_BROKER_PORT = 1883
```

Default identity:

```python
SITE = "home"
ROOM = "testbench"
DEVICE_ID = "pi01"
```

## Manual Test

Run the publisher in the foreground:

```bash
cd /opt/growjo-pi-test-publisher
.venv/bin/python pi_test_publisher.py
```

Watch MQTT messages from another terminal:

```bash
sudo apt install -y mosquitto-clients
mosquitto_sub -h 192.168.0.107 -p 1883 -t 'grow/test/pi01/#' -v
```

Test camera image serving if a camera is detected:

```bash
curl -I http://localhost:5000/latest.jpg
```

The publisher continues heartbeat and system telemetry if no camera is detected.

## Systemd Service

Install the service file:

```bash
sudo cp /opt/growjo-pi-test-publisher/growjo-pi-test-publisher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable growjo-pi-test-publisher.service
sudo systemctl start growjo-pi-test-publisher.service
```

Check status and logs:

```bash
systemctl status growjo-pi-test-publisher.service
journalctl -u growjo-pi-test-publisher.service -f
```

Stop or restart:

```bash
sudo systemctl stop growjo-pi-test-publisher.service
sudo systemctl restart growjo-pi-test-publisher.service
```

## Notes

- Heartbeat publishes every 30 seconds.
- System metrics publish every 30 seconds.
- Camera capture publishes every 60 seconds when `rpicam-still` or `libcamera-still` can capture an image.
- Camera images are served from `http://<pi-ip>:5000/latest.jpg`.
- Individual metric failures are logged and skipped without stopping the process.
