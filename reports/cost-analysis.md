# Cost analysis — native thinking/effort integration

**Projection only · no paid calls.** 32 attempts across 16 tasks; same deepseek/deepseek-v4-pro-0813 / baidu/fp8 / High effort in both systems.

| Token scenario per attempt | Current observed rate | Conservative rate | Conservative, 32 attempts | Fits $1 at conservative rate? |
| --- | ---: | ---: | ---: | --- |
| Light: 100,000 input + 10,000 output | $0.026 | $0.172 | $5.49 | Yes |
| Moderate: 300,000 input + 40,000 output | $0.085 | $0.554 | $17.74 | Yes |
| Heavy: 600,000 input + 80,000 output | $0.170 | $1.109 | $35.48 | No — can stop at cap |

Output includes thinking + visible tokens, billed once. Input includes replayed histories and all child agents; caching is not assumed.

Current endpoint snapshot: $0.201960/M input, $0.605880/M output; advertised discount 84.7%. Reserve at $1.32/M input and $3.96/M output so the plan does not depend on a promotion.

**Ceilings:** $8 development/probes + $32 measurement + $5 recovery + $5 fees/buffer = **$50**. No new ablations, repeats, context truncation or lower-quality model/provider were added.

High is the model’s advertised default, is supported natively, and leaves the existing 32,768 combined-output cap unchanged. Max remains a supported control, not another paid arm. Actual adequacy must be established by the four development attempts after owner approval; if these cannot finish under their caps, measurement stays blocked.

**Change from the old plan:** reservation prices are 4.4× the expired $0.30/$0.90 rates. Effort is now explicitly set in the native controllers and verified before canonical provider normalization. No estimate assumes a fixed percentage reduction from High.

## Limits

- Assumed aggregate token volumes across the whole attempt, including all Engine children and repeated prompt/history input.
- All input priced as uncached. Cache discounts may help but are not assumed.
- Effort is a setting, not a guaranteed number of reasoning tokens. No measured High-vs-Max cost or accuracy claim.
- Retained reasoning history can increase repeated input cost; native compaction behavior is unchanged.
- An over-cap scenario may fail or be refused. Uncapped scenario totals are not a promise of successful completion.
- Endpoint promotions, latency and availability can change; admission ceilings remain fixed.
- Fees are reserved separately. No credits purchased or paid calls made.

Sources: https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-pro-0813/endpoints, https://openrouter.ai/docs/guides/best-practices/reasoning-tokens, https://api-docs.deepseek.com/guides/thinking_mode/
