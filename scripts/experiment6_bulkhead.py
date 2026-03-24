import argparse
import time
import statistics
import requests
import os
import concurrent.futures
import threading

API_URL = os.getenv("API_URL", "http://localhost:9090")
FAULT_INJECTOR = os.getenv("FAULT_INJECTOR_URL", "http://localhost:5001")


def set_delay(ms: int):
    requests.post(f"{FAULT_INJECTOR}/delay/{ms}", timeout=3)
    print(f"[fault] DB delay → {ms} ms")


def reset_faults():
    requests.post(f"{FAULT_INJECTOR}/reset", timeout=3)


def send(endpoint: str, student_id: int, client_timeout: float = 3.0) -> dict:
    start = time.time()
    try:
        r = requests.get(
            f"{API_URL}{endpoint}",
            params={"student_id": f"s{student_id}", "course_id": "SENG468"},
            timeout=client_timeout,
        )
        latency = (time.time() - start) * 1000
        return {"ok": r.status_code == 200, "latency_ms": latency,
                "status": r.status_code, "endpoint": endpoint}
    except requests.exceptions.Timeout:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": "timeout",
                "endpoint": endpoint}
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {"ok": False, "latency_ms": latency, "status": str(e),
                "endpoint": endpoint}



def run_without_bulkhead(duration: int, total_threads: int,
                          analytics_users: int, critical_users: int) -> dict:
    """
    All traffic shares the same thread pool.
    When analytics floods it, critical registrations starve.
    """
    critical_results = []
    analytics_results = []
    end_time = time.time() + duration
    lock = threading.Lock()
    req_id = 0

    def analytics_worker():
        nonlocal req_id
        while time.time() < end_time:
            with lock:
                rid = req_id; req_id += 1
            r = send("/analytics", rid, client_timeout=5.0)
            with lock:
                analytics_results.append(r)
            time.sleep(0.02)

    def critical_worker():
        nonlocal req_id
        while time.time() < end_time:
            with lock:
                rid = req_id; req_id += 1
            r = send("/register-course", rid, client_timeout=2.0)
            with lock:
                critical_results.append(r)
            time.sleep(0.05)

    # Shared pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as pool:
        futures = (
            [pool.submit(analytics_worker) for _ in range(analytics_users)] +
            [pool.submit(critical_worker)  for _ in range(critical_users)]
        )
        concurrent.futures.wait(futures)

    return {"critical": critical_results, "analytics": analytics_results}


def run_with_bulkhead(duration: int, critical_pool: int, analytics_pool: int,
                       analytics_users: int, critical_users: int) -> dict:
    """
    Separate thread pools for critical and non-critical traffic.
    Analytics can't consume critical threads.
    """
    critical_results = []
    analytics_results = []
    end_time = time.time() + duration
    lock = threading.Lock()
    req_id = 0

    def analytics_worker():
        nonlocal req_id
        while time.time() < end_time:
            with lock:
                rid = req_id; req_id += 1
            r = send("/analytics", rid, client_timeout=5.0)
            with lock:
                analytics_results.append(r)
            time.sleep(0.02)

    def critical_worker():
        nonlocal req_id
        while time.time() < end_time:
            with lock:
                rid = req_id; req_id += 1
            r = send("/register-course", rid, client_timeout=2.0)
            with lock:
                critical_results.append(r)
            time.sleep(0.05)

    # Separate pools (the bulkhead)
    with concurrent.futures.ThreadPoolExecutor(max_workers=analytics_pool) as apool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=critical_pool)  as cpool:
        af = [apool.submit(analytics_worker) for _ in range(analytics_users)]
        cf = [cpool.submit(critical_worker)  for _ in range(critical_users)]
        concurrent.futures.wait(af + cf)

    return {"critical": critical_results, "analytics": analytics_results}


def print_endpoint_report(label: str, critical: list, analytics: list):
    def summarise(results: list, name: str):
        if not results:
            return
        lat    = [r["latency_ms"] for r in results]
        errors = [r for r in results if not r["ok"]]
        p95    = sorted(lat)[int(0.95 * len(lat))]
        print(f"    {name:20s} | req={len(results):4d} | err={len(errors):3d} "
              f"({100*len(errors)/max(len(results),1):4.1f}%) | "
              f"p50={statistics.median(lat):6.1f}ms | p95={p95:6.1f}ms")

    print(f"\n  {'─'*70}")
    print(f"  {label}")
    print(f"  {'─'*70}")
    summarise(critical,  "/register-course  (critical)")
    summarise(analytics, "/analytics        (non-critical)")
    print(f"  {'─'*70}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 6 – Bulkhead")
    parser.add_argument("--delay",    type=int, default=500,  help="DB delay ms")
    parser.add_argument("--duration", type=int, default=20)
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  Experiment 6 – Bulkhead Pattern")
    print("="*60)

    set_delay(args.delay)

    TOTAL_THREADS = 10
    ANALYTICS_USERS = 8
    CRITICAL_USERS = 5
    CRITICAL_POOL = 7
    ANALYTICS_POOL = 3

    # Phase 1: no bulkhead
    print(f"\n[Phase 1] WITHOUT bulkhead ({TOTAL_THREADS} shared threads, "
          f"{ANALYTICS_USERS} analytics + {CRITICAL_USERS} critical users)…")
    r1 = run_without_bulkhead(args.duration, TOTAL_THREADS,
                               ANALYTICS_USERS, CRITICAL_USERS)
    print_endpoint_report("NO BULKHEAD", r1["critical"], r1["analytics"])

    # Phase 2: with bulkhead
    print(f"\n[Phase 2] WITH bulkhead ({CRITICAL_POOL} critical threads, "
          f"{ANALYTICS_POOL} analytics threads)…")
    r2 = run_with_bulkhead(args.duration, CRITICAL_POOL, ANALYTICS_POOL,
                            ANALYTICS_USERS, CRITICAL_USERS)
    print_endpoint_report("WITH BULKHEAD", r2["critical"], r2["analytics"])



    reset_faults()
    print("[cleanup] Done.\n")


if __name__ == "__main__":
    main()
