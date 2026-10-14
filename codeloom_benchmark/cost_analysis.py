"""Read-only sensitivity analysis; request volumes are assumptions, not forecasts."""

from collections import Counter
from decimal import Decimal
import json
import subprocess

from .artifacts import write_json
from .manifests import ROOT, config, tasks, model_metadata
from .pricing import check

# Across root and ALL children: (requests, average input, reasoning output, visible output).
# Input includes repeated code/tool content and histories on every request.
SCENARIOS = {
    "light": (8, 20_000, 1_000, 500),
    "medium": (16, 40_000, 1_750, 750),
    "heavy": (24, 65_000, 2_000, 1_000),
}


def estimate(input_tokens, reasoning_tokens, visible_tokens, input_rate, output_rate):
    # Reasoning is already part of billed output; never count completion twice.
    return float(
        (
            Decimal(input_tokens) * Decimal(str(input_rate))
            + Decimal(reasoning_tokens + visible_tokens) * Decimal(str(output_rate))
        )
        / 1_000_000
    )


def analyze(destination=None, refresh=True):
    settings = config()
    model = model_metadata()
    observation = (
        check() if refresh else json.loads((ROOT / "reports/pricing.json").read_text())
    )
    if "pricing" not in observation:
        raise ValueError("Pinned provider unavailable; no usable pricing snapshot")
    price = observation["pricing"]
    variants = [price, *price.get("overrides", [])]
    current_input = max(Decimal(p["prompt"]) for p in variants) * 1_000_000
    current_output = max(Decimal(p["completion"]) for p in variants) * 1_000_000
    discount = Decimal(str(price.get("discount", 0)))
    selected = tasks()
    mix = Counter(t["difficulty"] for t in selected)
    arms = len(settings["systems"])
    rows = []
    for difficulty, (requests, avg_input, reasoning, visible) in SCENARIOS.items():
        inputs, reasoning, visible = (
            requests * avg_input,
            requests * reasoning,
            requests * visible,
        )
        observed = estimate(inputs, reasoning, visible, current_input, current_output)
        conservative = estimate(
            inputs,
            reasoning,
            visible,
            settings["input_per_million"],
            settings["output_per_million"],
        )
        attempts = mix[difficulty] * arms
        cap = settings["difficulty_limits_usd"][difficulty]
        rows.append(
            {
                "difficulty": difficulty,
                "tasks": mix[difficulty],
                "attempts": attempts,
                "requests_per_attempt": requests,
                "average_input_per_request": avg_input,
                "input_tokens_per_attempt": inputs,
                "reasoning_tokens_per_attempt": reasoning,
                "visible_tokens_per_attempt": visible,
                "billed_output_tokens_per_attempt": reasoning + visible,
                "observed_price_cost_per_attempt": observed,
                "reservation_price_cost_per_attempt": conservative,
                "observed_price_cohort_total": observed * attempts,
                "reservation_price_uncapped_cohort_total": conservative * attempts,
                "attempt_cap_usd": cap,
                "cohort_cap_usd": cap * attempts,
                "fits_cap_at_reservation_price": conservative <= cap,
            }
        )
    heavy = rows[-1]
    stress_cost = estimate(
        2 * heavy["input_tokens_per_attempt"],
        2 * heavy["reasoning_tokens_per_attempt"],
        2 * heavy["visible_tokens_per_attempt"],
        settings["input_per_million"],
        settings["output_per_million"],
    )
    report = {
        "status": "projection_only",
        "paid_calls": 0,
        "engine_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent / "engine", text=True
        ).strip(),
        "model": settings["model"],
        "provider": settings["provider"],
        "quantization": observation.get("quantization"),
        "reasoning_effort": settings["reasoning_effort"],
        "supported_efforts": model["reasoning"]["supported_efforts"],
        "thinking_enabled": settings["thinking_enabled"],
        "max_tokens_per_request": settings["max_tokens"],
        "planned_attempts": len(selected) * arms,
        "task_count": len(selected),
        "difficulty_mix": dict(mix),
        "ablations": 0,
        "observed_at": observation.get("created_at"),
        "observed_price": price,
        "implied_unpromoted_rates": {
            "input_per_million": float(current_input / (1 - discount)),
            "output_per_million": float(current_output / (1 - discount)),
        }
        if 0 <= discount < 1
        else None,
        "reservation_rates": {
            "input_per_million": settings["input_per_million"],
            "output_per_million": settings["output_per_million"],
        },
        "budget": {
            "development_and_probes": 8,
            "measurement": 32,
            "recovery": 5,
            "inference_ceiling": 45,
            "fees_buffer": 5,
            "total_ceiling": 50,
            "per_difficulty_attempt_caps": settings["difficulty_limits_usd"],
        },
        "primary_attempt_maxima_sum": sum(r["cohort_cap_usd"] for r in rows),
        "scenarios": rows,
        "weighted_observed_price_total": sum(
            r["observed_price_cohort_total"] for r in rows
        ),
        "weighted_reservation_price_uncapped_total": sum(
            r["reservation_price_uncapped_cohort_total"] for r in rows
        ),
        "stress": {
            "description": "Heavy attempts make twice as many calls with the same average context and output.",
            "requests_per_heavy_attempt": 48,
            "reservation_price_per_heavy_attempt": stress_cost,
            "fits_heavy_cap": stress_cost <= heavy["attempt_cap_usd"],
        },
        "limitations": [
            "Difficulty is a pre-run scope assessment relative to this small corpus, not a measured complexity or token ranking.",
            "Request counts and average context/output sizes are illustrative assumptions, not calibrated forecasts or guaranteed completion budgets.",
            "A 10,000-token code read carried into six subsequent requests alone contributes 60,000 input tokens; all parent/child histories and other inputs also count.",
            "All input priced as uncached; replayed reasoning counts as future input. Reasoning plus visible output is billed once as output.",
            "High effort has no guaranteed reasoning-token volume or measured savings versus Max.",
            "Conservative byte-based request reservations can refuse calls before actual spend reaches the attempt cap; aggregate token math is not an admission guarantee.",
            "Individual attempt maxima sum to $50 but share a $32 measurement envelope; completing all 20 attempts is not guaranteed.",
            "Heavy stress case exceeds its cap. Budget stops are published failures, not silently retried or removed.",
            "Paid development calibration, fixture recertification and preregistration remain pending; no model scoring or credits purchased.",
            "Provider promotions and availability can change; the plan reserves conservative rates and a separate fee buffer.",
        ],
        "sources": [
            observation["url"],
            "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens",
            "https://api-docs.deepseek.com/guides/thinking_mode/",
        ],
    }
    target = destination or ROOT / "reports/cost-analysis.json"
    write_json(target, report)
    lines = [
        "# Cost analysis — 10-task paired study",
        "",
        f"**Assumptions, not measured forecasts.** {len(selected)} tasks / {len(selected) * arms} attempts: 3 light, 3 medium, 4 heavy. Same model, provider and High effort in both systems; zero ablations/repeats. No paid calls.",
        "",
        "| Scope | Tasks / attempts | Calls × average input | Total output per attempt | Conservative cost / attempt | Cap / attempt | Conservative cohort cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['difficulty'].title()} | {r['tasks']} / {r['attempts']} | {r['requests_per_attempt']} × {r['average_input_per_request']:,} | {r['billed_output_tokens_per_attempt']:,} | ${r['reservation_price_cost_per_attempt']:.3f} | ${r['attempt_cap_usd']:.2f} | ${r['reservation_price_uncapped_cohort_total']:.2f} |"
        )
    lines += [
        "",
        f"**Weighted illustrative total: ${report['weighted_reservation_price_uncapped_total']:.2f}** at conservative rates, or ${report['weighted_observed_price_total']:.2f} at the captured promotional rates. These are not promised spending or success rates.",
        "",
        "Calls include the root and every child. Average input is the entire billed request, including instructions, code, tool results and replayed histories. Output includes thinking and visible output once. A single large code read is not the whole-attempt input budget.",
        "",
        f"**Stress case:** doubling heavy calls to 48 gives ${stress_cost:.2f} per heavy attempt at conservative rates, above the $4 cap. Such an attempt may stop and must remain a failure in the report. Four development attempts cannot establish a reliable tail estimate for heavy tasks.",
        "",
        "**Attempt caps:** 6 light attempts × $1 + 6 medium × $2 + 8 heavy × $4 = $50 in individual maxima. Both systems receive the same cap on each task. These maxima are not guaranteed allocations: the shared measurement envelope is still $32, and pair admission can stop before all 20 attempts run.",
        "",
        "**Overall ceiling:** $8 development/probes + $32 measurement + $5 recovery + $5 fees/buffer = **$50**. Exhausting every primary cap plus those reserves would require $68; that spending is not authorized. All budget stops and unrun tasks remain in the report.",
        "",
        f"Captured endpoint snapshot ({report['observed_at']}): ${float(current_input):.6f}/M input and ${float(current_output):.6f}/M output. Reserve at ${settings['input_per_million']:.2f}/${settings['output_per_million']:.2f} per million; promotions are not required. Implied unpromoted rates come from the advertised discount, not an independent quote.",
        "",
        "Model/provider/High effort and the 32,768 combined-output ceiling remain unchanged. Six unselected fixtures stay available as reserves; their previous authoring/certification evidence is retained. Task selection and scope rationale are pinned in `suites/v1/selection.yaml` before any scored outputs.",
        "",
        "## Limits",
        "",
        *["- " + s for s in report["limitations"]],
        "",
        "Sources: " + ", ".join(report["sources"]),
        "",
    ]
    target.with_suffix(".md").write_text("\n".join(lines))
    return report
