"""
main.py - Interstate 75 W train departure board.

Displays departure information on a 128x32 panel using the built-in bitmap font.
Service data is sourced from a JSON file located on-device or served from a local
HTTP JSON endpoint, or directly from National Rail OpenLDB SOAP. The script
rotates through multiple services every five minutes (or on button press) and
falls back to defaults if no data is available.
"""

import time
import json

try:
    import socket
except ImportError:
    socket = None  # type: ignore

try:
    import ssl
except ImportError:
    ssl = None  # type: ignore

try:
    import machine
except ImportError:
    machine = None  # type: ignore

try:
    import urequests as requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

try:
    import network
except ImportError:
    network = None  # type: ignore

try:
    import ntptime
except ImportError:
    ntptime = None  # type: ignore

try:
    from interstate75 import Interstate75, DISPLAY_INTERSTATE75_128X32, SWITCH_A, SWITCH_B
except ImportError:
    raise RuntimeError(
        "Could not import Interstate75. Ensure the 'interstate75' module is on the MicroPython filesystem."
    )

try:
    import _thread
except ImportError:
    _thread = None  # type: ignore


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

CONFIG_JSON_PATH = "config.json"
APP_VERSION = "2026-07-30-on-demand-wifi-v18"
LOCAL_JSON_PATH = "departures.json"  # set to None to skip local file lookups
REMOTE_JSON_URL = None  # populated from config.json when LIVE_MODE is enabled
LIVE_SOURCE = "json"  # "json" or "openldb"
OPENLDB_URL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx"
OPENLDB_HOST_IP = "63.33.233.219"
OPENLDB_CRS = "OXN"
PLATFORM_FILTER = "1"
OPENLDB_ROWS = 12
OPENLDB_TIME_WINDOW = 119
OPENLDB_SOAP_VERSION = "2017-10-01"
OPENLDB_SOAP_ACTION_VERSION = "2015-05-14"
FETCH_INTERVAL = 60  # seconds between data refreshes
SERVICE_ROTATE_INTERVAL = 300  # seconds between service rotations
UTC_OFFSET_HOURS = 0  # adjust if you want local time displayed
AUTO_UK_DST = True
NTP_HOST = "pool.ntp.org"
NTP_HOST_IP = "185.51.192.63"
NTP_SYNC_INTERVAL = 86400  # seconds between clock resyncs
WIFI_COUNTRY = "GB"
WIFI_CONNECT_TIMEOUT = 20  # seconds
WIFI_DNS = "gateway"
WIFI_DNS_FALLBACKS = "gateway,1.1.1.1,8.8.8.8"
WIFI_DIAGNOSTICS = False
WIFI_RECONNECT_ON_FETCH = True
WIFI_DISCONNECT_AFTER_FETCH = True

# Manual advance debounce for Interstate75 switch inputs
BUTTON_DEBOUNCE_MS = 200

DEFAULT_SERVICE = {
    "sched": "12:24",
    "destination": "London Euston",
    "status": "On time",
    "calling": "Watford Junction, Milton Keynes Central, Rugby, Coventry, Birmingham Int'l",
    "platform": "",
}

DEFAULT_CONFIG = {
    "LIVE_MODE": False,
    "LIVE_SOURCE": LIVE_SOURCE,
    "LIVE_URL": "",
    "OPENLDB_URL": OPENLDB_URL,
    "OPENLDB_HOST_IP": OPENLDB_HOST_IP,
    "OPENLDB_CRS": OPENLDB_CRS,
    "PLATFORM_FILTER": PLATFORM_FILTER,
    "OPENLDB_ROWS": OPENLDB_ROWS,
    "OPENLDB_TIME_WINDOW": OPENLDB_TIME_WINDOW,
    "OPENLDB_SOAP_VERSION": OPENLDB_SOAP_VERSION,
    "OPENLDB_SOAP_ACTION_VERSION": OPENLDB_SOAP_ACTION_VERSION,
    "LOCAL_JSON_PATH": LOCAL_JSON_PATH,
    "FETCH_INTERVAL": FETCH_INTERVAL,
    "SERVICE_ROTATE_INTERVAL": SERVICE_ROTATE_INTERVAL,
    "UTC_OFFSET_HOURS": UTC_OFFSET_HOURS,
    "AUTO_UK_DST": AUTO_UK_DST,
    "NTP_HOST": NTP_HOST,
    "NTP_HOST_IP": NTP_HOST_IP,
    "NTP_SYNC_INTERVAL": NTP_SYNC_INTERVAL,
    "WIFI_COUNTRY": WIFI_COUNTRY,
    "WIFI_CONNECT_TIMEOUT": WIFI_CONNECT_TIMEOUT,
    "WIFI_DNS": WIFI_DNS,
    "WIFI_DNS_FALLBACKS": WIFI_DNS_FALLBACKS,
    "WIFI_DIAGNOSTICS": WIFI_DIAGNOSTICS,
    "WIFI_RECONNECT_ON_FETCH": WIFI_RECONNECT_ON_FETCH,
    "WIFI_DISCONNECT_AFTER_FETCH": WIFI_DISCONNECT_AFTER_FETCH,
    "DEFAULT_SCHED": DEFAULT_SERVICE["sched"],
    "DEFAULT_DESTINATION": DEFAULT_SERVICE["destination"],
    "DEFAULT_STATUS": DEFAULT_SERVICE["status"],
    "DEFAULT_CALLING": DEFAULT_SERVICE["calling"],
    "DEFAULT_PLATFORM": DEFAULT_SERVICE["platform"],
}

# Calling-point row timing
CALLING_SCROLL_MS = 260
CALLING_VISIBLE_CHARS = 21

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
COL_DIM_ORANGE = (80, 0, 36)
COL_GREEN = (0, 50, 255)
COL_RED = (255, 0, 0)
COL_BLUE = (0, 120, 255)

BLACK_PEN = graphics.create_pen(*COL_BLACK)

# State
svc_state = None  # (sched, destination, status, calling, platform)
svc_services = []  # list of available services
current_service_idx = 0
ticker_text = ""
ticker_w = 1
ticker_px = 0
ticker_char_idx = 0
calling_points = []
calling_scroll_idx = 0
last_fetch_ms = 0
last_scroll_ms = 0
last_rotate_ms = 0
last_source = "defaults"
last_successful_update_ms = 0
wifi_ok = False
wlan = None
last_button_state = False
last_button_ms = 0
last_source_button_state = False
last_source_button_ms = 0
svc_schedule_seconds = []
last_ntp_sync_ms = 0
ntp_synced = False
refresh_thread_running = False
pending_refresh_result = None
refresh_lock = _thread.allocate_lock() if _thread else None
prefer_remote = False
local_services_cached = None
original_getaddrinfo = getattr(socket, "getaddrinfo", None) if socket else None
dns_fallback_servers = []


def _bool_from_config(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int_from_config(value, fallback):
    try:
        return int(value)
    except Exception:
        return fallback


def load_runtime_config():
    """Load on-device runtime settings from config.json."""
    cfg = DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_JSON_PATH) as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                cfg[key] = value
    except OSError:
        print("config.json missing; using built-in defaults")
    except Exception as exc:
        print("config.json error:", exc)

    return cfg


def apply_runtime_config():
    """Apply config.json values to module-level settings."""
    global LOCAL_JSON_PATH, REMOTE_JSON_URL, FETCH_INTERVAL, SERVICE_ROTATE_INTERVAL
    global UTC_OFFSET_HOURS, DEFAULT_SERVICE, prefer_remote
    global LIVE_SOURCE, OPENLDB_URL, OPENLDB_HOST_IP, OPENLDB_CRS, PLATFORM_FILTER, OPENLDB_ROWS, OPENLDB_TIME_WINDOW
    global OPENLDB_SOAP_VERSION, OPENLDB_SOAP_ACTION_VERSION
    global AUTO_UK_DST, NTP_HOST, NTP_HOST_IP, NTP_SYNC_INTERVAL
    global WIFI_COUNTRY, WIFI_CONNECT_TIMEOUT, WIFI_DNS, WIFI_DNS_FALLBACKS
    global WIFI_DIAGNOSTICS, WIFI_RECONNECT_ON_FETCH, WIFI_DISCONNECT_AFTER_FETCH

    cfg = load_runtime_config()

    LIVE_SOURCE = str(cfg.get("LIVE_SOURCE") or LIVE_SOURCE).strip().lower()
    LOCAL_JSON_PATH = cfg.get("LOCAL_JSON_PATH") or LOCAL_JSON_PATH
    FETCH_INTERVAL = _int_from_config(cfg.get("FETCH_INTERVAL"), FETCH_INTERVAL)
    SERVICE_ROTATE_INTERVAL = _int_from_config(
        cfg.get("SERVICE_ROTATE_INTERVAL"),
        SERVICE_ROTATE_INTERVAL,
    )
    UTC_OFFSET_HOURS = _int_from_config(cfg.get("UTC_OFFSET_HOURS"), UTC_OFFSET_HOURS)
    AUTO_UK_DST = _bool_from_config(cfg.get("AUTO_UK_DST"))
    NTP_HOST = str(cfg.get("NTP_HOST") or NTP_HOST).strip()
    NTP_HOST_IP = str(cfg.get("NTP_HOST_IP") or NTP_HOST_IP).strip()
    NTP_SYNC_INTERVAL = _int_from_config(
        cfg.get("NTP_SYNC_INTERVAL"),
        NTP_SYNC_INTERVAL,
    )
    WIFI_COUNTRY = str(cfg.get("WIFI_COUNTRY") or WIFI_COUNTRY).strip().upper()
    WIFI_CONNECT_TIMEOUT = _int_from_config(
        cfg.get("WIFI_CONNECT_TIMEOUT"),
        WIFI_CONNECT_TIMEOUT,
    )
    WIFI_DNS = str(cfg.get("WIFI_DNS") or WIFI_DNS).strip()
    WIFI_DNS_FALLBACKS = str(
        cfg.get("WIFI_DNS_FALLBACKS") or WIFI_DNS_FALLBACKS
    ).strip()
    WIFI_DIAGNOSTICS = _bool_from_config(cfg.get("WIFI_DIAGNOSTICS"))
    WIFI_RECONNECT_ON_FETCH = _bool_from_config(cfg.get("WIFI_RECONNECT_ON_FETCH"))
    WIFI_DISCONNECT_AFTER_FETCH = _bool_from_config(
        cfg.get("WIFI_DISCONNECT_AFTER_FETCH")
    )

    DEFAULT_SERVICE = {
        "sched": str(cfg.get("DEFAULT_SCHED") or DEFAULT_SERVICE["sched"]),
        "destination": str(
            cfg.get("DEFAULT_DESTINATION") or DEFAULT_SERVICE["destination"]
        ),
        "status": str(cfg.get("DEFAULT_STATUS") or DEFAULT_SERVICE["status"]),
        "calling": str(cfg.get("DEFAULT_CALLING") or DEFAULT_SERVICE["calling"]),
        "platform": str(cfg.get("DEFAULT_PLATFORM") or DEFAULT_SERVICE["platform"]),
    }

    prefer_remote = _bool_from_config(cfg.get("LIVE_MODE"))
    live_url = str(cfg.get("LIVE_URL") or "").strip()
    REMOTE_JSON_URL = live_url or None
    OPENLDB_URL = str(cfg.get("OPENLDB_URL") or OPENLDB_URL).strip()
    OPENLDB_HOST_IP = str(cfg.get("OPENLDB_HOST_IP") or OPENLDB_HOST_IP).strip()
    OPENLDB_CRS = str(cfg.get("OPENLDB_CRS") or OPENLDB_CRS).strip().upper()
    PLATFORM_FILTER = str(cfg.get("PLATFORM_FILTER") or "").strip().upper()
    OPENLDB_ROWS = _int_from_config(cfg.get("OPENLDB_ROWS"), OPENLDB_ROWS)
    OPENLDB_TIME_WINDOW = _int_from_config(
        cfg.get("OPENLDB_TIME_WINDOW"),
        OPENLDB_TIME_WINDOW,
    )
    OPENLDB_SOAP_VERSION = str(
        cfg.get("OPENLDB_SOAP_VERSION") or OPENLDB_SOAP_VERSION
    ).strip()
    OPENLDB_SOAP_ACTION_VERSION = str(
        cfg.get("OPENLDB_SOAP_ACTION_VERSION") or OPENLDB_SOAP_ACTION_VERSION
    ).strip()

    print(
        "Data mode:",
        LIVE_SOURCE if prefer_remote else "local",
        "local file:",
        LOCAL_JSON_PATH,
    )


# -----------------------------------------------------------------------------
# Networking helpers
# -----------------------------------------------------------------------------

def connect_wifi():
    """Connect using secrets.py; return True if connected."""
    global wlan

    if network is None:
        print("Wi-Fi module unavailable; running without remote JSON")
        return False
    try:
        from secrets import WIFI_SSID, WIFI_PASSWORD  # type: ignore
    except ImportError:
        print("secrets.py missing; cannot connect to Wi-Fi")
        return False

    ssid = str(WIFI_SSID or "").strip()
    password = str(WIFI_PASSWORD or "")
    if not ssid:
        print("WIFI_SSID is empty; cannot connect to Wi-Fi")
        return False

    if WIFI_COUNTRY:
        try:
            network.country(WIFI_COUNTRY)
        except Exception:
            pass

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    wlan.active(True)
    try:
        wlan.config(pm=0x11140)  # reduce power saving issues on some APs
    except Exception:
        pass

    if not wlan.isconnected():
        print("Connecting to Wi-Fi:", ssid)
        wlan.connect(ssid, password)
        wait_loops = max(1, WIFI_CONNECT_TIMEOUT * 10)
        for _ in range(wait_loops):
            if wlan.isconnected():
                break
            time.sleep(0.1)

    if wlan.isconnected():
        ip = None
        subnet = None
        gateway = None
        dns = None
        try:
            ip, subnet, gateway, dns = wlan.ifconfig()
            target_dns = gateway if WIFI_DNS.lower() == "gateway" else WIFI_DNS
            if target_dns and dns != target_dns:
                try:
                    wlan.ifconfig((ip, subnet, gateway, target_dns))
                    ip, subnet, gateway, dns = wlan.ifconfig()
                except Exception as exc:
                    print("DNS override failed:", exc)
            print("Connected. IP:", ip)
            print("Network config:", ip, subnet, gateway, dns)
        except Exception as exc:
            print("Connected; ifconfig error:", exc)

        install_dns_fallback(gateway)
        if WIFI_DIAGNOSTICS:
            check_dns_resolution(NTP_HOST, 123)
            if LIVE_SOURCE == "openldb":
                check_dns_resolution(host_from_url(OPENLDB_URL), 443)
            elif REMOTE_JSON_URL:
                check_dns_resolution(host_from_url(REMOTE_JSON_URL), 80)
        return True

    try:
        print("Failed to connect to Wi-Fi; status:", wlan.status())
    except Exception:
        print("Failed to connect to Wi-Fi")
    return False


def ensure_wifi_for_fetch():
    """Reconnect Wi-Fi before a data fetch if the interface has dropped."""
    global wifi_ok, wlan

    if not live_source_configured():
        return False

    connected = False
    if wlan is not None:
        try:
            connected = wlan.isconnected()
        except Exception:
            connected = False

    if connected:
        wifi_ok = True
        return True

    print("Wi-Fi not connected before fetch; reconnecting")
    wifi_ok = connect_wifi()
    return wifi_ok


def disconnect_wifi_after_fetch():
    """Drop Wi-Fi after a refresh so the next refresh starts cleanly."""
    global wifi_ok, wlan

    if not WIFI_DISCONNECT_AFTER_FETCH or wlan is None:
        return

    try:
        if wlan.isconnected():
            wlan.disconnect()
            print("Wi-Fi disconnected after fetch")
    except Exception as exc:
        print("Wi-Fi disconnect after fetch failed:", exc)

    try:
        wlan.active(False)
    except Exception:
        pass
    wifi_ok = False


def host_from_url(url):
    text = str(url or "").strip()
    scheme_pos = text.find("://")
    if scheme_pos >= 0:
        text = text[scheme_pos + 3 :]
    slash_pos = text.find("/")
    if slash_pos >= 0:
        text = text[:slash_pos]
    colon_pos = text.find(":")
    if colon_pos >= 0:
        text = text[:colon_pos]
    return text


def check_dns_resolution(host, port):
    if socket is None or not host:
        return False
    try:
        info = socket.getaddrinfo(host, port) if original_getaddrinfo else None
        if info:
            print("DNS OK:", host, info[0][-1][0])
            return True
    except Exception as exc:
        print("DNS failed:", host, exc)
    ip = manual_dns_lookup(host)
    if ip:
        return True
    return False


def resolve_dns_server(value, gateway):
    server = str(value or "").strip()
    if not server:
        return ""
    if server.lower() == "gateway":
        return gateway or ""
    return server


def install_dns_fallback(gateway):
    global dns_fallback_servers

    dns_fallback_servers = []
    for part in WIFI_DNS_FALLBACKS.split(","):
        server = resolve_dns_server(part, gateway)
        if server and server not in dns_fallback_servers:
            dns_fallback_servers.append(server)

    if socket is None or not dns_fallback_servers:
        return

    try:
        socket.getaddrinfo = fallback_getaddrinfo  # type: ignore
    except Exception as exc:
        print("DNS getaddrinfo patch skipped:", exc)
    print("DNS fallbacks:", ", ".join(dns_fallback_servers))


def encode_dns_name(host):
    out = bytearray()
    for part in str(host).split("."):
        if part:
            out.append(len(part))
            out.extend(part.encode())
    out.append(0)
    return out


def read_u16(data, idx):
    return (data[idx] << 8) | data[idx + 1]


def skip_dns_name(data, idx):
    while idx < len(data):
        length = data[idx]
        if length & 0xC0 == 0xC0:
            return idx + 2
        if length == 0:
            return idx + 1
        idx += 1 + length
    return idx


def parse_dns_a_record(data, question_end):
    if len(data) < 12:
        return None
    answer_count = read_u16(data, 6)
    idx = question_end
    for _ in range(answer_count):
        idx = skip_dns_name(data, idx)
        if idx + 10 > len(data):
            return None
        record_type = read_u16(data, idx)
        record_class = read_u16(data, idx + 2)
        rdlen = read_u16(data, idx + 8)
        idx += 10
        if idx + rdlen > len(data):
            return None
        if record_type == 1 and record_class == 1 and rdlen == 4:
            return "{}.{}.{}.{}".format(data[idx], data[idx + 1], data[idx + 2], data[idx + 3])
        idx += rdlen
    return None


def manual_dns_lookup(host):
    if socket is None:
        return None

    query_id = b"\x12\x34"
    question = encode_dns_name(host) + b"\x00\x01\x00\x01"
    packet = query_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + question
    question_end = 12 + len(question)

    for server in dns_fallback_servers:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.settimeout(2)
            except Exception:
                pass
            sock.sendto(packet, (server, 53))
            data = sock.recv(512)
            if data[:2] != query_id:
                continue
            ip = parse_dns_a_record(data, question_end)
            if ip:
                print("Manual DNS OK:", host, ip, "via", server)
                return ip
        except Exception as exc:
            print("Manual DNS failed:", host, server, exc)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    return None


def resolve_host_with_static(host, fallback_ip):
    ip = manual_dns_lookup(host)
    if ip:
        return ip
    fallback = str(fallback_ip or "").strip()
    if fallback:
        print("Using static IP for", host, fallback)
        return fallback
    return None


def fallback_getaddrinfo(host, port, family=0, socktype=0, proto=0, flags=0):
    if original_getaddrinfo:
        try:
            return original_getaddrinfo(host, port, family, socktype, proto, flags)
        except Exception:
            pass

    fallback_ip = ""
    if host == NTP_HOST:
        fallback_ip = NTP_HOST_IP
    elif host == host_from_url(OPENLDB_URL):
        fallback_ip = OPENLDB_HOST_IP
    ip = resolve_host_with_static(host, fallback_ip)
    if not ip:
        raise OSError(-2)

    return [(socket.AF_INET, socktype, proto, "", (ip, port))]


def set_rtc_from_epoch(epoch_seconds):
    if machine is None:
        return False
    try:
        t = time.gmtime(epoch_seconds)
        machine.RTC().datetime((t[0], t[1], t[2], t[6] + 1, t[3], t[4], t[5], 0))
        return True
    except Exception as exc:
        print("RTC set failed:", exc)
    return False


def manual_ntp_sync(host):
    if socket is None:
        return False

    ip = resolve_host_with_static(host, NTP_HOST_IP)
    if not ip:
        return False

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(3)
        except Exception:
            pass
        packet = bytearray(48)
        packet[0] = 0x1B
        sock.sendto(packet, (ip, 123))
        data = sock.recv(48)
        if len(data) < 44:
            return False
        ntp_seconds = (
            (data[40] << 24)
            | (data[41] << 16)
            | (data[42] << 8)
            | data[43]
        )
        unix_seconds = ntp_seconds - 2208988800
        if set_rtc_from_epoch(unix_seconds):
            print("Time synchronised via manual NTP:", host, ip)
            return True
    except Exception as exc:
        print("Manual NTP failed:", exc)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
    return False


def sync_time(now_ms=None):
    """Sync RTC via NTP (if available)."""
    global last_ntp_sync_ms, ntp_synced

    if ntptime is None or network is None:
        print("NTP unavailable")
        return False
    try:
        if NTP_HOST:
            try:
                ntptime.host = NTP_HOST
            except Exception:
                pass
        ntptime.settime()
        last_ntp_sync_ms = time.ticks_ms() if now_ms is None else now_ms
        ntp_synced = True
        print("Time synchronised via NTP:", NTP_HOST)
        return True
    except Exception as exc:
        print("NTP sync failed:", exc)
        if manual_ntp_sync(NTP_HOST):
            last_ntp_sync_ms = time.ticks_ms() if now_ms is None else now_ms
            ntp_synced = True
            return True
        return False


def sync_time_if_due(now_ms):
    """Resync the RTC periodically once Wi-Fi is available."""
    if not wifi_ok or not NTP_SYNC_INTERVAL:
        return

    if ntp_synced:
        elapsed = time.ticks_diff(now_ms, last_ntp_sync_ms)
        if elapsed >= 0 and elapsed < NTP_SYNC_INTERVAL * 1000:
            return

    sync_time(now_ms)


def live_source_configured():
    if LIVE_SOURCE == "openldb":
        return bool(OPENLDB_URL and OPENLDB_CRS)
    return bool(REMOTE_JSON_URL)


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

    platform = str(entry.get("platform") or entry.get("plat") or "").strip()
    if platform.upper() in ("NONE", "UNKNOWN"):
        platform = ""

    return (sched, destination, status, calling, platform)


def platform_matches(service):
    if not PLATFORM_FILTER:
        return True
    if len(service) < 5:
        return False
    return str(service[4]).strip().upper() == PLATFORM_FILTER


def filter_services_by_platform(services):
    if not PLATFORM_FILTER:
        return services

    filtered = []
    for service in services:
        if platform_matches(service):
            filtered.append(service)

    if services and not filtered:
        print("No services for platform", PLATFORM_FILTER)
    elif filtered:
        print("Filtered to platform", PLATFORM_FILTER, ":", len(filtered), "services")
    return filtered


def parse_sched_to_seconds(value):
    """Convert HH:MM strings to seconds past midnight; return None if parsing fails."""
    try:
        text = str(value).strip()
        if not text or ":" not in text:
            return None
        hour_part, minute_part = text.split(":", 1)
        hour = int(hour_part)
        digits = ""
        for ch in minute_part:
            if ch.isdigit():
                digits += ch
                if len(digits) == 2:
                    break
        if len(digits) == 0:
            return None
        minute = int(digits)
        if minute < 0 or minute > 59:
            return None
        hour %= 24
        return hour * 3600 + minute * 60
    except (TypeError, ValueError):
        return None


def service_sort_key(service):
    """Sorting key placing services in time order, unknown times at end."""
    sched_seconds = parse_sched_to_seconds(service[0])
    if sched_seconds is None:
        sched_seconds = 86400
    return (sched_seconds, service[1])


def find_service_index_for_time(now_secs):
    """Return index of the first service at or after now_secs (seconds past midnight)."""
    if not svc_services or not svc_schedule_seconds:
        return 0
    fallback_idx = 0
    lowest_seen = None
    for idx, sched_secs in enumerate(svc_schedule_seconds):
        if sched_secs is None:
            continue
        if lowest_seen is None or sched_secs < lowest_seen:
            lowest_seen = sched_secs
            fallback_idx = idx
        if now_secs is not None and sched_secs >= now_secs:
            return idx
    return fallback_idx


def fetch_services_payload():
    """Load services from local/remote sources without touching display state."""
    global local_services_cached

    source_label = None
    attempted_remote = False
    services = []

    def load_local_from_cache():
        nonlocal source_label
        if local_services_cached:
            source_label = "local"
            return list(local_services_cached)
        return []

    def load_local_from_disk():
        nonlocal source_label
        global local_services_cached
        local = load_local_services()
        if local:
            source_label = "local"
            local_services_cached = list(local)
            return list(local)
        return []

    def load_remote():
        nonlocal attempted_remote, source_label
        if not live_source_configured() or not wifi_ok:
            return []
        attempted_remote = True
        remote = load_remote_services()
        if remote:
            source_label = "remote"
            return remote
        return []

    if prefer_remote and live_source_configured():
        services = load_remote()
        if not services and last_source == "remote" and svc_services:
            return {
                "services": [],
                "source": None,
                "wifi_drop": False,
            }
        if not services:
            services = load_local_from_cache()
        if not services:
            services = load_local_from_disk()
    else:
        services = load_local_from_cache()
        if not services:
            services = load_local_from_disk()
        if not services:
            services = load_remote()

    wifi_drop = False
    return {
        "services": services,
        "source": source_label,
        "wifi_drop": wifi_drop,
    }


def apply_services_payload(services, source_label):
    """Update global state with freshly loaded services."""
    global svc_services, svc_schedule_seconds, current_service_idx, last_source, last_rotate_ms
    global local_services_cached, last_successful_update_ms

    services = sorted(services, key=service_sort_key)
    svc_services = services
    svc_schedule_seconds = [parse_sched_to_seconds(svc[0]) for svc in svc_services]

    current_service_idx = 0
    hh, mm, ss = get_local_time()
    if hh is not None:
        now_secs = hh * 3600 + mm * 60 + ss
        current_service_idx = find_service_index_for_time(now_secs)

    apply_service(svc_services[current_service_idx])
    last_source = source_label or "defaults"
    if source_label == "local":
        local_services_cached = list(svc_services)
    last_rotate_ms = time.ticks_ms()
    last_successful_update_ms = last_rotate_ms
    if len(svc_services) > 1:
        print("Loaded", len(svc_services), "services from", last_source)
    else:
        print("Service updated from", last_source)


def apply_fetched_services(result):
    """Handle fetched payload (synchronous or async) and update state."""
    global wifi_ok

    if not result:
        return False

    services = result.get("services") or []
    source_label = result.get("source")
    if result.get("wifi_drop"):
        wifi_ok = False

    if not services:
        if last_source == "remote" and svc_services:
            print("Remote refresh failed; retaining previous live services")
        else:
            print("No service data available; retaining previous values")
        return False

    apply_services_payload(services, source_label)
    return True


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


def xml_unescape(text):
    """Minimal XML entity unescape for OpenLDB response fields."""
    if text is None:
        return ""
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def _tag_local_name(raw_tag):
    tag = raw_tag.strip()
    if not tag:
        return ""
    if tag[0] == "/":
        tag = tag[1:]
    if tag.endswith("/"):
        tag = tag[:-1]
    tag = tag.split(None, 1)[0]
    if ":" in tag:
        tag = tag.split(":", 1)[1]
    return tag


def find_xml_blocks(xml, tag_name, start=0):
    """Return XML element bodies by local tag name, ignoring namespace prefixes."""
    blocks = []
    pos = start
    while True:
        open_start = xml.find("<", pos)
        if open_start < 0:
            break
        open_end = xml.find(">", open_start)
        if open_end < 0:
            break

        raw_open = xml[open_start + 1 : open_end]
        if raw_open.startswith("/") or raw_open.startswith("?") or raw_open.startswith("!"):
            pos = open_end + 1
            continue

        if _tag_local_name(raw_open) != tag_name:
            pos = open_end + 1
            continue

        open_tag = raw_open.split(None, 1)[0].rstrip("/")
        if raw_open.rstrip().endswith("/"):
            blocks.append("")
            pos = open_end + 1
            continue

        close_tag = "</" + open_tag + ">"
        close_start = xml.find(close_tag, open_end + 1)
        if close_start < 0:
            pos = open_end + 1
            continue

        blocks.append(xml[open_end + 1 : close_start])
        pos = close_start + len(close_tag)

    return blocks


def find_xml_text(xml, tag_name, fallback=""):
    blocks = find_xml_blocks(xml, tag_name)
    if not blocks:
        return fallback
    return xml_unescape(blocks[0]).strip()


def build_openldb_request(token):
    """Build a compact GetDepBoardWithDetails SOAP request."""
    ldb_ns = "http://thalesgroup.com/RTTI/{}/ldb/".format(OPENLDB_SOAP_VERSION)
    soap_action = "http://thalesgroup.com/RTTI/{}/ldb/GetDepBoardWithDetails".format(
        OPENLDB_SOAP_ACTION_VERSION
    )
    body = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:typ="http://thalesgroup.com/RTTI/2013-11-28/Token/types" xmlns:ldb="{ldb_ns}">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetDepBoardWithDetailsRequest>
      <ldb:numRows>{rows}</ldb:numRows>
      <ldb:crs>{crs}</ldb:crs>
      <ldb:timeOffset>0</ldb:timeOffset>
      <ldb:timeWindow>{time_window}</ldb:timeWindow>
    </ldb:GetDepBoardWithDetailsRequest>
  </soap:Body>
</soap:Envelope>""".format(
        ldb_ns=ldb_ns,
        token=token,
        rows=OPENLDB_ROWS,
        crs=OPENLDB_CRS,
        time_window=OPENLDB_TIME_WINDOW,
    )
    return soap_action, body


def summarise_openldb_error(xml):
    """Return a short SOAP/API error from an OpenLDB response."""
    for tag_name in ("faultstring", "Text", "message", "Message"):
        text = find_xml_text(xml, tag_name)
        if text:
            return text
    return ""


def parse_openldb_services(xml):
    """Extract display services from a GetDepBoardWithDetails SOAP response."""
    services = []
    for service_xml in find_xml_blocks(xml, "service"):
        sched = find_xml_text(service_xml, "std")
        destination_xml = find_xml_blocks(service_xml, "destination")
        destination = ""
        if destination_xml:
            destination = find_xml_text(destination_xml[0], "locationName")
        if not destination:
            destination = find_xml_text(service_xml, "destination")

        status = find_xml_text(service_xml, "etd", "On time")
        platform = find_xml_text(service_xml, "platform")
        calling = []
        subsequent = find_xml_blocks(service_xml, "subsequentCallingPoints")
        search_area = subsequent[0] if subsequent else service_xml
        for calling_xml in find_xml_blocks(search_area, "callingPoint"):
            name = find_xml_text(calling_xml, "locationName")
            if name:
                calling.append(name)

        svc = normalise_service(
            {
                "sched": sched,
                "destination": destination,
                "status": status,
                "calling": calling,
                "platform": platform,
            }
        )
        if svc:
            services.append(svc)

    return services


def load_local_services():
    if not LOCAL_JSON_PATH:
        print("Local JSON path disabled")
        return []
    try:
        with open(LOCAL_JSON_PATH) as f:
            payload = json.load(f)
        services = filter_services_by_platform(extract_services(payload))
        if not services:
            print("Local JSON has no displayable services:", LOCAL_JSON_PATH)
        return services
    except OSError as exc:
        print("Local JSON missing:", LOCAL_JSON_PATH, exc)
        return []
    except Exception as exc:
        print("Local JSON error:", exc)
        return []


def load_remote_services():
    if LIVE_SOURCE == "openldb":
        return load_openldb_services()

    if not REMOTE_JSON_URL:
        return []
    if requests is None:
        print("urequests unavailable; cannot load remote JSON")
        return []
    resp = None
    try:
        resp = requests.get(REMOTE_JSON_URL)
        payload = resp.json()
        result = filter_services_by_platform(extract_services(payload))
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


def load_openldb_services():
    if requests is None:
        print("urequests unavailable; cannot load OpenLDB")
        return []

    try:
        from secrets import OPENLDB_TOKEN  # type: ignore
    except ImportError:
        print("OPENLDB_TOKEN missing from secrets.py")
        return []

    token = str(OPENLDB_TOKEN or "").strip()
    if not token:
        print("OPENLDB_TOKEN is empty")
        return []

    soap_action, body = build_openldb_request(token)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": '"' + soap_action + '"',
        "Accept-Encoding": "identity",
    }
    resp = None
    try:
        resp = requests.post(OPENLDB_URL, data=body, headers=headers)
        xml = resp.text
        status_code = getattr(resp, "status_code", None)
        if status_code and status_code != 200:
            print("OpenLDB HTTP status:", status_code)
        fault = summarise_openldb_error(xml)
        if fault:
            print("OpenLDB response:", fault[:180])
        services = filter_services_by_platform(parse_openldb_services(xml))
        if not services:
            print("OpenLDB returned no displayable services")
            print("OpenLDB response head:", xml[:240])
        return services
    except Exception as exc:
        print("OpenLDB error:", exc)
        xml = post_openldb_direct(OPENLDB_URL, body, headers)
        if xml:
            services = filter_services_by_platform(parse_openldb_services(xml))
            if not services:
                print("OpenLDB direct returned no displayable services")
                print("OpenLDB direct response head:", xml[:240])
            return services
        return []
    finally:
        if resp:
            try:
                resp.close()
            except Exception:
                pass


def path_from_url(url):
    text = str(url or "").strip()
    scheme_pos = text.find("://")
    if scheme_pos >= 0:
        text = text[scheme_pos + 3 :]
    slash_pos = text.find("/")
    if slash_pos >= 0:
        return text[slash_pos:]
    return "/"


def post_openldb_direct(url, body, headers):
    if socket is None or ssl is None:
        return None

    host = host_from_url(url)
    path = path_from_url(url)
    ip = resolve_host_with_static(host, OPENLDB_HOST_IP)
    if not ip:
        return None

    raw = None
    wrapped = None
    try:
        raw = socket.socket()
        try:
            raw.settimeout(10)
        except Exception:
            pass
        raw.connect((ip, 443))
        try:
            wrapped = ssl.wrap_socket(raw, server_hostname=host)
        except TypeError:
            wrapped = ssl.wrap_socket(raw)

        body_bytes = body.encode() if isinstance(body, str) else body
        request = (
            "POST {} HTTP/1.0\r\n"
            "Host: {}\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n".format(path, host, len(body_bytes))
        )
        for key, value in headers.items():
            request += "{}: {}\r\n".format(key, value)
        request += "\r\n"
        wrapped.write(request.encode())
        wrapped.write(body_bytes)

        chunks = []
        while True:
            data = wrapped.read(512)
            if not data:
                break
            chunks.append(data)

        response = b"".join(chunks)
        split_at = response.find(b"\r\n\r\n")
        if split_at >= 0:
            response = response[split_at + 4 :]
        text = response.decode("utf-8")
        print("OpenLDB direct response bytes:", len(text))
        return text
    except Exception as exc:
        print("OpenLDB direct error:", exc)
        return None
    finally:
        for sock in (wrapped, raw):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def strip_calling_prefix(text):
    value = text.strip()
    return value[11:].lstrip() if value.upper().startswith("CALLING AT:") else value


def build_calling_points(raw):
    base = strip_calling_prefix(raw)
    points = []
    for part in base.split(","):
        point = part.strip()
        if point:
            points.append(point.upper())
    if not points:
        points.append("NO CALLING POINTS")
    return points


def build_calling_text(raw):
    points = build_calling_points(raw)
    return "VIA " + ", ".join(points) + "     "


def format_status_for_display(status):
    """Shorten delayed expected-time statuses for the 128px display."""
    text = str(status or "").strip()
    upper = text.upper()
    exp_idx = upper.find("EXP")
    if exp_idx >= 0:
        for idx in range(exp_idx, len(text) - 4):
            if (
                text[idx].isdigit()
                and text[idx + 1].isdigit()
                and text[idx + 2] == ":"
                and text[idx + 3].isdigit()
                and text[idx + 4].isdigit()
            ):
                return "EXP " + text[idx : idx + 5]
    return upper


def update_status_text(now_ms):
    if not last_successful_update_ms:
        return "NO UPD"

    age_ms = time.ticks_diff(now_ms, last_successful_update_ms)
    if age_ms < 0:
        age_ms = 0
    age_min = age_ms // 60000

    prefix = "LIVE" if last_source == "remote" else "LOCAL"
    if age_min <= 0:
        return prefix
    if age_min < 100:
        return "{} {}M".format(prefix, age_min)
    return "{} OLD".format(prefix)


def apply_service(service):
    global svc_state, ticker_text, ticker_w, ticker_px, ticker_char_idx
    global calling_points, calling_scroll_idx

    svc_state = service
    calling_points = build_calling_points(service[3])
    ticker_text = build_calling_text(service[3])
    ticker_w = max(1, int(graphics.measure_text(ticker_text, SCALE_MAIN)))
    ticker_px = 0
    ticker_char_idx = 0
    calling_scroll_idx = 0


def visible_ticker_text():
    if not ticker_text:
        return ""
    doubled = ticker_text + ticker_text
    idx = calling_scroll_idx % len(ticker_text)
    return doubled[idx : idx + CALLING_VISIBLE_CHARS]


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

    trigger_fetch(now_ms)


def auto_advance_if_due(now_ms):
    """Advance to the next relevant service once the current departure time has passed."""
    global current_service_idx, last_rotate_ms

    if not svc_services:
        return

    hh, mm, ss = get_local_time()
    if hh is None:
        return
    now_secs = hh * 3600 + mm * 60 + ss

    if current_service_idx >= len(svc_services):
        current_service_idx = 0

    current_sched = None
    if svc_schedule_seconds and current_service_idx < len(svc_schedule_seconds):
        current_sched = svc_schedule_seconds[current_service_idx]

    target_idx = find_service_index_for_time(now_secs)

    needs_change = False
    if current_sched is None:
        needs_change = len(svc_services) > 1 and target_idx != current_service_idx
    else:
        needs_change = now_secs > current_sched and target_idx != current_service_idx

    if needs_change:
        current_service_idx = target_idx
        apply_service(svc_services[current_service_idx])
        last_rotate_ms = now_ms
        print(
            "Auto-switched to service",
            current_service_idx + 1,
            "of",
            len(svc_services),
        )

        trigger_fetch(now_ms)


def rotate_service_if_due(now_ms):
    """Rotate through available services at the configured interval."""
    if len(svc_services) <= 1 or not SERVICE_ROTATE_INTERVAL:
        return

    elapsed = time.ticks_diff(now_ms, last_rotate_ms)
    if elapsed >= SERVICE_ROTATE_INTERVAL * 1000:
        advance_service(now_ms)


def day_of_week(year, month, day):
    """Return weekday for a Gregorian date, where Sunday is 0."""
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    return (year + year // 4 - year // 100 + year // 400 + offsets[month - 1] + day) % 7


def last_sunday(year, month):
    return 31 - day_of_week(year, month, 31)


def is_uk_dst(utc_tuple):
    """Return True during UK daylight saving time for a UTC time tuple."""
    year, month, day, hour = utc_tuple[0], utc_tuple[1], utc_tuple[2], utc_tuple[3]
    if month < 3 or month > 10:
        return False
    if month > 3 and month < 10:
        return True

    boundary = last_sunday(year, month)
    if month == 3:
        return day > boundary or (day == boundary and hour >= 1)
    return day < boundary or (day == boundary and hour < 1)


def local_utc_offset_hours(utc_tuple):
    offset = UTC_OFFSET_HOURS
    if AUTO_UK_DST and is_uk_dst(utc_tuple):
        offset += 1
    return offset


def get_local_time():
    """Return (hh, mm, ss) local using NTP UTC plus configured offset rules."""
    try:
        now = time.time()
        utc_tuple = time.localtime(now)
        t = time.localtime(now + local_utc_offset_hours(utc_tuple) * 3600)
        return t[3], t[4], t[5]
    except Exception:
        return None, None, None


def refresh_service():
    result = fetch_services_payload()
    ok = apply_fetched_services(result)
    disconnect_wifi_after_fetch()
    return ok


def start_async_refresh():
    """Kick off a background refresh if threading is available."""
    global refresh_thread_running

    if _thread is None:
        return False

    if pending_refresh_result is not None:
        return True

    if refresh_thread_running:
        return True

    refresh_thread_running = True

    def worker():
        global refresh_thread_running, pending_refresh_result
        result = None
        try:
            result = fetch_services_payload()
        except Exception as exc:
            result = {
                "services": [],
                "source": None,
                "wifi_drop": False,
                "error": "Async refresh failed: {}".format(exc),
            }
        disconnect_wifi_after_fetch()
        if refresh_lock:
            refresh_lock.acquire()
            pending_refresh_result = result
            refresh_lock.release()
        else:
            pending_refresh_result = result
        refresh_thread_running = False

    try:
        _thread.start_new_thread(worker, ())
    except Exception as exc:
        print("Unable to start async refresh:", exc)
        refresh_thread_running = False
        return False

    return True


def poll_async_refresh():
    """Apply results from a background refresh when ready."""
    global pending_refresh_result

    if pending_refresh_result is None:
        return

    if refresh_lock:
        refresh_lock.acquire()
        result = pending_refresh_result
        pending_refresh_result = None
        refresh_lock.release()
    else:
        result = pending_refresh_result
        pending_refresh_result = None

    if result and result.get("error"):
        print(result["error"])

    if result:
        apply_fetched_services(result)


def trigger_fetch(now_ms, force=False):
    """Initiate a data refresh when in remote mode or when forced."""
    global last_fetch_ms, wifi_ok

    if not live_source_configured():
        return

    if not force and not prefer_remote:
        return

    if not force and FETCH_INTERVAL:
        elapsed = time.ticks_diff(now_ms, last_fetch_ms)
        if elapsed >= 0 and elapsed < FETCH_INTERVAL * 1000:
            return

    if live_source_configured() and (WIFI_RECONNECT_ON_FETCH or not wifi_ok):
        was_synced = ntp_synced
        wifi_ok = ensure_wifi_for_fetch()
        if wifi_ok and not was_synced:
            sync_time(now_ms)

    if not start_async_refresh():
        refresh_service()

    last_fetch_ms = now_ms


def toggle_data_source(now_ms):
    """Flip between local-first and remote-first service loading."""
    global prefer_remote, last_fetch_ms

    if not prefer_remote and not live_source_configured():
        print("Live source not configured; staying on local data")
        return

    prefer_remote = not prefer_remote
    mode = "REMOTE" if prefer_remote else "LOCAL"
    print("Source preference switched to", mode, "priority")

    if prefer_remote:
        trigger_fetch(now_ms, force=True)
    else:
        refresh_service()
        last_fetch_ms = now_ms


# -----------------------------------------------------------------------------
# Drawing
# -----------------------------------------------------------------------------

def draw():
    now_ms = time.ticks_ms()

    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
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

    sched, dest, status, _, platform = svc_state

    status_text = format_status_for_display(status)
    status_pen = COL_ORANGE
    if "ON" in status_text and "TIME" in status_text:
        status_pen = COL_GREEN
    elif any(word in status_text for word in ("CANCEL", "DELAY", "LATE", "EXP")):
        status_pen = COL_RED

    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

    status_w = int(graphics.measure_text(status_text, SCALE_MAIN))
    left_w = DISPLAY_WIDTH - status_w - 3

    sched_text = sched.upper()
    if platform:
        sched_text += " P" + platform.upper()

    graphics.set_clip(0, 0, max(0, left_w), 8)
    graphics.set_pen(BLACK_PEN)
    graphics.clear()
    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.text(sched_text, 1, 0, scale=SCALE_MAIN)

    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
    graphics.set_pen(graphics.create_pen(*status_pen))
    graphics.text(status_text, DISPLAY_WIDTH - status_w, 0, scale=SCALE_MAIN)

    graphics.set_clip(0, 8, DISPLAY_WIDTH, 8)
    graphics.set_pen(BLACK_PEN)
    graphics.clear()
    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.text(dest.upper(), 1, 8, scale=SCALE_MAIN)

    if calling_points:
        graphics.set_clip(0, 16, DISPLAY_WIDTH, 8)
        graphics.set_pen(BLACK_PEN)
        graphics.clear()
        graphics.set_pen(graphics.create_pen(*COL_ORANGE))
        graphics.text(visible_ticker_text(), 1, 16, scale=SCALE_MAIN)

    graphics.set_clip(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
    hh, mm, ss = get_local_time()
    tstr = "--:--:--" if hh is None else f"{hh:02d}:{mm:02d}:{ss:02d}"
    update_text = update_status_text(now_ms)
    graphics.set_clip(0, 24, DISPLAY_WIDTH, 8)
    graphics.set_pen(BLACK_PEN)
    graphics.clear()
    graphics.set_pen(graphics.create_pen(*COL_ORANGE))
    graphics.text(update_text, 1, 24, scale=SCALE_CLOCK)
    tw = int(graphics.measure_text(tstr, SCALE_CLOCK))
    tx = DISPLAY_WIDTH - tw
    graphics.text(tstr, tx, 24, scale=SCALE_CLOCK)

    i75.update()


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------

def main():
    global svc_state, ticker_text, ticker_w, ticker_px, ticker_char_idx, calling_scroll_idx
    global last_fetch_ms, last_scroll_ms, last_rotate_ms
    global wifi_ok, svc_services, current_service_idx
    global last_button_state, last_button_ms, svc_schedule_seconds
    global last_source_button_state, last_source_button_ms, prefer_remote

    apply_runtime_config()
    print("App version:", APP_VERSION)
    graphics.set_font(FONT_NAME)

    default_service = (
        DEFAULT_SERVICE["sched"],
        DEFAULT_SERVICE["destination"],
        DEFAULT_SERVICE["status"],
        DEFAULT_SERVICE["calling"],
        DEFAULT_SERVICE["platform"],
    )

    svc_services = [default_service]
    current_service_idx = 0
    svc_schedule_seconds = [parse_sched_to_seconds(default_service[0])]
    apply_service(default_service)

    now_ms = time.ticks_ms()
    last_fetch_ms = now_ms
    last_scroll_ms = now_ms
    last_rotate_ms = now_ms
    last_button_ms = now_ms
    last_button_state = i75.switch_pressed(SWITCH_A)
    last_source_button_ms = now_ms
    last_source_button_state = i75.switch_pressed(SWITCH_B)

    if prefer_remote and live_source_configured():
        wifi_ok = connect_wifi()
        if wifi_ok:
            sync_time(now_ms)

    refresh_service()

    while True:
        now = time.ticks_ms()

        poll_async_refresh()
        sync_time_if_due(now)

        auto_advance_if_due(now)
        rotate_service_if_due(now)

        pressed = i75.switch_pressed(SWITCH_A)
        if pressed != last_button_state and time.ticks_diff(now, last_button_ms) >= BUTTON_DEBOUNCE_MS:
            last_button_ms = now
            last_button_state = pressed
            if pressed:
                advance_service(now, manual=True)

        source_pressed = i75.switch_pressed(SWITCH_B)
        if (
            source_pressed != last_source_button_state
            and time.ticks_diff(now, last_source_button_ms) >= BUTTON_DEBOUNCE_MS
        ):
            last_source_button_ms = now
            last_source_button_state = source_pressed
            if source_pressed:
                toggle_data_source(now)

        if ticker_text and time.ticks_diff(now, last_scroll_ms) >= CALLING_SCROLL_MS:
            last_scroll_ms = now
            calling_scroll_idx = (calling_scroll_idx + 1) % len(ticker_text)

        draw()
        time.sleep_ms(10)


if __name__ == "__main__":
    main()
