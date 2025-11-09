# Titon Controller Home Assistant Integration

This integration packages the Titon HRV adaptive controller so it can run entirely inside Home Assistant. It deploys the Flask dashboard, serial control logic, and background workers while exposing a sidebar panel.

## Features
- Runs the Titon serial controller and adaptive logic directly on Home Assistant
- Ships the multi-page Flask dashboard (Home, Logs, Performance, Settings)
- Reads humidity from Home Assistant sensor state (no extra tokens)
- Provides boost inhibit logic, night quiet scheduling, auto-learning and logging
- Registers a sidebar panel that opens the dashboard

## Quick Start via HACS
1. In Home Assistant open **HACS → Integrations → ⋮ → Custom repositories** and add this repo (category: Integration).
2. Search for **Titon Controller** and install it. Choose the latest release (e.g. v2.0.2). If releases are not listed, enable “Show Beta” and pick the newest tag (avoid selecting raw commit hashes to prevent HACS download errors).
3. Restart Home Assistant so the integration is discovered.
4. Go to **Settings → Devices & Services → + Add Integration**, search for **Titon Controller**, and follow the setup wizard.
5. Open the integration’s **Options** (⋮ menu) to select which Home Assistant humidity sensors should appear as rooms in the Titon dashboard.

## Manual Installation
1. Copy `custom_components/titon_controller` into Home Assistant’s `config/custom_components` directory.
2. Copy the `titon_controller_webui` package into the same `config` directory.
3. Ensure `Flask` and `pyserial` are installed in the Home Assistant Python environment.
4. Restart Home Assistant, add the integration via the UI, and choose sensors through the options dialog.

## Optional YAML Configuration
YAML support is still available for advanced deployments. Values provided in YAML are imported into the config entry on first setup.

```yaml
# Titon controller integration
titon_controller:
  serial_port: /dev/ttyUSB1
  web_host: 0.0.0.0
  web_port: 8050
  humidity_sensors:
    - name: Living Room
      entity_id: sensor.living_room_humidity
    - name: Bedroom
      entity_id: sensor.bedroom_humidity
  panel_url: http://homeassistant.local:8050  # optional
  settings_path: /config/titon_controller_settings.json  # optional
  log_path: /config/titon_controller_webui.log          # optional
```

## Runtime Notes
- Environment variables are set so the web UI uses the bundled assets.
- The integration falls back to the legacy `titon_controller_webui` package if HACS installs that layout.
- Settings are stored at `<config>/titon_controller_settings.json` and logs at `<config>/titon_controller_webui.log` by default.

## Troubleshooting
- **No sidebar entry:** confirm the integration installed, then restart Home Assistant.
- **Humidity shows “--%”:** ensure the selected sensor entity IDs exist and report numeric states.
- **Serial conflicts:** only one process should access the RS-485 adapter; the integration manages boost inhibit to prevent contention.

## License
MIT License
