# LED Trainboard

MicroPython train departure board for a Pimoroni Interstate 75 W driving a
128x32 HUB75 LED matrix.

## Run directly on the Interstate 75 W

Flash Pimoroni's Interstate 75 W MicroPython build, then copy these files to the
board filesystem:

- `main.py`
- `config.json`
- `departures.json`
- `secrets.py` if you enable live HTTP mode

With the checked-in `config.json`, the board runs in live OpenLDB mode and uses
`departures.json` as a fallback if Wi-Fi or the API is unavailable.

## Live OpenLDB mode

To fetch official National Rail OpenLDB data directly from the board, update
`config.json`:

```json
{
  "LIVE_MODE": true,
  "LIVE_SOURCE": "openldb",
  "OPENLDB_CRS": "OXN",
  "OPENLDB_ROWS": 6,
  "OPENLDB_TIME_WINDOW": 119,
  "OPENLDB_SOAP_VERSION": "2017-10-01",
  "OPENLDB_SOAP_ACTION_VERSION": "2015-05-14"
}
```

Then set Wi-Fi credentials and your OpenLDB consumer key in `secrets.py`:

```python
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
OPENLDB_TOKEN = "your-openldb-consumer-key"
```

The app uses `GetDepBoardWithDetails` so it can display departure time,
destination, live status, and subsequent calling points from one small SOAP
request.

## Clock sync

When Wi-Fi is available, the board syncs its RTC from NTP at startup and then
resyncs periodically. The checked-in config uses `pool.ntp.org`, resyncs hourly,
and applies UK daylight saving time automatically:

```json
{
  "NTP_HOST": "pool.ntp.org",
  "NTP_SYNC_INTERVAL": 3600,
  "UTC_OFFSET_HOURS": 0,
  "AUTO_UK_DST": true
}
```

If NTP or OpenLDB logs error `-2` after Wi-Fi connects, the board is usually
connected to the network but cannot resolve hostnames. The checked-in config
sets `WIFI_DNS` to `gateway`, which uses the router as DNS, and prints the full
network config after connect.

## Live custom JSON mode

To fetch service data over Wi-Fi, update `config.json`:

```json
{
  "LIVE_MODE": true,
  "LIVE_SOURCE": "json",
  "LIVE_URL": "http://192.168.1.50:8080/next.json"
}
```

Then set Wi-Fi credentials in `secrets.py`:

```python
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
```

The live endpoint may return either a single service object, a list of service
objects, or an object with a `services` array.

## Controls

- Switch A: advance to the next service.
- Switch B: toggle between local-first and remote-first data when `LIVE_URL` is
  configured.
