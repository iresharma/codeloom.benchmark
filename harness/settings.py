"""Pinned study settings. Classic is the published light/medium/heavy page."""

SUITES = {
    "classic": "v1",
    "realistic": "v2",
    "v1": "v1",
    "v2": "v2",
}

MODEL = "deepseek/deepseek-v4-pro-0813"
PROVIDER = "baidu/fp8"
REASONING_EFFORT = "high"
MAX_TOKENS = 32768
PLATFORM = "linux/amd64"
INPUT_PER_MILLION = 1.32
OUTPUT_PER_MILLION = 3.96
REQUEST_TIMEOUT_S = 2400
CALL_LIMIT_MULTIPLIER = 3
DIFFICULTY_LIMITS = {"light": 1.0, "medium": 2.0, "heavy": 4.0}
SECONDARY_MODELS = {
    "anthropic/claude-haiku-4.5": {
        "provider": "anthropic",
        "input_per_million": 1.00,
        "output_per_million": 5.00,
        "reasoning_effort": "high",
        "thinking_enabled": True,
        "max_tokens": MAX_TOKENS,
    }
}

RUNTIME_TAG = "codeloom-harness-runtime:dev"
SOCAT_IMAGE = (
    "alpine/socat:1.8.0.3@"
    "sha256:beb4a68d9e4fe6b0f21ea774a0fde6c31f580dde6368939ed70100c5385b015e"
)

LIMITATIONS = {
    "v1": [
        "Author-designed corpus; no independent review.",
        "One run per task; no reliability, interaction, or component-effect claims.",
        "Synthetic bugs keep upstream tests, which can reveal the regression.",
        "Test-generation features are supplied with their reference tests withheld.",
        "Host ARM64 runs Linux AMD64 through emulation.",
        "Offline report rebuilding is reproducible; model outputs, endpoint availability and prices are not.",
        "Difficulty is a pre-run scope assessment, not measured token use; category counts are unequal.",
        "Six unselected fixtures remain reserves; no scored outputs informed selection.",
    ],
    "v2": [
        "Author-designed corpus; no independent review.",
        "One run per task; no reliability, interaction, or component-effect claims.",
        "Realistic tasks add browser, terminal and research evidence on top of hidden checks.",
        "Live research results can change between runs.",
        "Host ARM64 runs Linux AMD64 through emulation.",
        "Offline report rebuilding is reproducible; model outputs, endpoint availability and prices are not.",
    ],
}


def suite_id(name: str) -> str:
    if name not in SUITES:
        raise ValueError(f"Unknown suite {name!r}; use classic or realistic")
    return SUITES[name]


def schema_version(suite: str) -> int:
    return 2 if suite == "v2" else 1


def cap_for(difficulty: str) -> float:
    return DIFFICULTY_LIMITS[difficulty]
