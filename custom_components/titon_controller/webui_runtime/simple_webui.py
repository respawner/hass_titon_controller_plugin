#!/usr/bin/env python3
"""
Titon Controller WebUI
======================

Modernised interface inspired by the GREE controller dashboard. Provides:
  • Multi-page UX (Home, Logs, Performance, Settings)
  • Adaptive auto mode with humidity-driven learning
  • Night quiet hours guard to avoid disruptive speeds
  • Settings persistence for Home Assistant humidity sensors
  • Enhanced status + history APIs for graphs and logs
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import serial
from flask import Flask, jsonify, render_template, request

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime
    requests = None  # type: ignore

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

PORT = os.environ.get("TITON_SERIAL_PORT", "/dev/ttyUSB1")
BAUD = 1200

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WEBUI_DIR = BASE_DIR / "webui"
WEBUI_DIR = Path(os.environ.get("TITON_WEBUI_DIR", str(DEFAULT_WEBUI_DIR)))
TEMPLATE_DIR = WEBUI_DIR / "templates"
STATIC_DIR = WEBUI_DIR / "static"
LOG_PATH = Path(os.environ.get("TITON_LOG_PATH", str(WEBUI_DIR / "webui.log")))
SETTINGS_PATH = Path(os.environ.get("TITON_SETTINGS_PATH", str(WEBUI_DIR / "settings.json")))
WEB_HOST = os.environ.get("TITON_WEBUI_HOST", "0.0.0.0")
try:
    WEB_PORT = int(os.environ.get("TITON_WEBUI_PORT", "8050"))
except ValueError:
    WEB_PORT = 8050

for directory in (WEBUI_DIR, TEMPLATE_DIR, STATIC_DIR, LOG_PATH.parent, SETTINGS_PATH.parent):
    directory.mkdir(parents=True, exist_ok=True)

# Sensor entities supplied by the user (title, entity_id)
DEFAULT_SENSOR_ENTITIES: List[Tuple[str, str]] = [
    ("Svetainė", "sensor.0x3425b4fffe1283bb_humidity"),
    ("Miegamo vonia", "sensor.miegamo_vonia_humidity"),
    ("Miegamasis", "sensor.miegamas_humidity"),
    ("Jokūbo kambarys", "sensor.jokubo_kambarys_humidity"),
    ("Darbo kambarys", "sensor.darbo_kambarys_humidity"),
]

SENSOR_ENTITIES: List[Tuple[str, str]] = DEFAULT_SENSOR_ENTITIES.copy()
ENV_SENSOR_CONFIG = os.environ.get("TITON_SENSOR_ENTITIES")
if ENV_SENSOR_CONFIG:
    try:
        raw_entries = json.loads(ENV_SENSOR_CONFIG)
        parsed: List[Tuple[str, str]] = []
        for item in raw_entries:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                parsed.append((str(item[0]), str(item[1])))
            elif isinstance(item, dict) and "name" in item and "entity_id" in item:
                parsed.append((str(item["name"]), str(item["entity_id"])))
        if parsed:
            SENSOR_ENTITIES = parsed
    except Exception:
        pass

SENSOR_IDS = [entity for _, entity in SENSOR_ENTITIES]

DEFAULT_TARGET = 55.0

DEFAULT_SETTINGS: Dict[str, Any] = {
    "auto_mode": {
        "enabled": False,
        "override_minutes": 15,
        "aggressiveness": "balanced",
    },
    "night_quiet": {
        "enabled": True,
        "start": "21:00",
        "end": "08:00",
        "max_level": 2,
    },
    "humidity_targets": {entity: DEFAULT_TARGET for entity in SENSOR_IDS},
    "ha": {
        "url": "http://192.168.1.166:8123",
        "token": "",
        "timeout": 5,
        "poll_seconds": 30,
    },
    "learning": {
        "adapt_rate": 0.08,
        "max_offset": 8.0,
        "sample_window": 12,  # 12 samples ≈ 6 minutes @30s
    },
}

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

settings_lock = threading.Lock()


def ensure_setting_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge stored settings with defaults to keep backward compatibility."""
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key, value in data.items():
        if isinstance(value, dict) and key in merged:
            merged[key].update(value)
        else:
            merged[key] = value

    # Ensure humidity targets cover every entity
    targets = merged.setdefault("humidity_targets", {})
    for entity in SENSOR_IDS:
        targets.setdefault(entity, DEFAULT_TARGET)

    return merged


def load_settings() -> Dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return ensure_setting_structure(data)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"⚠️ Failed to load settings ({exc}), reverting to defaults")
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings() -> None:
    with settings_lock:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


settings = load_settings()
if not SETTINGS_PATH.exists():
    save_settings()

# ---------------------------------------------------------------------------
# Flask application initialisation
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))


@app.context_processor
def inject_globals() -> Dict[str, Any]:
    return {"datetime": datetime}

# ---------------------------------------------------------------------------
# Global state & helpers
# ---------------------------------------------------------------------------

state_lock = threading.Lock()
_threads_lock = threading.Lock()
_threads_started = False
_runtime_lock = threading.Lock()
_runtime_started = False

state: Dict[str, Any] = {
    "current_level": None,
    "boost_active": False,
    "boost_inhibit": False,
    "last_command": None,
    "last_command_time": None,
    "level_start_time": None,
    "mode": "manual",
    "auto_enabled": settings["auto_mode"]["enabled"],
    "manual_override_until": None,
    "night_quiet_enabled": settings["night_quiet"]["enabled"],
    "night_quiet_active": False,
    "sensors": {
        "indoor_temp": None,
        "outdoor_temp": None,
        "fresh_temp": None,
        "humidity": None,
        "runtime_hours": None,
    },
    "status": {"raw": None, "flags": []},
    "metrics": {
        "avg_humidity": None,
        "max_humidity": None,
        "avg_delta": None,
        "max_delta": None,
        "time_in_range_pct": None,
    },
    "auto_status": {
        "last_run": None,
        "recommended_level": None,
        "applied_level": None,
        "reason": None,
        "last_learning_update": None,
    },
    "ha_humidity": {},
    "learning": {
        "offsets": {entity: 0.0 for entity in SENSOR_IDS},
    },
}

history_buffer: deque = deque(maxlen=1440)  # ≈24h @ 60s sampling
log_buffer: deque = deque(maxlen=500)

auto_state: Dict[str, Any] = {
    "diff_buffers": {entity: deque(maxlen=settings["learning"]["sample_window"]) for entity in SENSOR_IDS},
}

ha_session = requests.Session() if requests else None  # type: ignore
HA_STATE_PROVIDER: Optional[Callable[[str], Optional[float]]] = None

# ---------------------------------------------------------------------------
# Logging utilities
# ---------------------------------------------------------------------------

log_lock = threading.Lock()


def append_log(kind: str, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "kind": kind,
        "message": message,
        "meta": meta or {},
    }
    with log_lock:
        log_buffer.appendleft(entry)
        try:
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass


def record_history(entry: Dict[str, Any]) -> None:
    history_buffer.append(entry)
    # Update performance metric (time in range)
    valid_entries = [h for h in history_buffer if h.get("max_delta") is not None]
    if valid_entries:
        within = sum(1 for h in valid_entries if h["max_delta"] <= 0)
        pct = round(within / len(valid_entries) * 100, 1)
    else:
        pct = None
    with state_lock:
        state["metrics"]["time_in_range_pct"] = pct


def snapshot_state() -> Dict[str, Any]:
    with state_lock:
        data = json.loads(json.dumps(state))
    with settings_lock:
        data["settings"] = settings
    return data


def parse_time(value: str) -> dt_time:
    hour, minute = value.split(":")
    return dt_time(hour=int(hour), minute=int(minute))


def is_within_quiet_hours(now: Optional[datetime] = None) -> bool:
    if not state["night_quiet_enabled"]:
        return False

    cfg = settings["night_quiet"]
    start = parse_time(cfg["start"])
    end = parse_time(cfg["end"])
    now = now or datetime.now()
    now_time = now.time()

    if start <= end:
        return start <= now_time < end
    # Overnight window (e.g. 21:00 -> 08:00)
    return now_time >= start or now_time < end


def enforce_night_quiet(level: int) -> Tuple[int, bool]:
    quiet_max = settings["night_quiet"].get("max_level", 2)
    if is_within_quiet_hours():
        capped = min(level, int(quiet_max))
        with state_lock:
            state["night_quiet_active"] = True
        return capped, capped != level
    with state_lock:
        state["night_quiet_active"] = False
    return level, False


def schedule_manual_override() -> None:
    minutes = max(1, int(settings["auto_mode"].get("override_minutes", 10)))
    expiry = time.time() + minutes * 60
    with state_lock:
        state["manual_override_until"] = expiry
    append_log("mode", f"Manual override active for {minutes} min", {"until": expiry})


def manual_override_active() -> Tuple[bool, Optional[float]]:
    with state_lock:
        expiry = state.get("manual_override_until")
    if expiry and expiry > time.time():
        return True, expiry - time.time()
    if expiry and expiry <= time.time():
        with state_lock:
            state["manual_override_until"] = None
        append_log("mode", "Manual override window expired; auto mode resumes")
    return False, None


def update_metrics_from_humidity(humidity_map: Dict[str, Optional[float]]) -> Dict[str, Any]:
    valid_values = [v for v in humidity_map.values() if isinstance(v, (int, float))]
    if not valid_values:
        metrics = {"avg_humidity": None, "max_humidity": None, "avg_delta": None, "max_delta": None}
    else:
        avg = sum(valid_values) / len(valid_values)
        mx = max(valid_values)
        deltas = []
        for entity, val in humidity_map.items():
            if val is None:
                continue
            target = settings["humidity_targets"].get(entity, DEFAULT_TARGET)
            offset = state["learning"]["offsets"].get(entity, 0.0)
            deltas.append(val - (target + offset))
        avg_delta = sum(deltas) / len(deltas) if deltas else None
        max_delta = max(deltas) if deltas else None
        metrics = {
            "avg_humidity": round(avg, 1),
            "max_humidity": round(mx, 1),
            "avg_delta": round(avg_delta, 2) if avg_delta is not None else None,
            "max_delta": round(max_delta, 2) if max_delta is not None else None,
        }
    with state_lock:
        state["metrics"].update(metrics)
    return metrics


# ---------------------------------------------------------------------------
# Serial helpers (re-using previous proven logic)
# ---------------------------------------------------------------------------


def enable_titon_remote_control() -> bool:
    print("\n" + "=" * 70)
    print("🔧 ENABLING TITON REMOTE CONTROL")
    print("=" * 70)
    try:
        with serial.Serial(PORT, BAUD, timeout=2) as ser:
            commands = [
                ("041", 0, "Operating Mode = 0"),
                ("043", 1, "Auto/Manual = 1"),
                ("044", 0, "Fan Control = 0"),
                ("042", 1, "Remote Enable = 1"),
                ("054", 1, "Control Authority = 1"),
            ]
            for addr, val, label in commands:
                cmd = f"{addr}0+{val:05d}\r\n"
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                time.sleep(0.1)
                ser.write(cmd.encode("ascii"))
                ser.flush()
                time.sleep(0.3)
                print(f"  ✓ {label}")
        print("=" * 70)
        print("✅ REMOTE CONTROL ENABLED")
        print("=" * 70 + "\n")
        append_log("system", "Remote control enabled")
        return True
    except Exception as exc:
        print(f"❌ Failed to enable remote control: {exc}\n")
        append_log("error", "Failed to enable remote control", {"error": str(exc)})
        return False


def wait_for_quiet_period(ser: serial.Serial, max_wait: float = 10.0) -> bool:
    start = time.time()
    quiet_time = 0.0
    required_quiet = 2.0
    while time.time() - start < max_wait:
        if ser.in_waiting > 0:
            ser.read(ser.in_waiting)
            quiet_time = 0.0
            time.sleep(0.1)
        else:
            quiet_time += 0.1
            time.sleep(0.1)
            if quiet_time >= required_quiet:
                return True
    return False


def send_command(addr: str, val: int, retry: int = 0) -> bool:
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print("\n" + "=" * 70)
    prefix = f"[{timestamp}] 🔵 SERIAL COMMAND TRACE START"
    print(prefix if retry == 0 else f"{prefix} (RETRY #{retry})")
    print("=" * 70)
    try:
        with serial.Serial(PORT, BAUD, timeout=2) as ser:
            cmd = f"{addr}0+{val:05d}\r\n"
            cmd_bytes = cmd.encode("ascii")
            if addr in {"151", "152", "154", "045"}:
                wait_for_quiet_period(ser, max_wait=10.0)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.1)
            bytes_written = ser.write(cmd_bytes)
            ser.flush()
            print(f"[{timestamp}] → Sent {bytes_written}/{len(cmd_bytes)} bytes to register {addr}")
            time.sleep(0.5)
            resp = ser.read(ser.in_waiting if ser.in_waiting > 0 else 32)
            if resp:
                decoded = resp.decode("ascii", errors="replace")
                parsed_reg = decoded[0:3] if len(decoded) >= 3 else None
                if parsed_reg and parsed_reg != addr and retry < 2:
                    time.sleep(1)
                    return send_command(addr, val, retry=retry + 1)
            else:
                if retry < 2:
                    time.sleep(1)
                    return send_command(addr, val, retry=retry + 1)
        with state_lock:
            state["last_command"] = f"{addr}={val}"
            state["last_command_time"] = datetime.utcnow().isoformat()
        print(f"[{timestamp}] ✅ Command complete")
        print("=" * 70)
        return True
    except Exception as exc:
        print(f"[{timestamp}] ❌ ERROR: {exc}")
        append_log("error", "Serial command failed", {"register": addr, "value": val, "error": str(exc)})
        return False


def apply_boost_inhibit(enabled: bool) -> bool:
    target_val = 1 if enabled else 0
    if send_command("326", target_val):
        with state_lock:
            state["boost_inhibit"] = enabled
        return True
    return False


def apply_level_strategy(level: int) -> bool:
    strategies = {
        1: [
            ("154", 0, "Speed 4 OFF"),
            ("152", 0, "Speed 3 OFF"),
            ("151", 0, "Speed 1 OFF"),
            ("326", 1, "Boost Inhibit ON (block Speed 3/4)"),
            ("151", 1, "Speed 1 ON"),
        ],
        2: [
            ("154", 0, "Speed 4 OFF"),
            ("152", 0, "Speed 3 OFF"),
            ("151", 0, "Speed 1 OFF"),
            ("326", 1, "Boost Inhibit ON (hold default Speed 2)"),
        ],
        3: [
            ("326", 0, "Boost Inhibit OFF"),
            ("154", 0, "Speed 4 OFF"),
            ("151", 0, "Speed 1 OFF"),
            ("152", 1, "Speed 3 ON"),
        ],
        4: [
            ("326", 0, "Boost Inhibit OFF"),
            ("152", 0, "Speed 3 OFF"),
            ("151", 0, "Speed 1 OFF"),
            ("154", 1, "Speed 4 ON"),
        ],
    }
    steps = strategies.get(level)
    if not steps:
        append_log("error", f"Invalid strategy requested for level {level}")
        return False

    for addr, val, label in steps:
        if not send_command(addr, val):
            append_log("error", "Level strategy step failed", {"step": label, "level": level})
            return False
        time.sleep(0.2)

    with state_lock:
        state["boost_inhibit"] = level in (1, 2)
        state["boost_active"] = level == 4
        state["current_level"] = level
        state["level_start_time"] = datetime.utcnow().isoformat()
    append_log("control", f"Level {level} applied", {"strategy": True})
    return True


def turn_off_all_levels() -> bool:
    steps = [
        ("151", 0, "Speed 1 OFF"),
        ("152", 0, "Speed 3 OFF"),
        ("154", 0, "Speed 4 OFF"),
        ("326", 0, "Boost Inhibit OFF"),
    ]
    for addr, val, label in steps:
        if not send_command(addr, val):
            append_log("error", "Turn off step failed", {"step": label})
            return False
        time.sleep(0.2)
    with state_lock:
        state["current_level"] = None
        state["boost_active"] = False
        state["boost_inhibit"] = False
        state["level_start_time"] = None
    append_log("control", "All levels turned off")
    return True


def read_sensor(addr: str) -> Optional[int]:
    try:
        with serial.Serial(PORT, BAUD, timeout=2) as ser:
            ser.reset_input_buffer()
            cmd = f"{addr}1xxxxxx\r\n"
            ser.write(cmd.encode("ascii"))
            ser.flush()
            time.sleep(0.5)
            resp = ser.read(ser.in_waiting if ser.in_waiting > 0 else 32)
            if resp:
                decoded = resp.decode("ascii", errors="replace")
                for token in decoded.split("\r\n"):
                    if len(token) >= 9 and token[:3].isdigit():
                        try:
                            return int(token[4:9])
                        except ValueError:
                            continue
    except Exception:
        pass
    return None


STATUS_FLAGS = [
    (1, "Supply fan error"),
    (2, "Thermistor error"),
    (4, "Extract fan error"),
    (8, "EEPROM error"),
    (16, "Switch 1 active"),
    (32, "Switch 2 active"),
    (64, "Switch 3 active"),
    (128, "Limit switch 1 active"),
    (256, "Limit switch 2 active"),
    (512, "Engine error"),
    (1024, "Switch error"),
    (2048, "Engine running"),
    (4096, "Thermistor 1 error"),
    (8192, "Thermistor 2 error"),
    (16384, "Thermistor 3 error"),
    (32768, "Humidity sensor error"),
]


def decode_status_word(value: int) -> List[str]:
    flags = [label for bit, label in STATUS_FLAGS if value & bit]
    return flags or ["No errors reported"]


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


def update_sensors_loop() -> None:
    while True:
        try:
            indoor = read_sensor("030")
            outdoor = read_sensor("031")
            fresh = read_sensor("032")
            humidity = read_sensor("036")
            runtime = read_sensor("060")
            status_word = read_sensor("061")
            with state_lock:
                if indoor is not None:
                    state["sensors"]["indoor_temp"] = indoor / 10
                if outdoor is not None:
                    state["sensors"]["outdoor_temp"] = outdoor / 10
                if fresh is not None:
                    state["sensors"]["fresh_temp"] = fresh / 10
                if humidity is not None:
                    state["sensors"]["humidity"] = humidity
                if runtime is not None:
                    state["sensors"]["runtime_hours"] = runtime
                if status_word is not None:
                    state["status"]["raw"] = status_word
                    state["status"]["flags"] = decode_status_word(status_word)
        except Exception as exc:
            append_log("error", "Sensor update failed", {"error": str(exc)})
        time.sleep(60)


def set_ha_state_provider(provider: Optional[Callable[[str], Optional[float]]]) -> None:
    """Inject a callback that resolves Home Assistant sensor states."""
    global HA_STATE_PROVIDER
    HA_STATE_PROVIDER = provider


def fetch_home_assistant_humidity() -> Dict[str, Optional[float]]:
    if HA_STATE_PROVIDER:
        results: Dict[str, Optional[float]] = {}
        for _, entity in SENSOR_ENTITIES:
            try:
                results[entity] = HA_STATE_PROVIDER(entity)
            except Exception as exc:  # pragma: no cover - defensive
                results[entity] = None
                append_log("error", "HA provider failed", {"entity": entity, "error": str(exc)})
        return results

    if requests is None or not ha_session:
        return {}

    with settings_lock:
        ha_cfg = dict(settings["ha"])
    base_url = ha_cfg.get("url", "").rstrip("/")
    token = ha_cfg.get("token", "").strip()
    timeout = float(ha_cfg.get("timeout", 5))
    if not base_url or not token:
        return {}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    results: Dict[str, Optional[float]] = {}

    for _, entity in SENSOR_ENTITIES:
        try:
            resp = ha_session.get(f"{base_url}/api/states/{entity}", headers=headers, timeout=timeout)
            if resp.status_code != 200:
                results[entity] = None
                append_log(
                    "error",
                    "HA state fetch failed",
                    {"entity": entity, "status": resp.status_code, "body": resp.text[:200]},
                )
                continue
            payload = resp.json()
            raw = payload.get("state")
            if raw in {"unknown", "unavailable", None}:
                results[entity] = None
            else:
                results[entity] = float(raw)
        except Exception as exc:
            results[entity] = None
            append_log("error", f"HA fetch failed for {entity}", {"error": str(exc)})

    return results


def environment_monitor_loop() -> None:
    while True:
        humidity_map = fetch_home_assistant_humidity()
        metrics = update_metrics_from_humidity(humidity_map)
        with state_lock:
            state["ha_humidity"] = humidity_map
        record_history(
            {
                "ts": datetime.utcnow().isoformat(),
                "level": state.get("current_level"),
                "avg_humidity": metrics.get("avg_humidity"),
                "max_humidity": metrics.get("max_humidity"),
                "avg_delta": metrics.get("avg_delta"),
                "max_delta": metrics.get("max_delta"),
            }
        )
        with settings_lock:
            poll_seconds = max(10, int(settings["ha"].get("poll_seconds", 30)))
        time.sleep(poll_seconds)


def update_learning_offsets(entity: str, delta: float) -> None:
    buf = auto_state["diff_buffers"].setdefault(entity, deque(maxlen=settings["learning"]["sample_window"]))
    buf.append(delta)
    if len(buf) < buf.maxlen:
        return
    avg_delta = sum(buf) / len(buf)
    rate = float(settings["learning"].get("adapt_rate", 0.05))
    max_offset = float(settings["learning"].get("max_offset", 10.0))
    adjustment = rate * avg_delta
    with state_lock:
        offsets = state["learning"]["offsets"]
        new_val = offsets.get(entity, 0.0) + adjustment
        new_val = max(-max_offset, min(max_offset, new_val))
        offsets[entity] = round(new_val, 3)
        state["auto_status"]["last_learning_update"] = datetime.utcnow().isoformat()


def determine_auto_level(humidity_map: Dict[str, Optional[float]]) -> Tuple[int, str]:
    if not humidity_map:
        return 2, "No Home Assistant humidity data - staying at Level 2"

    deltas: List[float] = []
    for _, entity in SENSOR_ENTITIES:
        value = humidity_map.get(entity)
        if value is None:
            continue
        target = settings["humidity_targets"].get(entity, DEFAULT_TARGET)
        offset = state["learning"]["offsets"].get(entity, 0.0)
        delta = value - (target + offset)
        deltas.append(delta)
        update_learning_offsets(entity, delta)

    if not deltas:
        return 2, "No valid humidity readings - holding Level 2"

    max_delta = max(deltas)
    avg_delta = sum(deltas) / len(deltas)

    aggressiveness = settings["auto_mode"].get("aggressiveness", "balanced").lower()
    high_threshold = 8 if aggressiveness == "calm" else 6
    medium_threshold = 4 if aggressiveness == "calm" else 3
    low_threshold = -3 if aggressiveness == "aggressive" else -1.5

    if max_delta >= high_threshold:
        level = 4
    elif max_delta >= medium_threshold or avg_delta >= medium_threshold / 2:
        level = 3
    elif avg_delta <= low_threshold:
        level = 1
    else:
        level = 2

    reason = f"avgΔ {avg_delta:.1f}%, maxΔ {max_delta:.1f}%"
    return level, reason


def auto_controller_loop() -> None:
    while True:
        time.sleep(15)
        with state_lock:
            auto_enabled = state["auto_enabled"]
            humidity_map = dict(state.get("ha_humidity", {}))
            current_level = state.get("current_level")
        if not auto_enabled:
            continue

        override, remaining = manual_override_active()
        if override:
            with state_lock:
                state["auto_status"]["last_run"] = datetime.utcnow().isoformat()
                state["auto_status"]["recommended_level"] = None
                state["auto_status"]["reason"] = f"Manual override active ({int(remaining)}s left)"
            continue

        recommended, reason = determine_auto_level(humidity_map)
        recommended, capped = enforce_night_quiet(recommended)
        reason_suffix = " (night quiet cap)" if capped else ""
        should_change = recommended != current_level

        with state_lock:
            state["auto_status"]["last_run"] = datetime.utcnow().isoformat()
            state["auto_status"]["recommended_level"] = recommended
            state["auto_status"]["reason"] = reason + reason_suffix

        if should_change:
            if recommended == 0:
                if turn_off_all_levels():
                    with state_lock:
                        state["auto_status"]["applied_level"] = 0
                    append_log("auto", "Auto mode turned Titon OFF", {"reason": reason + reason_suffix})
            else:
                if apply_level_strategy(recommended):
                    with state_lock:
                        state["auto_status"]["applied_level"] = recommended
                    append_log("auto", f"Auto mode set Level {recommended}", {"reason": reason + reason_suffix})
        else:
            with state_lock:
                state["auto_status"]["applied_level"] = current_level


# ---------------------------------------------------------------------------
# Routes - Pages
# ---------------------------------------------------------------------------


@app.route("/")
def home():
    snap = snapshot_state()
    return render_template(
        "home.html",
        sensors=snap["sensors"],
        metrics=snap["metrics"],
        auto_state=snap["auto_status"],
        current_level=snap["current_level"],
        boost_inhibit=snap["boost_inhibit"],
        boost_active=snap["boost_active"],
        level_start_time=snap["level_start_time"],
        auto_enabled=snap["auto_enabled"],
        night_quiet_enabled=snap["night_quiet_enabled"],
        night_quiet_active=snap["night_quiet_active"],
        sensor_entities=SENSOR_ENTITIES,
        settings=snap["settings"],
    )


@app.route("/logs")
def logs_view():
    return render_template("logs.html")


@app.route("/performance")
def performance_view():
    return render_template("performance.html")


@app.route("/settings")
def settings_view():
    with settings_lock:
        cfg = json.loads(json.dumps(settings))
    return render_template("settings.html", settings=cfg, sensor_entities=SENSOR_ENTITIES)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.route("/api/status")
def api_status():
    snap = snapshot_state()
    snap["history_samples"] = len(history_buffer)
    snap["log_entries"] = len(log_buffer)
    return jsonify(snap)


@app.route("/api/logs")
def api_logs():
    limit = int(request.args.get("limit", 100))
    with log_lock:
        data = list(log_buffer)[:limit]
    return jsonify({"logs": data})


@app.route("/api/history")
def api_history():
    limit = int(request.args.get("limit", 288))
    data = list(history_buffer)[-limit:]
    return jsonify({"history": data})


@app.route("/api/auto/toggle", methods=["POST"])
def api_toggle_auto():
    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled"))
    with state_lock:
        state["auto_enabled"] = enabled
        state["mode"] = "auto" if enabled else "manual"
    with settings_lock:
        settings["auto_mode"]["enabled"] = enabled
    save_settings()
    append_log("mode", f"Auto mode {'enabled' if enabled else 'disabled'} via UI")
    return jsonify({"success": True, "auto_enabled": enabled})


@app.route("/api/night-mode", methods=["POST"])
def api_toggle_night_mode():
    payload = request.get_json(force=True, silent=True) or {}
    enabled = payload.get("enabled")
    start = payload.get("start")
    end = payload.get("end")
    max_level = payload.get("max_level")

    with settings_lock:
        cfg = settings["night_quiet"]
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if isinstance(start, str):
            cfg["start"] = start
        if isinstance(end, str):
            cfg["end"] = end
        if max_level is not None:
            cfg["max_level"] = int(max(1, min(4, int(max_level))))
        enabled_flag = cfg["enabled"]
    with state_lock:
        state["night_quiet_enabled"] = enabled_flag
    save_settings()
    append_log("mode", f"Night quiet hours {'enabled' if enabled_flag else 'disabled'}")
    return jsonify({"success": True, "night_quiet": settings["night_quiet"]})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        with settings_lock:
            cfg = json.loads(json.dumps(settings))
        return jsonify(cfg)

    payload = request.get_json(force=True, silent=True) or {}
    with settings_lock:
        for entity in SENSOR_IDS:
            try:
                target = float(payload.get("humidity_targets", {}).get(entity, settings["humidity_targets"][entity]))
                settings["humidity_targets"][entity] = max(30.0, min(80.0, target))
            except (TypeError, ValueError):
                continue
        if "ha" in payload:
            settings["ha"]["url"] = payload["ha"].get("url", settings["ha"]["url"])
            token = payload["ha"].get("token")
            if token is not None:
                settings["ha"]["token"] = token
            if "poll_seconds" in payload["ha"]:
                settings["ha"]["poll_seconds"] = max(10, int(payload["ha"]["poll_seconds"]))
        if "auto_mode" in payload:
            override = payload["auto_mode"].get("override_minutes")
            if override is not None:
                settings["auto_mode"]["override_minutes"] = max(1, int(override))
            aggressive = payload["auto_mode"].get("aggressiveness")
            if aggressive:
                settings["auto_mode"]["aggressiveness"] = aggressive
        if "night_quiet" in payload:
            settings["night_quiet"]["start"] = payload["night_quiet"].get("start", settings["night_quiet"]["start"])
            settings["night_quiet"]["end"] = payload["night_quiet"].get("end", settings["night_quiet"]["end"])
            settings["night_quiet"]["max_level"] = int(
                max(1, min(4, int(payload["night_quiet"].get("max_level", settings["night_quiet"]["max_level"]))))
            )
            settings["night_quiet"]["enabled"] = bool(payload["night_quiet"].get("enabled", settings["night_quiet"]["enabled"]))
        save_settings()

    append_log("settings", "Settings updated", {"payload": payload})
    return jsonify({"success": True})


@app.route("/api/level/<int:level>", methods=["POST"])
def api_set_level(level: int):
    if level not in (1, 2, 3, 4):
        return jsonify({"success": False, "error": "Invalid level"}), 400

    level, capped = enforce_night_quiet(level)
    if capped:
        append_log("mode", f"Requested level capped due to quiet hours", {"capped_level": level})

    success = apply_level_strategy(level)
    if success:
        schedule_manual_override()
        return jsonify({"success": True, "level": level})

    return jsonify({"success": False, "error": "Command sequence failed"}), 500


@app.route("/api/off", methods=["POST"])
def api_turn_off():
    if turn_off_all_levels():
        schedule_manual_override()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Command sequence failed"}), 500


@app.route("/api/boost", methods=["POST"])
def api_toggle_boost():
    with state_lock:
        new_state = not state["boost_active"]
    success = send_command("154", 1 if new_state else 0)
    if success:
        with state_lock:
            state["boost_active"] = new_state
            state["current_level"] = 4 if new_state else state["current_level"]
            state["level_start_time"] = datetime.utcnow().isoformat() if new_state else state["level_start_time"]
        schedule_manual_override()
        append_log("control", f"Boost {'ON' if new_state else 'OFF'}", {})
        return jsonify({"success": True, "state": "ON" if new_state else "OFF"})
    return jsonify({"success": False, "error": "Command failed"}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start_background_threads() -> None:
    global _threads_started
    with _threads_lock:
        if _threads_started:
            return
        _threads_started = True
    threading.Thread(target=update_sensors_loop, daemon=True).start()
    threading.Thread(target=environment_monitor_loop, daemon=True).start()
    threading.Thread(target=auto_controller_loop, daemon=True).start()


def ensure_runtime_started() -> None:
    global _runtime_started
    with _runtime_lock:
        if _runtime_started:
            return
        enable_titon_remote_control()
        start_background_threads()
        _runtime_started = True


def create_server(host: str = "0.0.0.0", port: int = 8050):
    from werkzeug.serving import make_server

    ensure_runtime_started()
    server = make_server(host, port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def shutdown_server(server) -> None:
    try:
        server.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    print("=" * 70)
    print("🌬  TITON CONTROLLER WEBUI")
    print("=" * 70)
    print(f"Serial Port: {PORT}")
    print(f"Baud Rate:   {BAUD}")
    print(f"WebUI Dir:   {WEBUI_DIR}")
    print(f"Log Path:    {LOG_PATH}")
    print(f"Settings:    {SETTINGS_PATH}")
    print("=" * 70)

    ensure_runtime_started()

    print("=" * 70)
    print(f"✅ Starting WebUI on http://{WEB_HOST}:{WEB_PORT}")
    print("=" * 70)

    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
