# Small host: game server with watchtower

For a host with about 1 GB of memory, such as the `google` instance. One game
container, kept current by watchtower. A deploy stops the running container
before the new one starts, so it ends every match in progress; this is for a dev
environment. Production uses the blue/green deployer in the repository root.

## Setup

1. As root: `sudo ./bootstrap.sh <login-user>` installs Docker Engine and adds a
   2 GB swap file. Log in again afterwards.
2. `docker login ghcr.io` with a token that has `read:packages`. Watchtower
   mounts `~/.docker/config.json` to pull the private image.
3. Copy `game.env.example` to `game.env` and fill it in. It must point at the
   dev database and the dev account server.
4. If the game server presents a service token to the lobby, put it in
   `./keys` and set `JWT_FILE_PATH` in `game.env`.
5. Open `PUBLIC_PORT` (default 7080) in the cloud firewall. The management
   port is bound to loopback.
6. `docker compose up -d`

## Memory

| | Limit |
|---|---|
| game (JVM heap 320 MB, metaspace 128 MB) | 640 MB |
| watchtower | 64 MB |

That is 704 MB of the 964 MB the host has, plus the OS. The swap file absorbs
spikes. Change the heap with `JAVA_HEAP_MB` in `.env` beside the compose file.
