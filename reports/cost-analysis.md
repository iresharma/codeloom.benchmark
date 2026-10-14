# Cost analysis — 10-task paired study

**Assumptions, not measured forecasts.** 10 tasks / 20 attempts: 3 light, 3 medium, 4 heavy. Same model, provider and High effort in both systems; zero ablations/repeats. No paid calls.

| Scope | Tasks / attempts | Calls × average input | Total output per attempt | Conservative cost / attempt | Cap / attempt | Conservative cohort cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Light | 3 / 6 | 8 × 20,000 | 12,000 | $0.259 | $1.00 | $1.55 |
| Medium | 3 / 6 | 16 × 40,000 | 40,000 | $1.003 | $2.00 | $6.02 |
| Heavy | 4 / 8 | 24 × 65,000 | 72,000 | $2.344 | $4.00 | $18.75 |

**Weighted illustrative total: $26.33** at conservative rates, or $3.61 at the captured promotional rates. These are not promised spending or success rates.

Calls include the root and every child. Average input is the entire billed request, including instructions, code, tool results and replayed histories. Output includes thinking and visible output once. A single large code read is not the whole-attempt input budget.

**Stress case:** doubling heavy calls to 48 gives $4.69 per heavy attempt at conservative rates, above the $4 cap. Such an attempt may stop and must remain a failure in the report. Four development attempts cannot establish a reliable tail estimate for heavy tasks.

**Attempt caps:** 6 light attempts × $1 + 6 medium × $2 + 8 heavy × $4 = $50 in individual maxima. Both systems receive the same cap on each task. These maxima are not guaranteed allocations: the shared measurement envelope is still $32, and pair admission can stop before all 20 attempts run.

**Overall ceiling:** $8 development/probes + $32 measurement + $5 recovery + $5 fees/buffer = **$50**. Exhausting every primary cap plus those reserves would require $68; that spending is not authorized. All budget stops and unrun tasks remain in the report.

Captured endpoint snapshot (2026-10-14T13:37:22.608606+00:00): $0.180840/M input and $0.542520/M output. Reserve at $1.32/$3.96 per million; promotions are not required. Implied unpromoted rates come from the advertised discount, not an independent quote.

Model/provider/High effort and the 32,768 combined-output ceiling remain unchanged. Six unselected fixtures stay available as reserves; their previous authoring/certification evidence is retained. Task selection and scope rationale are pinned in `suites/v1/selection.yaml` before any scored outputs.

## Limits

- Difficulty is a pre-run scope assessment relative to this small corpus, not a measured complexity or token ranking.
- Request counts and average context/output sizes are illustrative assumptions, not calibrated forecasts or guaranteed completion budgets.
- A 10,000-token code read carried into six subsequent requests alone contributes 60,000 input tokens; all parent/child histories and other inputs also count.
- All input priced as uncached; replayed reasoning counts as future input. Reasoning plus visible output is billed once as output.
- High effort has no guaranteed reasoning-token volume or measured savings versus Max.
- Conservative byte-based request reservations can refuse calls before actual spend reaches the attempt cap; aggregate token math is not an admission guarantee.
- Individual attempt maxima sum to $50 but share a $32 measurement envelope; completing all 20 attempts is not guaranteed.
- Heavy stress case exceeds its cap. Budget stops are published failures, not silently retried or removed.
- Paid development calibration, fixture recertification and preregistration remain pending; no model scoring or credits purchased.
- Provider promotions and availability can change; the plan reserves conservative rates and a separate fee buffer.

Sources: https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-pro-0813/endpoints, https://openrouter.ai/docs/guides/best-practices/reasoning-tokens, https://api-docs.deepseek.com/guides/thinking_mode/
