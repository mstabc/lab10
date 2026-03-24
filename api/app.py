
#Course Registration API
# Three endpoints

#   GET  /register-course?student_id=X&course_id=Y #Critical path; hits MongoDB (affected by fault injection)

#   GET  /view-courses #Cached path; served from Redis, DB only on cache miss

#   GET  /analytics #Non-critical; deliberately has no circuit-breaker so


import os
import time
import threading
import requests
from flask import Flask, jsonify, request
import redis
import pymongo

INSTANCE_ID = os.getenv("INSTANCE_ID", "api")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/lab10")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
FAULT_INJECTOR_URL = os.getenv("FAULT_INJECTOR_URL", "http://fault_injector:5001")

# DB timeout (Ex 3)
DB_TIMEOUT_MS = int(os.getenv("DB_TIMEOUT_MS", "5000"))

# Retry settings (Ex 4)
ENABLE_RETRIES = os.getenv("ENABLE_RETRIES", "false").lower() == "true"
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "3"))
RETRY_DELAY_S = float(os.getenv("RETRY_DELAY_S", "0.5"))

# Circuit-breaker (Ex 5)
ENABLE_CIRCUIT_BREAKER = os.getenv("ENABLE_CIRCUIT_BREAKER", "false").lower() == "true"
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_RECOVERY_TIMEOUT_S = int(os.getenv("CB_RECOVERY_TIMEOUT_S", "15"))

# Bulkhead (Ex 6)
CRITICAL_THREADS = int(os.getenv("CRITICAL_THREADS", "10"))
ANALYTICS_THREADS = int(os.getenv("ANALYTICS_THREADS", "2"))

app = Flask(__name__)

mongo_client = pymongo.MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=DB_TIMEOUT_MS,
    socketTimeoutMS=DB_TIMEOUT_MS,
    connectTimeoutMS=3000,
)
db = mongo_client.lab10
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


class CircuitBreaker:
    CLOSED = "closed"     # normal operation
    OPEN = "open"       # failing
    HALF_OPEN = "half_open"  # test for service recovery

    def __init__(self, failure_threshold: int, recovery_timeout: int):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self):
        with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state

    def record_success(self):
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN

    def status(self):
        return {
            "state":         self.state,
            "failure_count": self._failure_count,
            "threshold":     self.failure_threshold,
        }


circuit_breaker = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT_S)


# Hit MongoDB.
# The fault injector may add artificial latency between this call and the actual DB write.
def db_query_with_fault(student_id: str, course_id: str):
    
    try:
        r = requests.get(f"{FAULT_INJECTOR_URL}/delay", timeout=1)
        delay = r.json().get("delay_ms", 0) / 1000.0
        if delay > 0:
            time.sleep(delay)
    except Exception:
        pass

    # DB operation
    result = db.registrations.insert_one({
        "student_id": student_id,
        "course_id":  course_id,
        "timestamp":  time.time(),
        "instance":   INSTANCE_ID,
    })
    return str(result.inserted_id)


def db_query_with_retry(student_id: str, course_id: str):
    """Wrap db_query_with_fault in retry logic (Experiment 4)."""
    last_error = None
    attempts = RETRY_COUNT if ENABLE_RETRIES else 1
    for attempt in range(attempts):
        try:
            return db_query_with_fault(student_id, course_id)
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(RETRY_DELAY_S)
    raise last_error


def db_query_protected(student_id: str, course_id: str):
    """Wrap retry logic behind a circuit breaker (Experiment 5)."""
    if not ENABLE_CIRCUIT_BREAKER:
        return db_query_with_retry(student_id, course_id)

    state = circuit_breaker.state
    if state == CircuitBreaker.OPEN:
        raise RuntimeError("circuit_open")

    try:
        result = db_query_with_retry(student_id, course_id)
        circuit_breaker.record_success()
        return result
    except Exception as e:
        circuit_breaker.record_failure()
        raise


@app.route("/health")
def health():
    return jsonify({"status": "ok", "instance": INSTANCE_ID})


@app.route("/status")
def status():
    return jsonify({
        "instance":           INSTANCE_ID,
        "db_timeout_ms":      DB_TIMEOUT_MS,
        "retries_enabled":    ENABLE_RETRIES,
        "retry_count":        RETRY_COUNT,
        "circuit_breaker":    ENABLE_CIRCUIT_BREAKER,
        "cb_status":          circuit_breaker.status(),
        "bulkhead_critical":  CRITICAL_THREADS,
        "bulkhead_analytics": ANALYTICS_THREADS,
    })


@app.route("/register-course")
def register_course():
    start      = time.time()
    student_id = request.args.get("student_id", "s001")
    course_id  = request.args.get("course_id",  "SENG468")

    try:
        doc_id   = db_query_protected(student_id, course_id)
        duration = (time.time() - start) * 1000
        return jsonify({
            "status":      "registered",
            "student_id":  student_id,
            "course_id":   course_id,
            "doc_id":      doc_id,
            "latency_ms":  round(duration, 1),
            "instance":    INSTANCE_ID,
        })
    except RuntimeError as e:
        if "circuit_open" in str(e):
            return jsonify({
                "error":    "Service temporarily unavailable (circuit open)",
                "instance": INSTANCE_ID,
            }), 503
        return jsonify({"error": "DB error", "detail": str(e), "instance": INSTANCE_ID}), 500
    except Exception as e:
        duration = (time.time() - start) * 1000
        return jsonify({
            "error":      str(e),
            "latency_ms": round(duration, 1),
            "instance":   INSTANCE_ID,
        }), 500

@app.route("/view-courses")
def view_courses():
    cache_key = "course_catalogue"
    cached = redis_client.get(cache_key)
    if cached:
        return jsonify({"source": "cache", "instance": INSTANCE_ID,
                        "courses": cached.split(",")})
    try:
        r = requests.get(f"{FAULT_INJECTOR_URL}/delay", timeout=1)
        delay = r.json().get("delay_ms", 0) / 1000.0
        if delay > 0:
            time.sleep(delay)
    except Exception:
        pass

    courses = ["SENG468", "SENG499", "CSC485", "SENG350", "ECE458"]
    redis_client.setex(cache_key, 30, ",".join(courses))   # TTL 30 s
    return jsonify({"source": "db", "instance": INSTANCE_ID, "courses": courses})


@app.route("/analytics")
def analytics():
    start = time.time()
    try:
        r = requests.get(f"{FAULT_INJECTOR_URL}/delay", timeout=1)
        delay = r.json().get("delay_ms", 0) / 1000.0
        if delay > 0:
            time.sleep(delay)
    except Exception:
        pass

    count = db.registrations.count_documents({})
    duration = (time.time() - start) * 1000
    return jsonify({
        "total_registrations": count,
        "latency_ms":          round(duration, 1),
        "instance":            INSTANCE_ID,
    })


if __name__ == "__main__":
    if db.registrations.count_documents({}) == 0:
        db.registrations.insert_many([
            {"student_id": "seed", "course_id": "SENG468", "timestamp": time.time(), "instance": INSTANCE_ID}
        ])
    app.run(host="0.0.0.0", port=5000, threaded=True)
