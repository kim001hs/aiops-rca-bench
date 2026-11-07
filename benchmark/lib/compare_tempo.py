"""Summarize every valid alternating order-latency pair, not only latest runs."""
import json
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1] / "results"
CASE = "service_05_order_latency"


def runs(arm):
    base = ROOT / CASE / arm
    rows = []
    for directory in sorted((p for p in base.iterdir() if p.is_dir() and p.name.isdigit()),
                            key=lambda p: int(p.name)):
        info = json.loads((directory / "run_info.json").read_text(encoding="utf-8"))
        if info["status"] != "collected":
            continue
        if not info.get("cleanup_success") or not info.get("alert_resolved"):
            raise ValueError(f"unrestored run: {directory}")
        evaluation_file = directory / "evaluation.json"
        if not evaluation_file.exists():
            raise ValueError(f"evaluate run first: {directory}")
        evaluation = json.loads(evaluation_file.read_text(encoding="utf-8"))
        rows.append({
            "arm": arm, "run": int(directory.name), "started_at": info["startedAt"],
            "alert_fingerprint": info["alert_fingerprint"], "alert_starts_at": info["alert_starts_at"],
            "s3_uri": info["s3_uri"], "holmes_tempo_used": info.get("holmes_tempo_used", False),
            "linked_trace_count": len(info.get("linked_order_trace_ids", [])),
            "order_success_rate": mean(s["outcome"] == "success" for s in info["injected_orders"]),
            "baseline_latency_seconds": mean(s["duration_seconds"] for s in info["baseline_orders"]),
            "injected_latency_seconds": mean(s["duration_seconds"] for s in info["injected_orders"]),
            "scores": evaluation["scores"], "verdict": evaluation["verdict"],
        })
    return rows


def main():
    baseline, tempo = runs("baseline"), runs("tempo")
    if not baseline or len(baseline) != len(tempo):
        raise ValueError("equal nonzero valid run counts are required")
    chronological = sorted(baseline + tempo, key=lambda row: datetime.fromisoformat(row["started_at"]))
    if [row["arm"] for row in chronological] != ["baseline", "tempo"] * len(baseline):
        raise ValueError("runs did not alternate baseline then tempo")
    pairs = [{"pair": i + 1, "baseline": baseline[i], "tempo": tempo[i]}
             for i in range(len(baseline))]
    dimensions = ("root_cause_score", "evidence_score", "factual_accuracy_score", "total_score")
    summary = {arm: {
        **{key: mean(row["scores"][key] for row in rows) for key in dimensions},
        "holmes_tempo_used": sum(row["holmes_tempo_used"] for row in rows),
        "linked_trace_runs": sum(row["linked_trace_count"] > 0 for row in rows),
        "order_success_rate": mean(row["order_success_rate"] for row in rows),
        "baseline_latency_seconds": mean(row["baseline_latency_seconds"] for row in rows),
        "injected_latency_seconds": mean(row["injected_latency_seconds"] for row in rows),
    } for arm, rows in (("baseline", baseline), ("tempo", tempo))}
    output = {"case_id": CASE, "valid_pairs": len(pairs), "pairs": pairs, "summary": summary}
    (ROOT / "tempo_comparison.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Tempo 전후 주문 지연 반복 비교", "", f"유효한 교대 비교쌍: {len(pairs)}개. 유효하지 않은 회차는 제외.", "",
             "| 회차 | 기준군 원인/30 | Tempo군 원인/30 | 기준군 증거/30 | Tempo군 증거/30 | 기준군 사실/20 | Tempo군 사실/20 | Tempo 조회 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for pair in pairs:
        left, right = pair["baseline"], pair["tempo"]
        lines.append(f"| {pair['pair']} | {left['scores']['root_cause_score']:.1f} | {right['scores']['root_cause_score']:.1f} | "
                     f"{left['scores']['evidence_score']:.1f} | {right['scores']['evidence_score']:.1f} | "
                     f"{left['scores']['factual_accuracy_score']:.1f} | {right['scores']['factual_accuracy_score']:.1f} | "
                     f"{right['holmes_tempo_used']} |")
    lines += ["", "| 지표 | 기준군 평균 | Tempo군 평균 |", "| --- | ---: | ---: |"]
    for key in dimensions:
        lines.append(f"| {key} | {summary['baseline'][key]:.1f} | {summary['tempo'][key]:.1f} |")
    lines.append(f"| Holmes Tempo 조회 회차 | {summary['baseline']['holmes_tempo_used']} | {summary['tempo']['holmes_tempo_used']} |")
    lines.append(f"| 연결 trace 확인 회차 | {summary['baseline']['linked_trace_runs']} | {summary['tempo']['linked_trace_runs']} |")
    lines.append(f"| 주문 성공률 | {summary['baseline']['order_success_rate']:.0%} | {summary['tempo']['order_success_rate']:.0%} |")
    lines.append(f"| 주입 후 응답 초 | {summary['baseline']['injected_latency_seconds']:.3f} | {summary['tempo']['injected_latency_seconds']:.3f} |")
    lines += ["", f"trace 사용과 점수는 별개로 기록합니다. LLM 채점과 {len(pairs)}쌍의 표본은 확정적인 성능 개선 보증이 아닙니다."]
    (ROOT / "tempo_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
