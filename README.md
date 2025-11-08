# Titon Controller Home Assistant Integration

This repository packages the Titon HRV adaptive controller as a Home Assistant custom integration that can be distributed through HACS. It wraps the existing Flask dashboard and control logic inside a Home Assistant managed service, exposing a sidebar panel and using Home Assistant state to drive humidity-based automation.

## Features at a Glance

- **All-in-one control** – runs the Titon serial controller, adaptive logic, and dashboard directly inside Home Assistant.
- **Modern WebUI** – reuses the multi-page Flask dashboard (Home, Logs, Performance, Settings) from the standalone project.
- **Home Assistant telemetry** – pulls humidity values directly from HA state; no additional long-lived token required.
- **Boost inhibit logic** – respects the Titon BM893 “fastest speed wins” rule and uses register 326 to cap speeds during quiet hours.
- **Adaptive auto-mode** – humidity learning, night quiet scheduling, manual override timers, and historical metrics are maintained automatically.
- **Sidebar integration** – registers an iframe panel in the HA sidebar for one-click access to the UI.
- **Persistent storage** – settings and logs are written alongside the HA configuration (paths are configurable).

## Repository Layout

-  – Home Assistant integration that launches and supervises the controller runtime.
-  – Flask app plus templates, static assets, and JSON settings schema.
-  – metadata so this repository can be added to HACS as a custom integration.
-  – development dependencies mirroring the integration manifest.

## Quick Start (HACS)

1. Open **HACS → Integrations → ⋮ → Custom repositories** and add this GitHub repository (category: ).
2. Search for **Titon Controller** in HACS and install it.
3. Restart Home Assistant so the integration can load.
4. Add the configuration block below to , then restart Home Assistant again.

## Manual Installation

1. Copy  into your Home Assistant  directory.
2. Copy the  package into the same config directory (the integration imports it directly).
3. Ensure the HA Python environment has the dependencies: .
4. Restart Home Assistant and configure the integration in YAML.

## Configuration

Add a  section to :



### Configuration Options

-  – RS-485 adapter path (default ).
-  /  – network binding for the embedded Flask server (default ).
-  – list of Home Assistant humidity entities with display names shown in the UI cards.
-  – optional explicit URL for the iframe (useful when HA is reverse-proxied).
-  /  – optional file locations for persistent data; defaults live in the HA config directory.

By default the integration writes settings to  and logs to .

## What Happens at Runtime

1. On startup the integration:
   - Sets environment variables for serial port, sensor list, log and settings paths.
   - Starts the Titon Flask server via Werkzeug, ensuring background worker threads (serial polling, HA sync, auto control) run only once.
   - Registers a **Titon Controller** iframe in the Home Assistant sidebar targeting the configured host/port.
2. Humidity values are fetched directly from  via an injected provider, so the controller mirrors live HA sensor values.
3. On shutdown the integration stops the Flask server, un-registers the sidebar panel, and cleans up environment overrides.

## Using the Web UI

- **Home** – manual speed controls, boost toggle, auto-mode toggle/status, HA humidity snapshot cards, Titon status word decoding.
- **Performance** – Chart.js graphs for humidity trends, level history, average/max deltas, adaptive learning insights.
- **Logs** – recent controller events, including HA fetch warnings, serial retries, and auto decisions.
- **Settings** – humidity targets per room, night quiet hours, auto aggressiveness, and HA polling interval. Saves immediately via the REST API.

## Development & Local Testing



The UI will be available at . When launched through the integration, these environment variables are set automatically.

## Troubleshooting Tips

- **No UI on port 8050?** Verify nothing else is listening on that port, or change  in the YAML config.
- **Humidity cards show ?** Confirm the entity IDs exist and report numeric states; check Home Assistant logs for messages from .
- **Serial contention?** Only one process should access the RS-485 adapter; the controller already enforces boost inhibit and retries serial errors.
- **Panel URL mismatch?** Set  to the externally reachable URL if the sidebar iframe fails to load due to host header differences.

## License

MIT License.
