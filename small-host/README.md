# Small host: game server, one JVM at a time

For a host with about 1 GB of memory, such as the `google` e2-micro. The
blue/green deployer in the repository root needs two game servers up during a
swap, which does not fit here.

`rolling-deploy.sh` swaps one at a time instead. When a new image is pulled:

1. It calls the drain endpoint. The server goes to DRAINING and takes no new
   matches; the lobby places them on other hosts.
2. It waits for the container to exit by itself once its last match ends, up to
   `DRAIN_TIMEOUT` (default 1 hour). After that it stops the container with 30
   seconds of SIGTERM grace.
3. `docker compose up -d --force-recreate game` starts the new image.
4. It waits for `/actuator/health` to report UP and logs the last 50 lines if it
   does not.

No match is cut off unless the timeout passes. The cost is that this host takes
no new matches from the start of the drain until the new server is up, which can
be as long as the longest running match. If this is the only game server the
lobby knows, new matches fail in that window.

`game-deploy.timer` runs the script every 60 seconds. `flock` makes a tick skip
while the previous one still waits.

## Setup

1. As root: `sudo ./bootstrap.sh <login-user>` installs Docker Engine, a 2 GB
   swap file and the timer. Log in again afterwards.
2. `docker login ghcr.io` with a token that has `read:packages`.
3. Copy `game.env.example` to `game.env` and `env.example` to `.env`, and fill
   them in. Production points at the production database and account server.
4. If the game server presents a service token to the lobby, put it in `./keys`
   and set `JWT_FILE_PATH` in `game.env`.
5. Open `PUBLIC_PORT` (default 7080) in the cloud firewall.
6. `./rolling-deploy.sh` once by hand to start the first container, then watch
   `journalctl -u game-deploy -f`.

## Memory

| | Limit |
|---|---|
| game (JVM heap 320 MB, metaspace 128 MB) | 640 MB |

The rest of the 964 MB goes to the OS; the swap file absorbs spikes. Change the
heap with `JAVA_HEAP_MB` in `.env`.
