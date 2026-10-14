"""Read-only cost sensitivity analysis. Token scenarios are assumptions, not runs."""

from __future__ import annotations

from decimal import Decimal
from .artifacts import write_json
from .manifests import ROOT, config, tasks, model_metadata
from .pricing import check

SCENARIOS = [
    ("Light", 100_000, 8_000, 2_000),
    ("Moderate", 300_000, 30_000, 10_000),
    ("Heavy", 600_000, 60_000, 20_000),
]


def estimate(input_tokens, reasoning_tokens, visible_tokens, input_rate, output_rate):
    # Reasoning is already part of billed output; never add it to completion twice.
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
        check()
        if refresh
        else __import__("json").loads((ROOT / "reports/pricing.json").read_text())
    )
    if "pricing" not in observation:
        raise ValueError("Pinned provider unavailable; no usable pricing snapshot")
    price = observation["pricing"]
    count = len(tasks()) * len(settings["systems"])
    variants = [price, *price.get("overrides", [])]
    current_input = max(Decimal(p["prompt"]) for p in variants) * 1_000_000
    current_output = max(Decimal(p["completion"]) for p in variants) * 1_000_000
    discount = Decimal(str(price.get("discount", 0)))
    # This is an inference from the advertised discount, not an independently quoted list price.
    implied_base = (
        {
            "input_per_million": float(current_input / (1 - discount)),
            "output_per_million": float(current_output / (1 - discount)),
        }
        if 0 <= discount < 1
        else None
    )
    rows = []
    for label, inputs, reasoning, visible in SCENARIOS:
        observed = estimate(inputs, reasoning, visible, current_input, current_output)
        conservative = estimate(
            inputs,
            reasoning,
            visible,
            settings["input_per_million"],
            settings["output_per_million"],
        )
        rows.append(
            {
                "scenario": label,
                "input_tokens_per_attempt": inputs,
                "reasoning_tokens_per_attempt": reasoning,
                "visible_tokens_per_attempt": visible,
                "billed_output_tokens_per_attempt": reasoning + visible,
                "observed_price_cost_per_attempt": observed,
                "reservation_price_cost_per_attempt": conservative,
                "observed_price_total_for_32": observed * count,
                "reservation_price_uncapped_total": conservative * count,
                "fits_trial_cap_at_observed_price": observed
                <= settings["trial_limit_usd"],
                "fits_trial_cap_at_reservation_price": conservative
                <= settings["trial_limit_usd"],
            }
        )
    report = {
        "status": "projection_only",
        "paid_calls": 0,
        "engine_commit": __import__("subprocess")
        .check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent / "engine", text=True
        )
        .strip(),
        "model": settings["model"],
        "provider": settings["provider"],
        "quantization": observation.get("quantization"),
        "reasoning_effort": settings["reasoning_effort"],
        "supported_efforts": model["reasoning"]["supported_efforts"],
        "thinking_enabled": settings["thinking_enabled"],
        "max_tokens_per_request": settings["max_tokens"],
        "planned_attempts": count,
        "task_count": len(tasks()),
        "ablations": 0,
        "observed_at": observation.get("created_at"),
        "observed_price": price,
        "implied_unpromoted_rates": implied_base,
        "reservation_rates": {
            "input_per_million": settings["input_per_million"],
            "output_per_million": settings["output_per_million"],
        },
        "budget": {
            "development_and_probes": settings["stage_limits"]["development"],
            "measurement": settings["stage_limits"]["measured"],
            "recovery": settings["stage_limits"]["recovery"],
            "inference_ceiling": settings["campaign_limit_usd"],
            "fees_buffer": 5,
            "total_ceiling": 50,
            "per_primary_attempt": settings["trial_limit_usd"],
        },
        "max_output_reservation_per_request": settings["max_tokens"]
        * settings["output_per_million"]
        / 1_000_000,
        "scenarios": rows,
        "limitations": [
            "Assumed aggregate token volumes across the whole attempt, including all Engine children and repeated prompt/history input.",
            "All input priced as uncached. Cache discounts may help but are not assumed.",
            "Effort is a setting, not a guaranteed number of reasoning tokens. No measured High-vs-Max cost or accuracy claim.",
            "Retained reasoning history can increase repeated input cost; native compaction behavior is unchanged.",
            "An over-cap scenario may fail or be refused. Uncapped scenario totals are not a promise of successful completion.",
            "Endpoint promotions, latency and availability can change; admission ceilings remain fixed.",
            "Fees are reserved separately. No credits purchased or paid calls made.",
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
        "# Cost analysis — native thinking/effort integration",
        "",
        f"**Projection only · no paid calls.** {count} attempts across {len(tasks())} tasks; same {settings['model']} / {settings['provider']} / High effort in both systems.",
        "",
        "| Token scenario per attempt | Current observed rate | Conservative rate | Conservative, 32 attempts | Fits $1 at conservative rate? |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']}: {row['input_tokens_per_attempt']:,} input + {row['billed_output_tokens_per_attempt']:,} output | ${row['observed_price_cost_per_attempt']:.3f} | ${row['reservation_price_cost_per_attempt']:.3f} | ${row['reservation_price_uncapped_total']:.2f} | {'Yes' if row['fits_trial_cap_at_reservation_price'] else 'No — can stop at cap'} |"
        )
    lines += [
        "",
        "Output includes thinking + visible tokens, billed once. Input includes replayed histories and all child agents; caching is not assumed.",
        "",
        f"Current endpoint snapshot: ${float(current_input):.6f}/M input, ${float(current_output):.6f}/M output; advertised discount {float(discount) * 100:.1f}%. Reserve at $1.32/M input and $3.96/M output so the plan does not depend on a promotion.",
        "",
        "**Ceilings:** $8 development/probes + $32 measurement + $5 recovery + $5 fees/buffer = **$50**. No new ablations, repeats, context truncation or lower-quality model/provider were added.",
        "",
        "High is the model’s advertised default, is supported natively, and leaves the existing 32,768 combined-output cap unchanged. Max remains a supported control, not another paid arm. Actual adequacy must be established by the four development attempts after owner approval; if these cannot finish under their caps, measurement stays blocked.",
        "",
        "**Change from the old plan:** reservation prices are 4.4× the expired $0.30/$0.90 rates. Effort is now explicitly set in the native controllers and verified before canonical provider normalization. No estimate assumes a fixed percentage reduction from High.",
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
