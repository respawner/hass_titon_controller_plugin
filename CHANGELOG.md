# Changelog

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

