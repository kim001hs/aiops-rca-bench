"""Format recorded model usage for benchmark Markdown reports."""


def usage_lines(usage, cost_usd):
    usage = usage or {}
    parts = []
    for label, key in (("입력", "prompt_tokens"), ("출력", "completion_tokens"),
                       ("전체", "total_tokens"), ("캐시 입력", "cached_tokens")):
        value = usage.get(key)
        if isinstance(value, int):
            parts.append(f"{label} {value:,}")
    tokens = " / ".join(parts) if parts else "기록 없음"
    cost = f"약 ${cost_usd:.6f} (USD)" if isinstance(cost_usd, (int, float)) else "기록 없음"
    return f"- **사용 토큰**: {tokens}\n- **비용**: {cost}"
