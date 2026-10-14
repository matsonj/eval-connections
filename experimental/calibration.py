"""Calibration of relatedness_probe.py outputs: when a model says P(yes) = 0.7, is it right 70% of the time?

Reads one or more probe JSON files (no API calls). True groups are positives; near_miss and mixed sets are
negatives; trap sets are left out because they are genuinely related. For each model it prints a reliability
table, expected calibration error (ECE), Brier score and AUROC, and writes one SVG reliability diagram with
every model on it.

The probe's negatives outnumber positives 3 to 1, so the base rate is 25%. A model can be well calibrated
for this mix and still miscalibrated for another; --balance reweights negatives to a 50/50 base rate.

    uv run python experimental/calibration.py experimental/runs/*_probe_*.json
    uv run python experimental/calibration.py experimental/runs/*_probe_*.json --bins 5 --balance
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUTPUT_DIR, auroc  # noqa: E402

COLORS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#db2777"]


def load(path: Path) -> tuple[str, list[tuple[float, int]]]:
    data = json.loads(path.read_text())
    label = f"{data['model'].split('/')[-1]} ({data.get('context', 'none')})"
    points = []
    for it in data["items"]:
        if it["kind"] == "trap":
            continue
        ps = [p for p in it["p_yes"] if p is not None]
        if ps:
            points.append((statistics.mean(ps), 1 if it["kind"] == "true" else 0))
    return label, points


def analyse(points: list[tuple[float, int]], bins: int, balance: bool) -> dict:
    n_pos = sum(y for _, y in points)
    n_neg = len(points) - n_pos
    w_neg = (n_pos / n_neg) if balance and n_neg else 1.0
    weight = [1.0 if y else w_neg for _, y in points]
    total_w = sum(weight)
    rows = []
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, (p, _) in enumerate(points) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        w = sum(weight[i] for i in idx)
        if not idx:
            rows.append({"lo": lo, "hi": hi, "n": 0, "mean_p": None, "observed": None})
            continue
        mean_p = sum(points[i][0] * weight[i] for i in idx) / w
        observed = sum(points[i][1] * weight[i] for i in idx) / w
        ece += (w / total_w) * abs(mean_p - observed)
        rows.append({"lo": lo, "hi": hi, "n": len(idx), "mean_p": mean_p, "observed": observed})
    brier = sum(weight[i] * (p - y) ** 2 for i, (p, y) in enumerate(points)) / total_w
    return {"rows": rows, "ece": ece, "brier": brier, "n_pos": n_pos, "n_neg": n_neg,
            "auroc": auroc([p for p, y in points if y], [p for p, y in points if not y]),
            "mean_p_pos": statistics.mean(p for p, y in points if y) if n_pos else None,
            "mean_p_neg": statistics.mean(p for p, y in points if not y) if n_neg else None}


def svg(curves: list[tuple[str, dict]], path: Path) -> None:
    size, pad = 420, 50
    plot = size - 2 * pad

    def xy(p, o):
        return pad + p * plot, size - pad - o * plot

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size + 220} {size}" font-family="sans-serif" '
             f'font-size="12">', f'<rect width="{size + 220}" height="{size}" fill="white"/>']
    for t in range(0, 11, 2):
        x, y = xy(t / 10, t / 10)
        parts.append(f'<line x1="{x}" y1="{size - pad}" x2="{x}" y2="{pad}" stroke="#eee"/>')
        parts.append(f'<line x1="{pad}" y1="{y}" x2="{size - pad}" y2="{y}" stroke="#eee"/>')
        parts.append(f'<text x="{x}" y="{size - pad + 16}" text-anchor="middle">{t / 10:.1f}</text>')
        parts.append(f'<text x="{pad - 8}" y="{y + 4}" text-anchor="end">{t / 10:.1f}</text>')
    x0, y0 = xy(0, 0)
    x1, y1 = xy(1, 1)
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#999" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{size / 2}" y="{size - 10}" text-anchor="middle">model P(yes)</text>')
    parts.append(f'<text x="14" y="{size / 2}" text-anchor="middle" transform="rotate(-90 14 {size / 2})">'
                 'share that are real groups</text>')
    for k, (label, res) in enumerate(curves):
        color = COLORS[k % len(COLORS)]
        pts = [xy(r["mean_p"], r["observed"]) for r in res["rows"] if r["n"]]
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="'
                     + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + '"/>')
        for (x, y), r in zip(pts, [r for r in res["rows"] if r["n"]]):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{2 + min(r["n"], 100) ** 0.5 / 2:.1f}" '
                         f'fill="{color}"/>')
        parts.append(f'<rect x="{size + 10}" y="{pad + k * 20 - 9}" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{size + 26}" y="{pad + k * 20}">{label}  ECE {res["ece"]:.2f}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--bins", type=int, default=10)
    p.add_argument("--balance", action="store_true", help="reweight negatives to a 50/50 base rate")
    p.add_argument("--out", type=Path, default=OUTPUT_DIR / "calibration.svg")
    args = p.parse_args()

    curves = []
    for f in args.files:
        label, points = load(f)
        res = analyse(points, args.bins, args.balance)
        curves.append((label, res))
        print(f"\n{label}   {res['n_pos']} real groups, {res['n_neg']} fakes"
              f"{'   (negatives reweighted to 50/50)' if args.balance else ''}")
        print(f"{'bin':<11} {'n':>4} {'mean P':>7} {'observed':>9}")
        for r in res["rows"]:
            if r["n"]:
                print(f"{r['lo']:.1f}-{r['hi']:.1f}    {r['n']:>4} {r['mean_p']:>7.2f} {r['observed']:>9.2f}")
        print(f"ECE {res['ece']:.3f}   Brier {res['brier']:.3f}   AUROC {res['auroc']:.3f}   "
              f"mean P real {res['mean_p_pos']:.2f} / fake {res['mean_p_neg']:.2f}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    svg(curves, args.out)
    print(f"\nsaved {args.out}")


if __name__ == "__main__":
    main()
