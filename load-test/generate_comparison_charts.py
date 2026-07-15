"""Gera 2 graficos sobrepondo a latencia media da stack resiliente e da
stack naive na MESMA imagem (NetworkChaos e StressChaos - os 2 experimentos
que tem serie de latencia por job), para deixar visualmente obvio o quanto
o pico naive e mais alto que o resiliente.

Reusa os timestamps ja gravados em cada events.json para requisitar os
pontos brutos ao Prometheus - por isso so funciona enquanto a janela de
tempo dos experimentos ainda estiver na retencao do Prometheus (10d neste
cluster). Deve rodar logo apos `run_all.py` + `run_all_naive.py` (e e
exatamente isso que o Makefile faz, chamando este script antes do `report`).

Run: python3 load-test/generate_comparison_charts.py
"""

import json
from pathlib import Path

import chaos_lib as lib

RESULTS = Path(__file__).parent / "results"
RESULTS_NAIVE = Path(__file__).parent / "results-naive"
OUT_DIR = Path(__file__).parent / "comparison-charts"


def load_events(results_dir, exp_key):
    return json.loads((results_dir / exp_key / "events.json").read_text())


def latency_series(results_dir, exp_key, job, t_key_start="t_attack_start", t_key_end="t_attack_end"):
    events = load_events(results_dir, exp_key)
    t0, t1 = events["t_start"], events["t_end"]
    latency = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.mean_latency_query(job), t0, t1), t0
    )
    window = (events[t_key_start] - t0, events[t_key_end] - t0)
    return latency, window


def compare(exp_key, title, out_name):
    res_latency, res_window = latency_series(RESULTS, exp_key, "api-gateway")
    nai_latency, _ = latency_series(RESULTS_NAIVE, exp_key, "api-gateway-naive")
    lib.plot_timeseries(
        [res_latency, nai_latency],
        title,
        "segundos",
        OUT_DIR / out_name,
        res_window,
        labels=["resiliente", "naive"],
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prom_pf = lib.start_port_forward(
        "svc/monitoring-kube-prometheus-prometheus", 9090, 9090, namespace="monitoring"
    )
    try:
        compare(
            "network-chaos",
            "Latência média — NetworkChaos: resiliente vs. naive",
            "network-chaos-latency-comparativo.png",
        )
        compare(
            "stress-chaos",
            "Latência média — StressChaos: resiliente vs. naive",
            "stress-chaos-latency-comparativo.png",
        )
        print(f"Wrote comparison charts to {OUT_DIR}")
    finally:
        prom_pf.terminate()


if __name__ == "__main__":
    main()
