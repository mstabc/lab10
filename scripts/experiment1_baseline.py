# Sends concurrent requests to /register-course and reports latency percentiles and error rate.

import argparse
import concurrent.futures
import time
import statistics
import requests
import os

API_URL = os.getenv("API_URL", "http://localhost:9090")
FAULT_INJECTOR = os.getenv("FAULT_INJECTOR_URL", "http://localhost:5001")


def reset_faults():
    try:
        requests.post(f"{FAULT_INJECTOR}/reset", timeout=2)
        print("[setup] Fault injector reset → healthy state\n")
    except Exception as e:
        print(f"[warn] Could not reach fault injector: {e}")


def send_request(student_id: int) -> dict:
    start = time.time()
    try:
        r = requests.get(
            f"{API_URL}/register-course",
            params={"student_id": f"s{student_id}", "course_id": "SENG468"},
            timeout=10,
        )
        latency = (time.time() - start) * 1000
        return {"ok": r.status_code == 200, "latency_ms": latency, "status": r.status_code}
    except requests.exceptions.Timeout:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": "timeout"}
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": str(e)}


def run_load(users: int, duration: int):
    print(f"Running load: {users} concurrent users for {duration}s")
    print("-" * 60)

    results = []
    end_time = time.time() + duration
    request_id = 0
    lock = __import__("threading").Lock()

    def worker():
        nonlocal request_id
        while time.time() < end_time:
            with lock:
                req_id = request_id
                request_id += 1
            result = send_request(req_id % 1000)
            with lock:
                results.append(result)
            time.sleep(0.05)

    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker) for _ in range(users)]
        concurrent.futures.wait(futures)

    return results


def print_report(label: str, results: list):
    if not results:
        print("No results collected.")
        return

    latencies = [r["latency_ms"] for r in results]
    errors = [r for r in results if not r["ok"]]

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Total requests   : {len(results)}")
    print(f"  Successful       : {len(results) - len(errors)}")
    print(f"  Errors           : {len(errors)}  ({100*len(errors)/len(results):.1f}%)")
    print(f"  Latency p50      : {statistics.median(latencies):.1f} ms")
    print(f"  Latency p95      : {sorted(latencies)[int(0.95*len(latencies))]:.1f} ms")
    print(f"  Latency p99      : {sorted(latencies)[int(0.99*len(latencies))]:.1f} ms")
    print(f"  Max latency      : {max(latencies):.1f} ms")
    if errors:
        statuses = {}
        for e in errors:
            statuses[e["status"]] = statuses.get(e["status"], 0) + 1
        print(f"  Error breakdown  : {statuses}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Experiment 1, Healthy Baseline")
    parser.add_argument("--users",    type=int, default=20, help="concurrent users")
    parser.add_argument("--duration", type=int, default=20, help="test duration in seconds")
    args = parser.parse_args()

    print("  Experiment 1, Healthy Baseline")
    reset_faults()

    results = run_load(args.users, args.duration)
    print_report("BASELINE (no faults)", results)


if __name__ == "__main__":
    main()
