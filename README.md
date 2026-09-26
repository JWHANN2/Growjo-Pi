# Growjo Raspberry Pi Soil Sensor Publisher

Reads the Yieryi THE01888S-RS485 8-in-1 soil sensor over Modbus RTU and publishes the readings to the Growjo MQTT broker.

## Data path

`THE01888S-RS485 -> USB/RS485 adapter -> Raspberry Pi -> MQTT -> Growjo server -> Telegraf -> InfluxDB`

## Sensor defaults

- Serial device: `/dev/ttyUSB0`
- Modbus address: `1`
- Baud: `9600`
- Format: `8N1`
- Function: holding-register read (`0x03`)
- Live registers: `0x0000` through `0x0007`

The publisher reads temperature, moisture, EC, pH, nitrogen, phosphorus, potassium, and salinity every 30 seconds.

## MQTT topics

Readings are published under:

```
grow/home/testbench/soil/temperature_c
grow/home/testbench/soil/moisture_percent
grow/home/testbench/soil/ec_us_cm
grow/home/testbench/soil/ph
grow/home/testbench/soil/nitrogen_mg_kg
grow/home/testbench/soil/phosphorus_mg_kg
grow/home/testbench/soil/potassium_mg_kg
grow/home/testbench/soil/salinity_mg_kg
```

Heartbeat:

```
grow/home/testbench/heartbeat/status
```

Each payload includes `site`, `room`, `plant_id`, `sensor_id`, `metric`, `value`, and `unit`.

## Install

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
sudo mkdir -p /opt/growjo-pi-test-publisher
sudo cp pi_test_publisher.py requirements.txt growjo-pi-test-publisher.service /opt/growjo-pi-test-publisher/
sudo chown -R pi:pi /opt/growjo-pi-test-publisher
cd /opt/growjo-pi-test-publisher
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## Find the USB/RS485 adapter

After plugging the adapter into the Pi:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

The default code expects `/dev/ttyUSB0`. Change `SERIAL_PORT` in `pi_test_publisher.py` if your adapter appears under a different device.

The service user must have serial-port access. On Raspberry Pi OS this is normally the `dialout` group:

```bash
sudo usermod -aG dialout pi
```

Log out/reboot after changing group membership.

## Manual test

```bash
cd /opt/growjo-pi-test-publisher
.venv/bin/python pi_test_publisher.py
```

Watch MQTT from another machine:

```bash
mosquitto_sub -h 192.168.0.107 -p 1883 -t 'grow/home/testbench/#' -v
```

## Systemd

```bash
sudo cp /opt/growjo-pi-test-publisher/growjo-pi-test-publisher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now growjo-pi-test-publisher.service
journalctl -u growjo-pi-test-publisher.service -f
```

## Notes

- The old CPU/memory/disk telemetry has been removed. The Pi now publishes actual soil-sensor readings plus a heartbeat.
- If Modbus reads time out, verify sensor power and serial device first. If power is correct, swapping RS485 A/B is a normal troubleshooting step.
- NPK values from this class of multi-parameter probe are best treated as trend/reference values rather than laboratory nutrient analysis.
