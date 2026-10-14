# CodeLoom benchmark — v1-not-run

Status: not_run. 16 tasks; no repeated trials or ablations.

Generator: deepseek/deepseek-v4-pro-0813 / baidu/fp8; classifier off.

| System | Pass / evaluable | Delivered / scheduled | Cost | Cost / success |
| --- | --- | --- | --- | --- |
| mini-swe-agent | 0 / 0 | 0 / 16 | Unavailable | Unavailable |
| engine-core | 0 / 0 | 0 / 16 | Unavailable | Unavailable |

Paired outcomes: engine_only: 0; mini_only: 0; both_pass: 0; both_fail: 0; unresolved: 0; unrun: 16

Run gate: Engine streaming/history drops provider-required reasoning state; measured calls are disabled.

## Observations from traces

No measured traces exist yet. The separately labeled synthetic adapter traces check tool continuation and sealing; they provide no evidence of coding performance.

## Limitations

- Author-designed corpus; no independent review.
- One run per task; no reliability, interaction, or component-effect claims.
- Synthetic bugs keep upstream tests, which can reveal the regression.
- Test-generation features are supplied with their reference tests withheld.
- Host ARM64 runs Linux AMD64 through emulation.
- Offline report rebuilding is reproducible; model outputs, endpoint availability and prices are not.
- Ky candidates were excluded before freeze after reference browser/timing certification failed; four reserve tasks maintain the category balance.
