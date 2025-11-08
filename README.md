# Titon Controller Home Assistant Integration

This integration packages the Titon HRV adaptive controller so it can run entirely inside Home Assistant. It deploys the Flask dashboard, serial control logic, and background workers while exposing a sidebar panel.

## Features
- Runs the Titon serial controller and adaptive logic directly on Home Assistant
- Ships the multi-page Flask dashboard (Home, Logs, Performance, Settings)
- Reads humidity from Home Assistant sensor state (no extra tokens)
- Provides boost inhibit logic, night quiet scheduling, auto-learning and logging
- Registers a sidebar panel that opens the dashboard

## Quick Start via HACS
1. In Home Assistant open HACS → Integrations → ⋮ → Custom repositories and add this repo (category: Integration).
2. Search for “Titon Controller” and install it. Choose the latest release (e.g. v1.4.2). If releases are not listed, enable “Show Beta” and select the commit hash (for example fd30913).
3. Restart Home Assistant so the integration loads.
4. Add the configuration block below to configuration.yaml, then restart again.

## Manual Installation
1. Copy custom_components/titon_controller into Home Assistant’s config/custom_components directory.
2. Copy the titon_controller_webui package into the same config directory.
3. Ensure Flask and pyserial are installed in the Home Assistant Python environment.
4. Restart Home Assistant and configure the integration in YAML.

## Configuration YAML Example

hacs:
  custom_repositories:
    - repository: https://github.com/respawner/hass_titon_controller_plugin
      category: integration

# Titon controller integration
titon_controller:
  serial_port: /dev/ttyUSB1
  web_host: 0.0.0.0
  web_port: 8050
  humidity_sensors:
    - name: Svetainė
      entity_id: sensor.0x3425b4fffe1283bb_humidity
    - name: Miegamo vonia
      entity_id: sensor.miegamo_vonia_humidity
    - name: Miegamasis
      entity_id: sensor.miegamas_humidity
    - name: Jokūbo kambarys
      entity_id: sensor.jokubo_kambarys_humidity
    - name: Darbo kambarys
      entity_id: sensor.darbo_kambarys_humidity
  panel_url: http://homeassistant.local:8050  # optional
  settings_path: /config/titon_controller_settings.json  # optional
  log_path: /config/titon_controller_webui.log          # optional

## Runtime Notes
- Environment variables are set so the web UI uses the bundled assets.
- The integration falls back to the legacy titon_controller_webui package if HACS installs that layout.
- Settings are stored at <config>/titon_controller_settings.json and logs at <config>/titon_controller_webui.log by default.

## Troubleshooting
- No sidebar entry: confirm the integration installed and restart Home Assistant.
- Humidity shows “--%”: ensure the sensor entity IDs exist and report numeric states.
- Serial conflicts: only one process should access the RS-485 adapter; the integration manages boost inhibit to prevent contention.

## License
MIT License
