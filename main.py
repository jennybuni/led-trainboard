"""
main.py - Interstate 75 W train departure board.

Displays departure information on a 128x32 panel using the built-in bitmap font.
Service data is sourced from a JSON file located on-device or served from a local
Docker container (HTTP). The script rotates through multiple services every five
minutes (or on button press) and falls back to defaults if no data is available.
"""

import time
import json

try:
    import urequests as requests  # type: ignore
except ImportError:
    raise RuntimeError(
        "urequests module not found. Use Pimoroni's MicroPython build or install it manually."
    )

try:
    import network
except ImportError:
    network = None  # type: ignore

try:
    import ntptime
except ImportError:
    ntptime = None  # type: ignore

try:
    from interstate75 import Interstate75, DISPLAY_INTERSTATE75_128X32, SWITCH_A
except ImportError:
    raise RuntimeError(
        "Could not import Interstate75. Ensure the 'interstate75' module is on the MicroPython filesystem."
    )


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

LOCAL_JSON_PATH = "departures.json"  # set to None to skip local file lookups
REMOTE_JSON_URL = "http://192.168.1.50:8000/departures.json"  # set to None if unused
FETCH_INTERVAL = 30  # seconds between data refreshes
SERVICE_ROTATE_INTERVAL = 300  # seconds between service rotations
UTC_OFFSET_HOURS = 0  # adjust if you want local time displayed

# Manual advance debounce for Interstate75 switch inputs
BUTTON_DEBOUNCE_MS = 200

DEFAULT_SERVICE = {
    "sched": "12:24",
    "destination": "London Euston",
    "status": "On time",
    "calling": "Watford Junction, Milton Keynes Central, Rugby, Coventry, Birmingham Int'l",
}

# Ticker timing (pixel-based)
TICKER_MS = 120
TICKER_STEP_PX = 1

# Built-in font & scale
FONT_NAME = "bitmap8"
SCALE_MAIN = 1
SCALE_CLOCK = 1


# -----------------------------------------------------------------------------
# Display setup
# -----------------------------------------------------------------------------

i75 = Interstate75(display=DISPLAY_INTERSTATE75_128X32)
graphics = i75.display
DISPLAY_WIDTH = i75.width
DISPLAY_HEIGHT = i75.height

# Colours
COL_BLACK = (0, 0, 0)
COL_ORANGE = (255, 0, 140)
COL_GREEN = (0, 50, 255)
COL_RED = (255, 0, 0)
COL_BLUE = (0, 120, 255)

BLACK_PEN = graphics.create_pen(*COL_BLACK)

# State
svc_state = None  # (sched, destination, status, calling)
svc_services = []  # list of available services
current_service_idx = 0
ticker_text = ""
ticker_w = 1
ticker_px = 0
last_fetch_ms = 0
last_scroll_ms = 0
last_rotate_ms = 0
last_source = "defaults"
wifi_ok = False
last_button_state = False
last_button_ms = 0


# -----------------------------------------------------------------------------
# Networking helpers
# -----------------------------------------------------------------------------

def connect_wifi():
    """Connect using secrets.py; return True if connected."""
    if network is None:
        print("Wi-Fi module unavailable; running without remote JSON")
        return False
    try:
        from secrets import WIFI_SSID, WIFI_PASSWORD  # type: ignore
    except ImportError:
        print("secrets.py missing; cannot connect to Wi-Fi")
        return False

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        wlan.config(pm=0x11140)  # reduce power saving issues on some APs
    except Exception:
        pass

    if not wlan.isconnected():
        print("Connecting to Wi-Fi.")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(100):  # wait up to ~10s
            if wlan.isconnected():
                break
            time.sleep(0.1)

    if wlan.isconnected():
        print("Connected. IP:", wlan.ifconfig()[0])
        return True

    print("Failed to connect to Wi-Fi")
    return False


def sync_time():
    """Sync RTC via NTP (if available)."""
    if ntptime is None or network is None:
        return
    try:
        ntptime.settime()
        print("Time synchronised via NTP")
    except Exception as exc:
        print("NTP sync failed:", exc)


# -----------------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------------

def normalise_service(entry):
    if not isinstance(entry, dict):
        return None

    sched = str(entry.get("sched") or entry.get("scheduled") or DEFAULT_SERVICE["sched"]).strip()
    if not sched:
        sched = DEFAULT_SERVICE["sched"]

    destination = str(entry.get("destination") or DEFAULT_SERVICE["destination"]).strip()
    if not destination:
        destination = DEFAULT_SERVICE["destination"]

    status = str(entry.get("status") or DEFAULT_SERVICE["status"]).strip()
    if not status:
        status = DEFAULT_SERVICE["status"]

    calling = entry.get("calling", DEFAULT_SERVICE["calling"])
    if isinstance(calling, list):
        parts = []
        for item in calling:
            text = str(item).strip()
            if text:
                parts.append(text)
        calling = ", ".join(parts)
    calling = str(calling).strip()
    if not calling:
        calling = DEFAULT_SERVICE["calling"]

    return (sched, destination, status, calling)


def extract_services(payload):
    services = []
    candidates = []

    if isinstance(payload, dict):
        services_list = payload.get("services")
        if isinstance(services_list, list):
            candidates.extend(services_list)
        elif "service" in payload:
            candidates.append(payload.get("service"))
        else:
            candidates.append(payload)
    elif isinstance(payload, list):
        candidates = payload
    else:
        return services

    seen = set()
    for item in candidates:
        svc = normalise_service(item)
        if svc and svc not in seen:
            services.append(svc)
            seen.add(svc)
    return services


def load_local_services():
    if not LOCAL_JSON_PATH:
        return []
    try:
        with open(LOCAL_JSON_PATH) as f:
            payload = json.load(f)
        return extract_services(payload)
    except OSError:
        return []
    except Exception as exc:
        print("Local JSON error:", exc)
        return []


def load_remote_services():
    if not REMOTE_JSON_URL:
        return []
    resp = None
    try:
        resp = requests.get(REMOTE_JSON_URL)
        payload = resp.json()
        result = extract_services(payload)
    except Exception as exc:
        print("Remote JSON error:", exc)
        result = []
    finally:
        if resp:
            try:
                resp.close()
            except Exception:
                pass
    return result


def strip_calling_prefix(text):
    value = text.strip()
    return value[11:].lstrip() if value.upper().startswith("CALLING AT:") else value


def build_ticker_text(raw):
    base = strip_calling_prefix(raw).upper()
    if not base:
        base = "NO CALLING POINTS"
    return "CALLING AT: " + base + "   |   "


def apply_service(service):
    global svc_state, ticker_text, ticker_w, ticker_px

    svc_state = service
    ticker_text = build_ticker_text(service[3])
    ticker_w = max(1, int(graphics.measure_text(ticker_text, SCALE_MAIN)))
    ticker_px = 0


def advance_service(now_ms, manual=False):
    global current_service_idx, last_rotate_ms

    if len(svc_services) <= 1:
        return

    current_service_idx = (current_service_idx + 1) % len(svc_services)
    apply_service(svc_services[current_service_idx])
    last_rotate_ms = now_ms
    if manual:
        print(
            "Button advanced to service",
            current_service_idx + 1,
            "of",
            len(svc_services),
        )
    else:
        print(
            "Rotated to service",
            current_service_idx + 1,
            "of",
            len(svc_services),
        )


def get_local_time():
    """Return (hh, mm, ss) local using fixed UTC offset."""
    try:
        t = time.localtime(time.time() + UTC_OFFSET_HOURS * 3600)
        return t[3], t[4], t[5]
    except Exception:
        return None, None, None


def refresh_service():
    global svc_state, ticker_text, ticker_w, ticker_px
    global last_source, wifi_ok, svc_services, current_service_idx, last_rotate_ms

    source_label = None
    services = load_local_services()
    if services:
        source_label = "local"
    elif REMOTE_JSON_URL and wifi_ok:
        services = load_remote_services()
        if services:
            source_label = "remote"
        else:
            wifi_ok = False

    if not services:
        print("No service data available; retaining previous values")
        return False

    svc_services = services
    current_service_idx = 0
    apply_service(svc_services[current_service_idx])
    last_source = source_label or "defaults"
    last_rotate_ms = time.ticks_ms()
    if len(svc_services) > 1:
        print("Loaded", len(svc_services), "services from", last_source)
    else:
        print("Service updated from", last_source)
    return True


# -----------------------------------------------------------------------------
# Drawing
# -----------------------------------------------------------------------------

def draw():
    graphics.set_pen(BLACK_PEN)
    graphics.clear()
    graphics.set_font(FONT_NAME)

    if svc_state is None:
        graphics.set_pen(graphics.create_pen(*COL_ORANGE))
        graphics.text("NO DATA", 10, 12, scale=SCALE_MAIN)
        i75.update()
        return

    if last_source == "remote":
        dot_col = COL_GREEN
    elif last_source == "local":
        dot_col = COL_BLUE
    else:
        dot_col = COL_ORANGE
    graphics.set_pen(graphics.create_pen(*dot_col))
    graphics.pixel(0, 0)

    sched, dest, status, _ = svc_state

    status_text = status.upper()
    status_pen = COL_ORANGE
    if "ON" in status_text and "TIME" in status_text:
        status_pen = COL_GREEN
    elif any(word in status_text for word in ("CANCEL", "DELAY", "LATE")):
        status_pen = COL_RED

    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

    status_w = int(graphics.measure_text(status_text, SCALE_MAIN))
    left_w = DISPLAY_WIDTH - status_w - 3

    graphics.set_clip(0, 0, max(0, left_w), 8)
    graphics.text(sched.upper(), 1, 0, scale=SCALE_MAIN)

    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
    graphics.set_pen(graphics.create_pen(*status_pen))
    graphics.text(status_text, DISPLAY_WIDTH - status_w, 0, scale=SCALE_MAIN)

    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.set_clip(0, 8, DISPLAY_WIDTH, 8)
    graphics.text(dest.upper(), 1, 8, scale=SCALE_MAIN)

    if ticker_text:
        graphics.set_clip(0, 16, DISPLAY_WIDTH, 8)
        graphics.set_pen(graphics.create_pen(*COL_ORANGE))
        x1 = 1 - ticker_px
        graphics.text(ticker_text, x1, 16, scale=SCALE_MAIN)
        graphics.text(ticker_text, x1 + ticker_w, 16, scale=SCALE_MAIN)

    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
    hh, mm, ss = get_local_time()
    tstr = "--:--:--" if hh is None else f"{hh:02d}:{mm:02d}:{ss:02d}"
    tw = int(graphics.measure_text(tstr, SCALE_CLOCK))
    tx = (DISPLAY_WIDTH - tw) // 2
    graphics.text(tstr, tx, 24, scale=SCALE_CLOCK)

    i75.update()


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------

def main():
    global svc_state, ticker_text, ticker_w, ticker_px
    global last_fetch_ms, last_scroll_ms, last_rotate_ms
    global wifi_ok, svc_services, current_service_idx
    global last_button_state, last_button_ms

    graphics.set_font(FONT_NAME)

    default_service = (
        DEFAULT_SERVICE["sched"],
        DEFAULT_SERVICE["destination"],
        DEFAULT_SERVICE["status"],
        DEFAULT_SERVICE["calling"],
    )

    svc_services = [default_service]
    current_service_idx = 0
    apply_service(default_service)

    now_ms = time.ticks_ms()
    last_fetch_ms = now_ms
    last_scroll_ms = now_ms
    last_rotate_ms = now_ms
    last_button_ms = now_ms
    last_button_state = i75.switch_pressed(SWITCH_A)

    if REMOTE_JSON_URL:
        wifi_ok = connect_wifi()
        if wifi_ok:
            sync_time()

    refresh_service()

    while True:
        now = time.ticks_ms()

        if time.ticks_diff(now, last_fetch_ms) >= FETCH_INTERVAL * 1000:
            last_fetch_ms = now
            updated = refresh_service()
            if not updated and REMOTE_JSON_URL and not wifi_ok:
                wifi_ok = connect_wifi()
                if wifi_ok:
                    sync_time()
                    refresh_service()

        if len(svc_services) > 1 and time.ticks_diff(now, last_rotate_ms) >= SERVICE_ROTATE_INTERVAL * 1000:
            advance_service(now, manual=False)

        pressed = i75.switch_pressed(SWITCH_A)
        if pressed != last_button_state and time.ticks_diff(now, last_button_ms) >= BUTTON_DEBOUNCE_MS:
            last_button_ms = now
            last_button_state = pressed
            if pressed:
                advance_service(now, manual=True)

        if ticker_text and time.ticks_diff(now, last_scroll_ms) >= TICKER_MS:
            last_scroll_ms = now
            ticker_px = (ticker_px + TICKER_STEP_PX) % ticker_w

        draw()
        time.sleep_ms(10)


if __name__ == "__main__":
    main()
