#Inject 500 ms DB latency and observe cascading failure and then push load higher to show system collapse.

import argparse
import concurrent.futures
import time
import statistics
import requests
import os

API_URL = os.getenv("API_URL", "http://localhost:9090")
FAULT_INJECTOR = os.getenv("FAULT_INJECTOR_URL", "http://localhost:5001")


def set_delay(ms: int):
    r = requests.post(f"{FAULT_INJECTOR}/delay/{ms}", timeout=3)
    print(f"[fault] DB delay set to {ms} ms  →  {r.json()}")


def reset_faults():
    requests.post(f"{FAULT_INJECTOR}/reset", timeout=3)
    print("[fault] Reset to healthy state")


def send_request(student_id: int, endpoint: str = "/register-course") -> dict:
    start = time.time()
    try:
        r = requests.get(
            f"{API_URL}{endpoint}",
            params={"student_id": f"s{student_id}", "course_id": "SENG468"},
            timeout=15,
        )
        latency = (time.time() - start) * 1000
        return {"ok": r.status_code == 200, "latency_ms": latency, "status": r.status_code}
    except requests.exceptions.Timeout:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": "timeout"}
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": str(e)}


def run_load(users: int, duration: int, endpoint: str = "/register-course") -> list:
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
            result = send_request(req_id % 1000, endpoint)
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
    print(f"  Total requests : {len(results)}")
    print(f"  Errors         : {len(errors)}  ({100*len(errors)/max(len(results),1):.1f}%)")
    print(f"  Latency p50    : {statistics.median(latencies):.1f} ms")
    print(f"  Latency p95    : {sorted(latencies)[int(0.95*len(latencies))]:.1f} ms")
    print(f"  Max latency    : {max(latencies):.1f} ms")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 2, Cascading Failure")
    parser.add_argument("--users",    type=int, default=30)
    parser.add_argument("--delay",    type=int, default=500, help="DB delay in ms")
    parser.add_argument("--duration", type=int, default=20)
    args = parser.parse_args()

    print("  Experiment 2, Cascading Failure")

    # Phase 1: inject slow DB 
    print(f"\n[Phase 1] Injecting {args.delay} ms DB latency...")
    set_delay(args.delay)

    results_phase1 = run_load(args.users, args.duration)
    print_report(f"AFTER {args.delay}ms DB DELAY  ({args.users} users)", results_phase1)

    # Phase 2: increase load 10x
    print(f"\n[Phase 2] Increasing concurrent users to {args.users*10}...")
    results_phase2 = run_load(args.users * 10, args.duration)
    print_report(f"HIGH LOAD  ({args.users*10} users, {args.delay}ms delay)", results_phase2)

    # Phase 3: cached endpoint comparison
    print("\n[Phase 3] Comparing cached /view-courses endpoint...")
    results_cached = run_load(args.users, args.duration, "/view-courses")
    print_report(f"CACHED ENDPOINT  ({args.users} users)", results_cached)


    reset_faults()
    print("[cleanup] Faults cleared.\n")


if __name__ == "__main__":
    main()
