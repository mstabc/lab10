from flask import Flask, jsonify
import threading

app = Flask(__name__)
_lock = threading.Lock()

# Current fault state
_state = {
    "delay_ms": 0,
    "error_rate": 0.0
}


@app.route("/delay", methods=["GET"])
def get_delay():
    with _lock:
        return jsonify({"delay_ms": _state["delay_ms"]})


@app.route("/delay/<int:ms>", methods=["POST"])
def set_delay(ms: int):
    with _lock:
        _state["delay_ms"] = max(0, ms)
    print(f"[fault-injector] delay set to {_state['delay_ms']} ms", flush=True)
    return jsonify({"delay_ms": _state["delay_ms"], "message": "delay updated"})


@app.route("/error-rate/<rate>", methods=["POST"])
def set_error_rate(rate: str):
    with _lock:
        _state["error_rate"] = max(0.0, min(1.0, float(rate)))
    return jsonify({"error_rate": _state["error_rate"]})


@app.route("/reset", methods=["POST"])
def reset():
    with _lock:
        _state["delay_ms"]   = 0
        _state["error_rate"] = 0.0
    print("[fault-injector] reset to healthy state", flush=True)
    return jsonify({"message": "reset to healthy state", **_state})


@app.route("/status")
def status():
    with _lock:
        return jsonify(_state)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
