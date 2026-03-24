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


def send_with_retries(student_id: int, retries: int, retry_delay: float,
                      client_timeout: float = 1.5) -> dict:
  
    attempts        = 0
    total_latency   = 0.0
    last_status     = None

    for attempt in range(retries + 1):
        start = time.time()
        try:
            r = requests.get(
                f"{API_URL}/register-course",
                params={"student_id": f"s{student_id}", "course_id": "SENG468"},
                timeout=client_timeout,
            )
            elapsed = (time.time() - start) * 1000
            total_latency += elapsed
            attempts += 1
            if r.status_code == 200:
                return {"ok": True, "latency_ms": total_latency,
                        "attempts": attempts, "status": 200}
            last_status = r.status_code
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            total_latency += elapsed
            attempts += 1
            last_status = str(e)

        if attempt < retries:
            time.sleep(retry_delay)

    return {"ok": False, "latency_ms": total_latency, "attempts": attempts,
            "status": last_status}


def run_load(users: int, duration: int, retries: int,
             retry_delay: float, client_timeout: float) -> list:
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
            result = send_with_retries(req_id % 1000, retries, retry_delay, client_timeout)
            with lock:
                results.append(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker) for _ in range(users)]
        concurrent.futures.wait(futures)

    return results


def print_report(label: str, results: list):
    if not results:
        return
    latencies = [r["latency_ms"] for r in results]
    errors = [r for r in results if not r["ok"]]
    total_req = sum(r["attempts"] for r in results)

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Logical requests   : {len(results)}")
    print(f"  Actual HTTP calls  : {total_req}")
    print(f"  Errors             : {len(errors)}  ({100*len(errors)/max(len(results),1):.1f}%)")
    print(f"  Latency p50 ms     : {statistics.median(latencies):.1f}")
    print(f"  Latency p95 ms     : {sorted(latencies)[int(0.95*len(latencies))]:.1f}")
    print(f"  Max latency ms     : {max(latencies):.1f}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 4, Retry Storm")
    parser.add_argument("--users",    type=int,   default=25)
    parser.add_argument("--delay",    type=int,   default=600, help="DB delay ms")
    parser.add_argument("--duration", type=int,   default=20)
    args = parser.parse_args()

    print("  Experiment 4, Retry Storm")

    set_delay(args.delay)

    # Phase 1: no retries
    print("\n[Phase 1] No retries...")
    r1 = run_load(args.users, args.duration, retries=0,
                  retry_delay=0.0, client_timeout=1.5)
    print_report(f"NO RETRIES  ({args.users} users)", r1)

    # Phase 2: naive retries, no delay
    print("\n[Phase 2] 3 immediate retries (no backoff)...")
    r2 = run_load(args.users, args.duration, retries=3,
                  retry_delay=0.0, client_timeout=1.5)
    print_report(f"3 RETRIES, NO BACKOFF  ({args.users} users)", r2)

    # Phase 3: retries with exponential backoff
    print("\n[Phase 3] 3 retries with 500 ms backoff...")
    r3 = run_load(args.users, args.duration, retries=3,
                  retry_delay=0.5, client_timeout=1.5)
    print_report(f"3 RETRIES + 500ms BACKOFF  ({args.users} users)", r3)

    reset_faults()
    print("[cleanup] Faults cleared.\n")


if __name__ == "__main__":
    main()
