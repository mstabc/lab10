
# Compare how different DB timeout values affect system behaviour under injected latency.


import argparse
import time
import statistics
import requests
import os
import concurrent.futures

API_URL = os.getenv("API_URL", "http://localhost:9090")
FAULT_INJECTOR = os.getenv("FAULT_INJECTOR_URL", "http://localhost:5001")


def set_delay(ms: int):
    requests.post(f"{FAULT_INJECTOR}/delay/{ms}", timeout=3)
    print(f"[fault] DB delay → {ms} ms")


def reset_faults():
    requests.post(f"{FAULT_INJECTOR}/reset", timeout=3)


def send_request(student_id: int, client_timeout: float) -> dict:
    start = time.time()
    try:
        r = requests.get(
            f"{API_URL}/register-course",
            params={"student_id": f"s{student_id}", "course_id": "SENG468"},
            timeout=client_timeout,
        )
        latency = (time.time() - start) * 1000
        return {"ok": r.status_code == 200, "latency_ms": latency,
                "status": r.status_code, "fast_fail": False}
    except requests.exceptions.Timeout:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": "client_timeout", "fast_fail": True}
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": str(e), "fast_fail": False}


def run_load(users: int, duration: int, client_timeout: float) -> list:
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
            result = send_request(req_id % 1000, client_timeout)
            with lock:
                results.append(result)
            time.sleep(0.05)

    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker) for _ in range(users)]
        concurrent.futures.wait(futures)

    return results


def print_report(label: str, results: list):
    if not results:
        return
    latencies = [r["latency_ms"] for r in results]
    errors = [r for r in results if not r["ok"]]
    fast_fails = [r for r in results if r.get("fast_fail")]

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Requests   : {len(results)}")
    print(f"  Errors     : {len(errors)}  ({100*len(errors)/max(len(results),1):.1f}%)")
    print(f"  Fast-fails : {len(fast_fails)}")
    print(f"  p50 ms     : {statistics.median(latencies):.1f}")
    print(f"  p95 ms     : {sorted(latencies)[int(0.95*len(latencies))]:.1f}")
    print(f"  Max ms     : {max(latencies):.1f}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 3, Timeouts")
    parser.add_argument("--delay",    type=int,   default=800, help="DB delay ms")
    parser.add_argument("--users",    type=int,   default=25)
    parser.add_argument("--duration", type=int,   default=15)
    args = parser.parse_args()

    set_delay(args.delay)

    # Test with no timeout (unlimited wait)
    print("\n[Phase 1] No timeout (wait forever)...")
    r1 = run_load(args.users, args.duration, client_timeout=30.0)
    print_report("NO TIMEOUT (30s max wait)", r1)

    # Test with tight timeout
    print(f"\n[Phase 2] Tight timeout (200 ms)...")
    r2 = run_load(args.users, args.duration, client_timeout=0.2)
    print_report("TIGHT TIMEOUT (200 ms)", r2)

    # Test with reasonable timeout
    print(f"\n[Phase 3] Reasonable timeout (1 s)...")
    r3 = run_load(args.users, args.duration, client_timeout=1.0)
    print_report("REASONABLE TIMEOUT (1000 ms)", r3)


    reset_faults()
    print("[cleanup] Faults cleared.\n")


if __name__ == "__main__":
    main()
