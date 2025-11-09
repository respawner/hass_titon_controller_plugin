# Changelog

## 2.0.2
- Update panel registration for the latest Home Assistant APIs (use frontend_url_path)
- Gracefully surface WebUI port-in-use errors as ConfigEntryNotReady

## 2.0.1
- Fix panel registration for Home Assistant 2025.1+ (use frontend helper API)
- Ensure config flow release guidance references tagged releases to avoid HACS 404s

## 2.0.0
- Add Home Assistant config flow for guided setup
- Allow selecting room humidity sensors from the integration options
- Update documentation for the new UI-driven workflow

## 1.4.3
- Fix thread initialization race in simple_webui (ensure background workers start under lock)

## 1.4.2
- Fall back to either the bundled or legacy titon_controller_webui module and support builds that lack set_ha_state_provider

## 1.4.1
- Bundle web UI inside the integration but gracefully fall back to the legacy titon_controller_webui package if it exists
- Document how to install a specific commit from HACS when releases are unavailable

