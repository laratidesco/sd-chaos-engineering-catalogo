"""Shared orchestration helpers for the Chaos Mesh experiment drivers.

Runs k6 as a subprocess, applies/deletes chaos manifests idempotently,
polls cluster/app state in a background thread, queries Prometheus for
range data, and renders matplotlib charts. See load-test/run_all.py for
how the three experiment drivers compose these primitives.
"""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

GATEWAY_URL = "http://localhost:8080"
PROMETHEUS_URL = "http://localhost:9090"
K6_SCRIPT = Path(__file__).parent / "k6-script.js"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def kubectl_json(*args):
    result = run(["kubectl", *args, "-o", "json"])
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr}")
    return json.loads(result.stdout)


def apply_chaos(manifest_path):
    run(["kubectl", "delete", "-f", str(manifest_path), "--ignore-not-found"])
    result = run(["kubectl", "apply", "-f", str(manifest_path)])
    if result.returncode != 0:
        raise RuntimeError(f"kubectl apply -f {manifest_path} failed: {result.stderr}")
    return time.time()


def delete_chaos(manifest_path):
    run(["kubectl", "delete", "-f", str(manifest_path), "--ignore-not-found"])
    return time.time()


def wait_port_open(host, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def start_port_forward(target, local_port, remote_port, namespace=None):
    cmd = ["kubectl", "port-forward"]
    if namespace:
        cmd += ["-n", namespace]
    cmd += [target, f"{local_port}:{remote_port}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port_open("localhost", local_port, timeout=30):
        proc.terminate()
        raise RuntimeError(f"port-forward for {target} on {local_port} never became ready")
    return proc


def ensure_port_forward_alive(proc, target, local_port, remote_port, namespace=None):
    if proc.poll() is not None:
        return start_port_forward(target, local_port, remote_port, namespace)
    return proc


def start_k6(duration_seconds, results_dir, base_url=GATEWAY_URL):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(results_dir / "k6.log", "w")
    env = {
        **os.environ,
        "BASE_URL": base_url,
        "LOAD_DURATION": f"{duration_seconds}s",
    }
    proc = subprocess.Popen(
        [
            "k6", "run",
            "--out", f"json={results_dir / 'k6-metrics.json'}",
            "--summary-export", str(results_dir / "k6-summary.json"),
            str(K6_SCRIPT),
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return proc, log_file


def get_circuit_state(base_url=GATEWAY_URL):
    try:
        r = requests.get(f"{base_url}/health", timeout=3)
        return r.json().get("circuit_state")
    except Exception:
        return None


def get_deployment_ready(name, namespace="default"):
    d = kubectl_json("get", "deployment", name, "-n", namespace)
    desired = d["spec"].get("replicas", 0)
    ready = d.get("status", {}).get("readyReplicas", 0)
    return ready, desired


def wait_steady_state(timeout=60, interval=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_circuit_state()
        try:
            gw_ready, gw_desired = get_deployment_ready("api-gateway")
            ps_ready, ps_desired = get_deployment_ready("product-service")
        except RuntimeError:
            gw_ready = gw_desired = ps_ready = ps_desired = -1
        if (
            state == "closed"
            and gw_ready == gw_desired
            and ps_ready == ps_desired
            and ps_ready > 0
        ):
            return True
        time.sleep(interval)
    return False


class Poller:
    """Calls sample_fn() every interval seconds on a background thread,
    appending {"t": unix_ts, **sample_fn()} as JSON Lines to out_path."""

    def __init__(self, sample_fn, out_path, interval=2.0):
        self.sample_fn = sample_fn
        self.out_path = Path(out_path)
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._file = None

    def start(self):
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.out_path, "w")
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            ts = time.time()
            try:
                sample = self.sample_fn()
            except Exception as exc:
                sample = {"error": str(exc)}
            record = {"t": ts, **sample}
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)
        if self._file:
            self._file.close()


def query_prometheus_range(promql, start_ts, end_ts, step="10s", prom_url=PROMETHEUS_URL):
    resp = requests.get(
        f"{prom_url}/api/v1/query_range",
        params={"query": promql, "start": start_ts, "end": end_ts, "step": step},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data["status"] != "success" or not data["data"]["result"]:
        return []
    return data["data"]["result"]


def prom_series_to_points(result, t0):
    """First (and, for our pre-aggregated sum(...) queries, only) series
    from a range-query result, as (seconds_since_t0, value) pairs."""
    if not result:
        return []
    values = result[0]["values"]
    return [(float(ts) - t0, float(val)) for ts, val in values]


def plot_timeseries(series, title, ylabel, out_path, attack_window=None, labels=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for i, s in enumerate(series):
        if not s:
            continue
        xs = [p[0] for p in s]
        ys = [p[1] for p in s]
        label = labels[i] if labels else None
        ax.plot(xs, ys, label=label, linewidth=1.8)
    if attack_window:
        ax.axvspan(attack_window[0], attack_window[1], color="red", alpha=0.12, label="Ataque")
    ax.set_title(title)
    ax.set_xlabel("segundos desde o início do baseline")
    ax.set_ylabel(ylabel)
    if labels or attack_window:
        ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_step(series, title, ylabel, out_path, attack_window=None, hlines=None, label=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    if series:
        xs = [p[0] for p in series]
        ys = [p[1] for p in series]
        ax.step(xs, ys, where="post", linewidth=2, label=label)
    if attack_window:
        ax.axvspan(attack_window[0], attack_window[1], color="red", alpha=0.12, label="Ataque")
    if hlines:
        for y, text in hlines:
            ax.axhline(y, color="gray", linestyle="--", linewidth=1)
            ax.text(0, y, text, fontsize=8, va="bottom")
    ax.set_title(title)
    ax.set_xlabel("segundos desde o início do baseline")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def mean_latency_query(job):
    return (
        f'sum(rate(http_request_duration_seconds_sum{{job="{job}"}}[1m])) / '
        f'sum(rate(http_request_duration_seconds_count{{job="{job}"}}[1m]))'
    )


def slow_fraction_query(job):
    return (
        f'1 - (sum(rate(http_request_duration_seconds_bucket{{job="{job}", le="1.0"}}[1m])) / '
        f'sum(rate(http_request_duration_seconds_count{{job="{job}"}}[1m])))'
    )


def error_rate_query(job):
    # "or vector(0)" avoids an empty result when there are zero 4xx/5xx
    # samples at all yet (PromQL division yields no series, not 0, otherwise).
    return (
        f'(sum(rate(http_requests_total{{job="{job}", status=~"4xx|5xx"}}[1m])) or vector(0)) / '
        f'sum(rate(http_requests_total{{job="{job}"}}[1m]))'
    )


def cpu_query(app_label):
    # cAdvisor via kubelet in this cluster doesn't expose an "image" label on
    # these series (confirmed live) — there's exactly one row per pod already.
    return (
        f'sum(rate(container_cpu_usage_seconds_total'
        f'{{namespace="default", pod=~"{app_label}-.*"}}[1m]))'
    )


def memory_query(app_label):
    return (
        f'sum(container_memory_working_set_bytes'
        f'{{namespace="default", pod=~"{app_label}-.*"}})'
    )
