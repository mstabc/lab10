# Lab 10 – Failure Injection & Resilience Patterns

A complete Docker environment for studying how distributed systems fail
and how resilience patterns protect them.

## Quick Start

```bash
# 1. Build and start everything
docker compose up -d --build

# 2. Verify all services are running
docker compose ps

# 3. Open the runner shell
docker compose exec runner bash

# 4. Run experiments from inside the runner
python experiment1_baseline.py
python experiment2_cascading_failure.py
python experiment3_timeouts.py
python experiment4_retry_storm.py
python experiment5_circuit_breaker.py
python experiment6_bulkhead.py
```

## Architecture

```
Browser / Load Generator
        │
        ▼
  Nginx :9090  (load balancer)
   ┌─────┴─────┐
   ▼           ▼
 api1:5000  api2:5000   (Flask)
   │              │
   ├── Redis :6379     (cache)
   ├── MongoDB :27017  (database)
   └── FaultInjector :5001
```

## Fault Injector API

Control failure scenarios without touching the application code:

```bash
# Add 500 ms latency to every DB call
curl -X POST http://localhost:5001/delay/500

# Check current fault state
curl http://localhost:5001/status

# Remove all faults
curl -X POST http://localhost:5001/reset
```

## Application Endpoints

| Endpoint             | Description                        | Pattern tested       |
|----------------------|------------------------------------|----------------------|
| `GET /health`        | Instance health check              | –                    |
| `GET /status`        | Resilience configuration           | –                    |
| `GET /register-course` | Critical path (DB write)         | Timeout, CB, Bulkhead|
| `GET /view-courses`  | Cached catalogue                   | Cache vs DB          |
| `GET /analytics`     | Non-critical aggregate             | Bulkhead             |

## Environment Variables (api containers)

| Variable               | Default  | Description                        |
|------------------------|----------|------------------------------------|
| `DB_TIMEOUT_MS`        | `5000`   | MongoDB socket timeout             |
| `ENABLE_RETRIES`       | `false`  | Enable built-in retries            |
| `RETRY_COUNT`          | `3`      | Number of retry attempts           |
| `ENABLE_CIRCUIT_BREAKER` | `false` | Enable circuit breaker            |
| `CB_FAILURE_THRESHOLD` | `5`      | Failures before circuit opens      |
| `CB_RECOVERY_TIMEOUT_S`| `15`     | Seconds before half-open probe     |

Change these in `docker-compose.yml` and run `docker compose up -d` to apply.

## Teardown

```bash
docker compose down -v   # stop and remove all volumes
```
