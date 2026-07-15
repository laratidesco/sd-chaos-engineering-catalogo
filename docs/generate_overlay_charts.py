"""Gera os graficos sobrepostos usados na Secao 6 (6.1/6.2/6.3) do relatorio
tecnico curado: o resultado da stack RESILIENTE (o mesmo dado ja mostrado nas
secoes 3/4/5) desenhado ao fundo, em amarelo claro, com o resultado da stack
NAIVE desenhado por cima, em destaque - para deixar visualmente obvio o
quanto de latencia/erro a mais a stack naive expoe sob o mesmo ataque.

Este script e um artefato pontual de geracao do relatorio de entrega (nao
faz parte do pipeline do Makefile). Precisa do cluster Minikube no ar (usa
Prometheus para latencia/taxa de erro); a serie de replicas do StressChaos
vem direto dos timeline.jsonl ja salvos em disco, sem precisar do cluster.

Run: cd load-test && ../docs generate_overlay_charts... (ver instrucoes no
final do arquivo) ou, do root do repo:
    cd load-test && python3 ../docs/generate_overlay_charts.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "load-test"))
import chaos_lib as lib  # noqa: E402

RESULTS = ROOT / "load-test" / "results"
RESULTS_NAIVE = ROOT / "load-test" / "results-naive"
OUT_DIR = Path(__file__).parent / "overlay-charts"

YELLOW_FILL = "#f5e08a"
YELLOW_LINE = "#c9a227"
NAIVE_COLOR = "#c0392b"


def load_events(results_dir, exp_key):
    return json.loads((results_dir / exp_key / "events.json").read_text())


def plot_overlay(bg_series, fg_series, title, ylabel, out_path, attack_window=None,
                  label_bg="Resiliente (ao fundo)", label_fg="Naive (em destaque)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))

    if attack_window:
        ax.axvspan(attack_window[0], attack_window[1], color="red", alpha=0.10, label="Ataque", zorder=0)

    if bg_series:
        xs = [p[0] for p in bg_series]
        ys = [p[1] for p in bg_series]
        ax.fill_between(xs, 0, ys, color=YELLOW_FILL, alpha=0.85, zorder=1, label=label_bg)
        ax.plot(xs, ys, color=YELLOW_LINE, linewidth=1.2, zorder=2)

    if fg_series:
        xs = [p[0] for p in fg_series]
        ys = [p[1] for p in fg_series]
        ax.plot(xs, ys, color=NAIVE_COLOR, linewidth=2.2, zorder=3, label=label_fg)

    ax.set_title(title)
    ax.set_xlabel("segundos desde o inicio do ataque")
    ax.set_ylabel(ylabel)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3, zorder=0)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_overlay_step(bg_series, fg_series, title, ylabel, out_path, attack_window=None,
                       label_bg="Resiliente (ao fundo)", label_fg="Naive (em destaque)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))

    if attack_window:
        ax.axvspan(attack_window[0], attack_window[1], color="red", alpha=0.10, label="Ataque", zorder=0)

    if bg_series:
        xs = [p[0] for p in bg_series]
        ys = [p[1] for p in bg_series]
        ax.fill_between(xs, 0, ys, step="post", color=YELLOW_FILL, alpha=0.85, zorder=1, label=label_bg)
        ax.step(xs, ys, where="post", color=YELLOW_LINE, linewidth=1.4, zorder=2)

    if fg_series:
        xs = [p[0] for p in fg_series]
        ys = [p[1] for p in fg_series]
        ax.step(xs, ys, where="post", color=NAIVE_COLOR, linewidth=2.4, zorder=3, label=label_fg)

    ax.set_title(title)
    ax.set_xlabel("segundos desde o inicio do ataque")
    ax.set_ylabel(ylabel)
    ax.legend(loc="center right", fontsize=8)
    ax.grid(alpha=0.3, zorder=0)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def prom_series_since_attack(results_dir, exp_key, job, query_fn, t_attack_key="t_attack_start"):
    events = load_events(results_dir, exp_key)
    t0, t1 = events["t_start"], events["t_end"]
    t_attack = events[t_attack_key]
    points = lib.prom_series_to_points(
        lib.query_prometheus_range(query_fn(job), t0, t1), t_attack
    )
    return points


def network_chaos_charts():
    res_events = load_events(RESULTS, "network-chaos")
    nai_events = load_events(RESULTS_NAIVE, "network-chaos")
    window = (0, res_events["t_attack_end"] - res_events["t_attack_start"])

    res_lat = prom_series_since_attack(RESULTS, "network-chaos", "api-gateway", lib.mean_latency_query)
    nai_lat = prom_series_since_attack(RESULTS_NAIVE, "network-chaos", "api-gateway-naive", lib.mean_latency_query)
    plot_overlay(
        res_lat, nai_lat,
        "NetworkChaos - Latencia media: resiliente (fundo) vs. naive (destaque)",
        "segundos", OUT_DIR / "network-chaos-latency-overlay.png", window,
    )

    res_err = prom_series_since_attack(RESULTS, "network-chaos", "api-gateway", lib.error_rate_query)
    nai_err = prom_series_since_attack(RESULTS_NAIVE, "network-chaos", "api-gateway-naive", lib.error_rate_query)
    plot_overlay(
        res_err, nai_err,
        "NetworkChaos - Taxa de erro (4xx/5xx): resiliente (fundo) vs. naive (destaque)",
        "fracao de requisicoes", OUT_DIR / "network-chaos-error-overlay.png", window,
    )


def pod_chaos_charts():
    res_events = load_events(RESULTS, "pod-chaos")
    window = (0, 1)

    res_err = prom_series_since_attack(RESULTS, "pod-chaos", "api-gateway", lib.error_rate_query, "t_attack")
    nai_err = prom_series_since_attack(RESULTS_NAIVE, "pod-chaos", "api-gateway-naive", lib.error_rate_query, "t_attack")
    plot_overlay(
        res_err, nai_err,
        "PodChaos - Taxa de erro (4xx/5xx): resiliente (fundo) vs. naive (destaque)",
        "fracao de requisicoes", OUT_DIR / "pod-chaos-error-overlay.png", window,
    )


def stress_chaos_charts():
    res_events = load_events(RESULTS, "stress-chaos")
    window = (0, res_events["t_attack_end"] - res_events["t_attack_start"])

    def replicas_series(results_dir, exp_key, field):
        events = load_events(results_dir, exp_key)
        t_attack = events["t_attack_start"]
        series = []
        with open(results_dir / exp_key / "timeline.jsonl") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get(field) is not None:
                    series.append((rec["t"] - t_attack, rec[field]))
        return series

    res_replicas = replicas_series(RESULTS, "stress-chaos", "current_replicas")
    nai_replicas = replicas_series(RESULTS_NAIVE, "stress-chaos", "pod_count")
    plot_overlay_step(
        res_replicas, nai_replicas,
        "StressChaos - Replicas: resiliente/HPA (fundo) vs. naive sem HPA (destaque)",
        "replicas", OUT_DIR / "stress-chaos-replicas-overlay.png", window,
        label_bg="Resiliente - HPA (ao fundo)", label_fg="Naive - fixo, sem HPA (em destaque)",
    )

    res_lat = prom_series_since_attack(RESULTS, "stress-chaos", "api-gateway", lib.mean_latency_query)
    nai_lat = prom_series_since_attack(RESULTS_NAIVE, "stress-chaos", "api-gateway-naive", lib.mean_latency_query)
    plot_overlay(
        res_lat, nai_lat,
        "StressChaos - Latencia media: resiliente (fundo) vs. naive (destaque)",
        "segundos", OUT_DIR / "stress-chaos-latency-overlay.png", window,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prom_pf = lib.start_port_forward(
        "svc/monitoring-kube-prometheus-prometheus", 9090, 9090, namespace="monitoring"
    )
    try:
        network_chaos_charts()
        pod_chaos_charts()
        stress_chaos_charts()
        print(f"Wrote overlay charts to {OUT_DIR}")
    finally:
        prom_pf.terminate()


if __name__ == "__main__":
    main()
