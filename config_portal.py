# config_portal.py — HTTP setup portal for Pico W / Interstate 75W (no AUTH_OPEN)
import network, time, machine, json, ure, ubinascii, socket

CFG_FILE = "config.json"

DEFAULTS = {
    "LIVE_MODE": True,
    "LIVE_URL": "http://192.168.1.50:8080/next.json",
    "FETCH_INTERVAL": 60,
    "UTC_OFFSET_HOURS": 0,
    "DEFAULT_SCHED": "12:24",
    "DEFAULT_DESTINATION": "LONDON EUSTON",
    "DEFAULT_STATUS": "ON TIME",
    "DEFAULT_CALLING": "WATFORD JUNCTION, MILTON KEYNES CENTRAL, RUGBY, COVENTRY, BIRMINGHAM INT'L",
}

def load_config():
    try:
        with open(CFG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return DEFAULTS.copy()

def save_config(cfg):
    with open(CFG_FILE, "w") as f:
        json.dump(cfg, f)

def write_secrets(ssid, pw):
    with open("secrets.py", "w") as f:
        f.write('WIFI_SSID=%r\nWIFI_PASSWORD=%r\n' % (ssid, pw))

def _set_ap_config(ap, **kwargs):
    """Call ap.config with compatibility for essid/ssid and password/key."""
    # Rename keys if needed
    params = {}
    for k, v in kwargs.items():
        if k == "essid":
            try:
                ap.config(essid=v)
                params["essid"] = v
            except TypeError:
                ap.config(ssid=v)
                params["ssid"] = v
        elif k == "password":
            # Some firmwares use 'key' instead of 'password'
            try:
                ap.config(password=v)
                params["password"] = v
            except TypeError:
                ap.config(key=v)
                params["key"] = v
        else:
            ap.config(**{k: v})
            params[k] = v
    return params

def _get_mac_tail():
    # Try STA MAC first, then AP MAC; fall back to zeros
    mac = None
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        mac = sta.config('mac')
    except Exception:
        pass
    if not mac:
        try:
            ap_tmp = network.WLAN(network.AP_IF)
            ap_tmp.active(True)
            mac = ap_tmp.config('mac')
        except Exception:
            pass
    if not mac:
        mac = b"\x00\x00\x00"
    return ubinascii.hexlify(mac[-3:]).decode().upper()

def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    essid = "I75-SETUP-" + _get_mac_tail()
    # Leave it OPEN by not providing a password
    _set_ap_config(ap, essid=essid)
    try:
        ip = ap.ifconfig()[0]
    except Exception:
        ip = "192.168.4.1"  # typical default
    print("AP:", essid, "IP:", ip)
    return ap, essid, ip

def url_decode(s):
    return ure.sub(r'\+', ' ',
           ure.sub(r'%([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), s))

def parse_qs(body):
    out = {}
    for kv in body.split('&'):
        if '=' in kv:
            k, v = kv.split('=', 1)
            out[url_decode(k)] = url_decode(v)
    return out

def serve():
    cfg = load_config()
    ap, essid, ap_ip = start_ap()
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(addr)
    s.listen(1)

    form = """<html><head><meta name=viewport content='width=device-width,initial-scale=1'>
    <style>body{font-family:system-ui;margin:1rem}label{display:block;margin:.4rem 0}
    input,select{width:100%%;padding:.4rem}button{padding:.5rem 1rem;margin-top:.6rem}</style></head><body>
    <h3>Interstate 75W — Setup</h3>
    <form method="POST" action="/save">
    <label>Wi-Fi SSID <input name="ssid" required></label>
    <label>Wi-Fi Password <input name="pass" type="password" placeholder="(leave blank for open)"></label>
    <label>LIVE_MODE
      <select name="LIVE_MODE"><option %s value="1">True</option><option %s value="0">False</option></select>
    </label>
    <label>LIVE_URL <input name="LIVE_URL" value="%s"></label>
    <label>FETCH_INTERVAL (seconds) <input name="FETCH_INTERVAL" type="number" value="%d"></label>
    <label>UTC_OFFSET_HOURS <input name="UTC_OFFSET_HOURS" type="number" value="%d"></label>
    <label>DEFAULT_SCHED <input name="DEFAULT_SCHED" value="%s"></label>
    <label>DEFAULT_DESTINATION <input name="DEFAULT_DESTINATION" value="%s"></label>
    <label>DEFAULT_STATUS <input name="DEFAULT_STATUS" value="%s"></label>
    <label>DEFAULT_CALLING <input name="DEFAULT_CALLING" value="%s"></label>
    <button type="submit">Save & Reboot</button>
    </form>
    <p>Connect to AP: <b>%s</b></p>
    <p>Browse to: <b>http://%s</b></p>
    </body></html>""" % (
        "selected" if cfg["LIVE_MODE"] else "", "" if cfg["LIVE_MODE"] else "selected",
        cfg["LIVE_URL"], int(cfg["FETCH_INTERVAL"]), int(cfg["UTC_OFFSET_HOURS"]),
        cfg["DEFAULT_SCHED"], cfg["DEFAULT_DESTINATION"], cfg["DEFAULT_STATUS"], cfg["DEFAULT_CALLING"],
        essid, ap_ip)

    while True:
        cl, addr = s.accept()
        try:
            req = cl.recv(4096)
            head, _, body = req.partition(b"\r\n\r\n")
            line = head.split(b"\r\n", 1)[0]
            try:
                method, path, _ = line.decode().split(" ", 2)
            except Exception:
                method, path = "GET", "/"
            if method == "POST" and path == "/save":
                data = parse_qs(body.decode())
                # save Wi-Fi creds (ok to be blank for open networks)
                write_secrets(data.get("ssid", ""), data.get("pass", ""))

                new = load_config()
                new["LIVE_MODE"] = data.get("LIVE_MODE", "1") == "1"
                new["LIVE_URL"] = data.get("LIVE_URL", new["LIVE_URL"])
                for k in ("FETCH_INTERVAL", "UTC_OFFSET_HOURS"):
                    try:
                        new[k] = int(data.get(k, new[k]))
                    except Exception:
                        pass
                for k in ("DEFAULT_SCHED", "DEFAULT_DESTINATION", "DEFAULT_STATUS", "DEFAULT_CALLING"):
                    new[k] = data.get(k, new[k])
                save_config(new)

                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type:text/html\r\n\r\n"
                        b"<meta http-equiv='refresh' content='2;url=/'><body>Saved. Rebooting\u2026</body>")
                cl.close()
                time.sleep(2)
                machine.reset()
                return
            else:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type:text/html\r\n\r\n" + form.encode())
        except Exception:
            try:
                cl.send(b"HTTP/1.1 500\r\nContent-Type:text/plain\r\n\r\nError")
            except Exception:
                pass
        finally:
            cl.close()

serve()

