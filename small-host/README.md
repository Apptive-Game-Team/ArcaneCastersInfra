# Small host: game server, one at a time

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

| Image | Idle use | Limit |
|---|---|---|
| `ac-game-rust` (Rust) | about 16 MB | 256 MB (`GAME_MEM_LIMIT`) |
| `ac-game` (Java, heap 320 MB, metaspace 128 MB) | about 440 MB | 640 MB |

The Java image did not fit: with the Google agents and Docker the host needed
about 970 MB, so half of the JVM sat in swap and the game loop stalled for 12
seconds at a time. The Rust image leaves most of the 964 MB free. Change the
Java heap with `JAVA_HEAP_MB` in `.env`.

## Switching between the Rust and Java images

Both images speak the same contracts, so the switch is an `.env` edit:

1. Set `GAME_IMAGE` to `ghcr.io/apptive-game-team/ac-game-rust:latest` and
   `GAME_MEM_LIMIT=256m` (or back to `ac-game:latest` and `640m`).
2. The next timer tick pulls the image, drains the running server and starts the
   other one. Run `./rolling-deploy.sh` to do it at once.

The host must be able to pull `ac-game-rust` (`docker login ghcr.io` with
`read:packages`, as for `ac-game`).
