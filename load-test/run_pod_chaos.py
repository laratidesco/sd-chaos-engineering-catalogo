"""PodChaos driver: kills one product-service pod under active load and
measures the time Kubernetes takes to detect and recover from it."""

import json
import time
from pathlib import Path

import chaos_lib as lib

MANIFEST = Path(__file__).parent.parent / "k8s" / "chaos" / "resiliente" / "pod-chaos.yaml"
RESULTS_DIR = Path(__file__).parent / "results" / "pod-chaos"

BASELINE_SECONDS = 30
OBSERVATION_SECONDS = 60
TOTAL_K6_SECONDS = BASELINE_SECONDS + OBSERVATION_SECONDS


def sample():
    state = lib.get_circuit_state()
    pods = []
    try:
        data = lib.kubectl_json("get", "pods", "-l", "app=product-service")
        for item in data.get("items", []):
            conditions = {
                c["type"]: c["status"] for c in item.get("status", {}).get("conditions", [])
            }
            restarts = sum(
                cs.get("restartCount", 0) for cs in item.get("status", {}).get("containerStatuses", [])
            )
            pods.append({
                "name": item["metadata"]["name"],
                "phase": item.get("status", {}).get("phase"),
                "ready": conditions.get("Ready"),
                "restart_count": restarts,
            })
    except RuntimeError:
        pass
    return {"circuit_state": state, "pods": pods}


def derive_timings(timeline_path, t_attack):
    with open(timeline_path) as f:
        records = [json.loads(line) for line in f if line.strip()]

    pod_names_before = None
    for rec in records:
        if rec["t"] < t_attack:
            pod_names_before = {p["name"] for p in rec.get("pods", [])}

    detection_t = None
    recovery_t = None
    for rec in records:
        if rec["t"] < t_attack or pod_names_before is None:
            continue
        names_now = {p["name"] for p in rec.get("pods", [])}
        if detection_t is None and names_now != pod_names_before:
            detection_t = rec["t"]
        new_names = names_now - pod_names_before
        if recovery_t is None:
            for p in rec.get("pods", []):
                if p["name"] in new_names and p.get("ready") == "True":
                    recovery_t = rec["t"]
                    break
    return detection_t, recovery_t


def run():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    k6_proc, k6_log = lib.start_k6(TOTAL_K6_SECONDS, RESULTS_DIR)
    timeline_path = RESULTS_DIR / "timeline.jsonl"
    poller = lib.Poller(sample, timeline_path, interval=1.0).start()

    time.sleep(BASELINE_SECONDS)

    t_attack = lib.apply_chaos(MANIFEST)
    time.sleep(OBSERVATION_SECONDS)
    t_delete = lib.delete_chaos(MANIFEST)

    steady = lib.wait_steady_state(timeout=60)

    poller.stop()
    k6_proc.wait(timeout=15)
    k6_log.close()

    t_end = time.time()
    detection_t, recovery_t = derive_timings(timeline_path, t_attack)

    attack_window = (t_attack - t_start, t_attack - t_start + 1)
    charts_dir = RESULTS_DIR / "charts"
    cpu = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.cpu_query("product-service"), t_start, t_end), t_start
    )
    mem = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.memory_query("product-service"), t_start, t_end), t_start
    )
    errors = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.error_rate_query("api-gateway"), t_start, t_end), t_start
    )
    lib.plot_timeseries([cpu], "CPU — product-service", "cores", charts_dir / "cpu.png", attack_window)
    lib.plot_timeseries([mem], "Memória — product-service", "bytes", charts_dir / "memory.png", attack_window)
    lib.plot_timeseries(
        [errors], "Taxa de erro (4xx/5xx) — api-gateway", "fração de requisições",
        charts_dir / "error_rate.png", attack_window,
    )

    events = {
        "experiment": "pod-chaos",
        "t_start": t_start,
        "t_attack": t_attack,
        "t_delete": t_delete,
        "t_end": t_end,
        "steady_state_confirmed": steady,
        "detection_time_seconds": (detection_t - t_attack) if detection_t else None,
        "recovery_time_seconds": (recovery_t - t_attack) if recovery_t else None,
        "manifest": str(MANIFEST),
        "config": {"action": "pod-kill", "mode": "one", "gracePeriod": 0},
    }
    (RESULTS_DIR / "events.json").write_text(json.dumps(events, indent=2))
    return events


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
