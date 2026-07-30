import json
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


OPENLDB_URL = os.getenv(
    "OPENLDB_URL",
    "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx",
)
OPENLDB_TOKEN = os.getenv("OPENLDB_TOKEN", "").strip()
OPENLDB_CRS = os.getenv("OPENLDB_CRS", "OXN").strip().upper()
OPENLDB_ROWS = int(os.getenv("OPENLDB_ROWS", "12"))
OPENLDB_TIME_WINDOW = int(os.getenv("OPENLDB_TIME_WINDOW", "119"))
OPENLDB_SOAP_VERSION = os.getenv("OPENLDB_SOAP_VERSION", "2017-10-01").strip()
OPENLDB_SOAP_ACTION_VERSION = os.getenv(
    "OPENLDB_SOAP_ACTION_VERSION",
    "2015-05-14",
).strip()
PLATFORM_FILTER = os.getenv("PLATFORM_FILTER", "1").strip().upper()
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "55"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))

cache_lock = threading.Lock()
cache = {
    "updated_at": 0,
    "payload": None,
    "error": None,
    "refreshing": False,
}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def first_text(node, name, default=""):
    for child in node.iter():
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return default


def children_named(node, name):
    return [child for child in node.iter() if local_name(child.tag) == name]


def build_openldb_request():
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
        token=OPENLDB_TOKEN,
        rows=OPENLDB_ROWS,
        crs=OPENLDB_CRS,
        time_window=OPENLDB_TIME_WINDOW,
    )
    return soap_action, body.encode("utf-8")


def parse_services(xml_bytes):
    root = ET.fromstring(xml_bytes)
    services = []

    for service in children_named(root, "service"):
        destination = ""
        destinations = children_named(service, "destination")
        if destinations:
            destination = first_text(destinations[0], "locationName")
        if not destination:
            destination = first_text(service, "destination")

        calling = []
        subsequent = children_named(service, "subsequentCallingPoints")
        search_root = subsequent[0] if subsequent else service
        for point in children_named(search_root, "callingPoint"):
            name = first_text(point, "locationName")
            if name:
                calling.append(name)

        platform = first_text(service, "platform")
        if platform.upper() in ("NONE", "UNKNOWN"):
            platform = ""

        if PLATFORM_FILTER and platform.upper() != PLATFORM_FILTER:
            continue

        services.append(
            {
                "sched": first_text(service, "std", "--:--"),
                "destination": destination or "Unknown",
                "status": first_text(service, "etd", "On time"),
                "platform": platform,
                "calling": calling,
            }
        )

    return services


def fetch_services():
    if not OPENLDB_TOKEN:
        raise RuntimeError("OPENLDB_TOKEN is not set")

    soap_action, body = build_openldb_request()
    request = urllib.request.Request(
        OPENLDB_URL,
        data=body,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"{}"'.format(soap_action),
            "Accept-Encoding": "identity",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        xml_bytes = response.read()

    services = parse_services(xml_bytes)
    return {
        "station": OPENLDB_CRS,
        "platform_filter": PLATFORM_FILTER,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": services,
    }


def refresh_cache():
    now = time.time()
    with cache_lock:
        if cache["refreshing"]:
            return cache["payload"]
        cache["refreshing"] = True
    try:
        payload = fetch_services()
        with cache_lock:
            cache["payload"] = payload
            cache["updated_at"] = now
            cache["error"] = None
        print("Refreshed", len(payload.get("services", [])), "services")
        return payload
    except Exception as exc:
        with cache_lock:
            cache["error"] = str(exc)
        print("Refresh failed:", exc)
        return None
    finally:
        with cache_lock:
            cache["refreshing"] = False


def cached_payload():
    with cache_lock:
        return cache["payload"]


def cache_status():
    with cache_lock:
        payload = cache["payload"] or {}
        return {
            "cached_services": len(payload.get("services", [])),
            "updated_at": cache["updated_at"],
            "last_error": cache["error"],
            "refreshing": cache["refreshing"],
        }


def refresh_loop():
    while True:
        refresh_cache()
        time.sleep(max(5, REFRESH_INTERVAL_SECONDS))


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            status = cache_status()
            self.send_json(
                200,
                {
                    "ok": True,
                    "station": OPENLDB_CRS,
                    "platform_filter": PLATFORM_FILTER,
                    "cached_services": status["cached_services"],
                    "last_updated": status["updated_at"],
                    "last_error": status["last_error"],
                    "refreshing": status["refreshing"],
                },
            )
            return

        if self.path.startswith("/next.json"):
            payload = cached_payload()
            if payload is None:
                self.send_json(
                    503,
                    {
                        "ok": False,
                        "error": "cache not ready",
                        "services": [],
                    },
                )
            else:
                self.send_json(200, payload)
            return

        if self.path.startswith("/refresh"):
            payload = refresh_cache() or cached_payload()
            if payload is None:
                self.send_json(502, {"ok": False, "error": "refresh failed", "services": []})
            else:
                self.send_json(200, payload)
            return

        self.send_json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.address_string(), fmt % args))


def main():
    print("Starting OpenLDB proxy on port", HTTP_PORT)
    print("Station:", OPENLDB_CRS, "platform:", PLATFORM_FILTER or "all")
    print("Background refresh interval:", REFRESH_INTERVAL_SECONDS, "seconds")
    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
