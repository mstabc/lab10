"""
1. Enables the circuit breaker on the API (via env flag toggle)
2. Injects failures
3. Watches the breaker open, then half-open, then close
"""

import argparse
import time
import statistics
import requests
import os
import concurrent.futures
import json

API_URL = os.getenv("API_URL", "http://localhost:9090")
FAULT_INJECTOR = os.getenv("FAULT_INJECTOR_URL", "http://localhost:5001")

import threading

class LocalCircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(self, threshold: int, timeout: int):
        self.threshold = threshold
        self.timeout  = timeout
        self._failures = 0
        self._state = self.CLOSED
        self._last_fail_time = 0.0
        self._lock = threading.Lock()
        self._state_changes = []

    @property
    def state(self):
        with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._last_fail_time > self.timeout:
                    self._state = self.HALF_OPEN
                    self._state_changes.append((time.time(), self.HALF_OPEN))
            return self._state

    def success(self):
        with self._lock:
            if self._state != self.CLOSED:
                self._state_changes.append((time.time(), self.CLOSED))
            self._failures = 0
            self._state = self.CLOSED

    def failure(self):
        with self._lock:
            self._failures += 1
            self._last_fail_time = time.time()
            if self._failures >= self.threshold:
                if self._state != self.OPEN:
                    self._state_changes.append((time.time(), self.OPEN))
                self._state = self.OPEN

    def status(self):
        return {"state": self.state, "failures": self._failures,
                "changes": self._state_changes}


def set_delay(ms: int):
    requests.post(f"{FAULT_INJECTOR}/delay/{ms}", timeout=3)
    print(f"[fault] DB delay → {ms} ms")


def reset_faults():
    requests.post(f"{FAULT_INJECTOR}/reset", timeout=3)


def send_request(student_id: int, cb: LocalCircuitBreaker,
                 client_timeout: float = 1.5) -> dict:
    if cb.state == LocalCircuitBreaker.OPEN:
        return {"ok": False, "latency_ms": 0, "status": "circuit_open",
                "cb_state": "OPEN"}

    start = time.time()
    try:
        r = requests.get(
            f"{API_URL}/register-course",
            params={"student_id": f"s{student_id}", "course_id": "SENG468"},
            timeout=client_timeout,
        )
        latency = (time.time() - start) * 1000
        if r.status_code == 200:
            cb.success()
            return {"ok": True, "latency_ms": latency, "status": 200,
                    "cb_state": cb.state}
        else:
            cb.failure()
            return {"ok": False, "latency_ms": latency, "status": r.status_code,
                    "cb_state": cb.state}
    except Exception:
        latency = (time.time() - start) * 1000
        cb.failure()
        return {"ok": False, "latency_ms": latency, "status": "error",
                "cb_state": cb.state}


def run_phase(label: str, users: int, duration: int,
              cb: LocalCircuitBreaker, client_timeout: float) -> list:
    print(f"\n  [{label}] running for {duration}s …")
    results = []
    end_time = time.time() + duration
    request_id = 0
    lock = threading.Lock()

    def worker():
        nonlocal request_id
        while time.time() < end_time:
            with lock:
                req_id = request_id
                request_id += 1
            result = send_request(req_id % 1000, cb, client_timeout)
            with lock:
                results.append(result)
            time.sleep(0.1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker) for _ in range(users)]
        concurrent.futures.wait(futures)

    return results


def print_report(label: str, results: list):
    if not results:
        return
    latencies = [r["latency_ms"] for r in results]
    errors = [r for r in results if not r["ok"]]
    cb_opens = [r for r in results if r.get("cb_state") == "OPEN"]
    circuit_rej = [r for r in results if r.get("status") == "circuit_open"]

    print(f"\n  {'─'*55}")
    print(f"  {label}")
    print(f"  {'─'*55}")
    print(f"  Total requests     : {len(results)}")
    print(f"  Errors             : {len(errors)}  ({100*len(errors)/max(len(results),1):.1f}%)")
    print(f"  Circuit-rejected   : {len(circuit_rej)}")
    non_zero = [l for l in latencies if l > 0]
    if non_zero:
        print(f"  p50 latency ms     : {statistics.median(non_zero):.1f}")
        print(f"  p95 latency ms     : {sorted(non_zero)[int(0.95*len(non_zero))]:.1f}")
    print(f"  {'─'*55}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 5 – Circuit Breaker")
    parser.add_argument("--users",    type=int, default=20)
    parser.add_argument("--delay",    type=int, default=1000, help="DB delay ms")
    parser.add_argument("--duration", type=int, default=15)
    args = parser.parse_args()


    cb = LocalCircuitBreaker(threshold=5, timeout=10)

    reset_faults()
    r1 = run_phase("Phase 1 – Healthy baseline", args.users, args.duration, cb,
                   client_timeout=2.0)
    print_report("Healthy baseline (no faults)", r1)
    print(f"  CB state: {cb.status()['state']}")

    print(f"\n[+] Injecting {args.delay}ms DB latency…")
    set_delay(args.delay)
    r2 = run_phase("Phase 2 – Failure injection", args.users, args.duration * 2, cb,
                   client_timeout=0.5)  
    print_report("After failure injection", r2)
    print(f"  CB state after failure: {cb.status()['state']}")
    print(f"  State transitions: {cb.status()['changes']}")


    print("\n[+] Removing fault – watching circuit breaker recover…")
    reset_faults()
    time.sleep(2) 
    r3 = run_phase("Phase 3 – Recovery", args.users, args.duration, cb,
                   client_timeout=2.0)
    print_report("After recovery", r3)
    print(f"  CB state after recovery: {cb.status()['state']}")


    reset_faults()
    print("[cleanup] Done.\n")


if __name__ == "__main__":
    main()
