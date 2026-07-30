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
  "NTP_SYNC_INTERVAL": 86400,
  "UTC_OFFSET_HOURS": 0,
  "AUTO_UK_DST": true
}
```

If NTP or OpenLDB logs error `-2` after Wi-Fi connects, the board is usually
connected to the network but cannot resolve hostnames. The checked-in config
sets `WIFI_DNS` to `gateway`, which uses the router as DNS, and prints the full
network config after connect.

`WIFI_DIAGNOSTICS` defaults to `false` so normal operation does not keep probing
DNS and stressing the Pico W Wi-Fi stack. Set it to `true` only while debugging.
`WIFI_RECONNECT_ON_FETCH` defaults to `true`, so the board checks the Wi-Fi
interface and reconnects only when it is about to fetch fresh service data.
`WIFI_DISCONNECT_AFTER_FETCH` defaults to `true`, so the board drops Wi-Fi after
each refresh and reconnects cleanly for the next one.

## Live custom JSON mode

To fetch service data from the local Docker proxy over Wi-Fi, update the board
`config.json`:

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

## Docker OpenLDB proxy

The proxy fetches OpenLDB from a normal computer/server, filters to a platform,
and exposes simple JSON for the board. This avoids doing DNS, TLS, and SOAP
directly on the Pico W. It refreshes OpenLDB in the background and serves
`/next.json` from cache, so the board gets a fast local response every time.

Create `.env` from the example:

```sh
cp .env.example .env
```

Edit `.env` and set:

```sh
OPENLDB_TOKEN=your-openldb-consumer-key
OPENLDB_CRS=OXN
PLATFORM_FILTER=1
REFRESH_INTERVAL_SECONDS=55
```

Start the proxy:

```sh
docker compose up -d --build
```

Test it from the Docker host:

```sh
curl http://localhost:8080/next.json
```

Then set the board `LIVE_URL` to the Docker host IP address, for example:

```json
{
  "LIVE_MODE": true,
  "LIVE_SOURCE": "json",
  "LIVE_URL": "http://192.168.1.50:8080/next.json",
  "FETCH_INTERVAL": 60
}
```

## Controls

- Switch A: advance to the next service.
- Switch B: toggle between local-first and remote-first data when `LIVE_URL` is
  configured.
