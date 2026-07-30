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

With the checked-in `config.json`, the board runs from the local
`departures.json` file and does not need Wi-Fi or a companion server.

## Live OpenLDB mode

To fetch official National Rail OpenLDB data directly from the board, update
`config.json`:

```json
{
  "LIVE_MODE": true,
  "LIVE_SOURCE": "openldb",
  "OPENLDB_CRS": "OXN",
  "OPENLDB_ROWS": 6,
  "OPENLDB_TIME_WINDOW": 119
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
