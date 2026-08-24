# Local development: Fluxnova + OTel Collector + MLflow (Podman)

This directory contains everything needed to run the **Fluxnova engine**, the **OTel Collector**, and (optionally)
the **MLflow tracking server** as containers for local development, using [Podman](https://podman.io) — Docker also
works for the Compose-based option, since that file is a standard Compose file.

Two ways to start Fluxnova + the OTel Collector are provided — pick whichever fits your workflow (see "Compose vs.
pod" below):

- **Compose** (`docker-compose.yml`) — the standard, portable option; needs a compose provider installed.
- **A single Podman pod** (`pod-up.sh` / `pod-down.sh`) — no compose provider needed, just `podman` itself.

MLflow can also be run as its own container/pod (`mlflow-pod-up.sh` / `mlflow-pod-down.sh`) instead of a host process.

## Prerequisites

- **[Podman](https://podman.io/docs/installation)** (or Docker) installed, with a running machine/VM on Windows and
  macOS (`podman machine init && podman machine start`) — not needed on Linux.
- **A compose provider** so `podman compose ...` works — Podman itself doesn't bundle one. Install `podman-compose`:
  ```bash
  pip install podman-compose --user
  ```
  then make sure the install location is on your `PATH` (e.g. on Windows,
  `%APPDATA%\Python\Python3xx\Scripts`; on macOS/Linux, `~/.local/bin`). Verify with:
  ```bash
  podman compose version
  ```
  (Docker Desktop users already have `docker compose` built in — no separate install needed.) Not needed if you're
  only using the pod scripts below.
- **A locally built/loaded Fluxnova image** — this isn't published anywhere. Build it from the eval fork using the
  readme instructions (LINK TODO), or `podman load -i <fluxnova image tarball>`.
- **The MLflow tracking server running** (either on the host — see the main README's "Running the MLflow tracking
  server" — or as the `mlflow-local` pod below) — the OTel Collector sends traces to it via `host.containers.internal`.

## Option A: Compose

```bash
cd local-dev
cp .env.example .env
# edit .env: set FLUXNOVA_IMAGE to your locally built/loaded Fluxnova image

podman compose up -d      # or: docker compose up -d
podman compose logs -f    # tail both containers
podman compose down       # stop and remove them
```

This starts:

- **`fluxnova`** — the Fluxnova engine, published on `http://localhost:8080` (matching `fluxnova_url` in the harness
  config files), with `OTEL_EXPORTER_OTLP_ENDPOINT` pointed at the collector container.
- **`otel-collector`** — an `otlp` receiver (gRPC `4317` / HTTP `4318`, also published to the host) piped through an
  `otlphttp` exporter to the MLflow tracking server (`http://host.containers.internal:5000`, auto-suffixed with
  `/v1/traces`). Make sure the MLflow tracking server is already running before starting the stack.

See `docker-compose.yml`, `otel-collector-config.yaml`, and `.env.example` for details and adjust image names, ports,
or the collector pipeline as your actual Fluxnova image requires.

## Option B: a single Podman pod (no Compose provider needed)

`pod-up.sh` starts the same two containers grouped in a single **Podman pod** instead — a Podman/Kubernetes-native
alternative to Compose's per-service containers + bridge network. Run it from **Git Bash** on Windows (see the main
README's "A note on shells (Windows)").

```bash
cd local-dev
cp .env.example .env
# edit .env: set FLUXNOVA_IMAGE to your locally built/loaded Fluxnova image

./pod-up.sh      # start the pod
./pod-down.sh    # stop and remove it
```

This creates a `fluxnova-local` pod publishing ports `8080` (Fluxnova), `4317`/`4318` (OTel Collector gRPC/HTTP), with
both containers sharing one network namespace — so, unlike the Compose setup, they reach each other via `localhost`
rather than by service name. That's why `default-pod.yml` (mounted by the pod script, in place of `default.yml`)
points `exporterEndpoint` at `http://localhost:4317` instead of `http://otel-collector:4317`. Everything else (image,
ports, MLflow requirement) is identical to the Compose setup above.

**Compose vs. pod:** Compose is more portable (the same file also works with Docker, e.g. in CI) and needs no extra
tooling beyond a compose provider; the pod script needs only `podman` itself (no compose provider to install) and
mirrors Kubernetes Pod networking more closely, at the cost of a bespoke script instead of a standard, widely
recognised file format. Pick whichever fits your workflow — both start the same two containers.

## Optional: run the MLflow tracking server in its own Podman pod

`mlflow-pod-up.sh` / `mlflow-pod-down.sh` run the MLflow tracking server (see the main README's "Running the MLflow
tracking server") as a container instead of a host process, as its own single-container `mlflow-local` pod — kept
separate from `fluxnova-local` since it's a longer-lived dev service you likely want to start/stop independently of
any one Fluxnova run. Run from **Git Bash**:

```bash
cd local-dev
cp .env.example .env   # if you haven't already; adjust MLFLOW_IMAGE/MLFLOW_PORT/MLFLOW_BACKEND_STORE_DIR if needed

./mlflow-pod-up.sh      # start it
./mlflow-pod-down.sh    # stop and remove it (the SQLite backend store on disk is untouched)
```

This uses the official `ghcr.io/mlflow/mlflow` image, bind-mounting `harness/.mlflow` (the same path already used by
`mlflow-eval` and the host-run `mlflow server`/`mlflow ui` commands) so they all read/write the same `mlflow.db` — you
can freely switch between running MLflow as a host process and as this container. The OTel Collector's `otlphttp`
exporter (`http://host.containers.internal:5000`) reaches it exactly as it would a host-run server, since the pod's
port is published to the host either way.

Once the container is up, `mlflow-pod-up.sh` waits for it to respond, then opens the UI in your default browser
automatically (see "Troubleshooting" below for what it does if `localhost` isn't reachable). Set
`MLFLOW_OPEN_BROWSER=false` to skip the auto-open.

### Troubleshooting: `localhost:5000` unreachable from Windows, but the container is running

On some Windows/WSL2 setups, MLflow's Python server is unreachable via `localhost`/`127.0.0.1` from *outside* WSL —
`podman ps` shows it running, and it responds fine to requests made *inside* the Podman machine, but everything
through the WSL2 `localhost`-forwarding relay silently resets (`curl` reports "Empty reply from server"). This
reproduced consistently in testing even across a full `wsl --shutdown` + restart, while other containers (e.g.
`nginx`, the Fluxnova/Java container above) were unaffected on the same machine — so it appears specific to this
Python server plus this Windows/WSL2 networking configuration, not a bug in the pod script or the MLflow image
itself.

`mlflow-pod-up.sh` handles this automatically: if `http://localhost:$MLFLOW_PORT` doesn't respond within a few
seconds, it looks up the Podman machine's own IP (`wsl -d podman-machine-default -- ip -4 addr show eth0`), checks
that it's reachable, and uses it instead — both for the printed URLs and for the browser it opens. If you're doing
this manually (e.g. pointing the OTel Collector's `otlphttp` exporter somewhere), the same commands are:

```bash
wsl -d podman-machine-default -- ip -4 addr show eth0   # note the inet address, e.g. 172.28.x.x
curl http://<that-ip>:5000/version                       # works even when localhost:5000 doesn't
```

That IP can change on VM restart, so re-run `mlflow-pod-up.sh` (or the `wsl` command above) if it stops working. If
you don't hit this on your machine, `http://localhost:5000` works as documented above.

## Files

| File | Used by | Purpose |
|------|---------|---------|
| `docker-compose.yml` | Option A | Compose definition for `fluxnova` + `otel-collector` |
| `pod-up.sh` / `pod-down.sh` | Option B | Start/stop the `fluxnova-local` pod |
| `mlflow-pod-up.sh` / `mlflow-pod-down.sh` | Optional MLflow pod | Start/stop the `mlflow-local` pod |
| `default.yml` | Option A | Fluxnova config mounted by Compose (OTel plugin → `otel-collector:4317`) |
| `default-pod.yml` | Option B | Same as `default.yml`, but OTel plugin → `localhost:4317` (shared pod network namespace) |
| `otel-collector-config.yaml` | Options A & B | OTel Collector pipeline: `otlp` receiver → `otlp_http` exporter to MLflow |
| `.env.example` | All | Template — copy to `.env` and adjust image names/ports/paths |
