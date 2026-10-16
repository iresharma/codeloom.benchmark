#!/usr/bin/env python3
"""Equivalent research transport for shell-native agents; credentials are trial scoped."""

import argparse
import json
import os
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("search", "fetch", "github"))
    parser.add_argument(
        "arguments", help='JSON object; e.g. {"query":"HTTP redirect authorization"}'
    )
    args = parser.parse_args()
    req = urllib.request.Request(
        os.environ["ENGINE_RESEARCH_GATEWAY"],
        data=json.dumps(
            {"operation": args.operation, "arguments": json.loads(args.arguments)}
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["ENGINE_RESEARCH_TOKEN"],
        },
    )
    with urllib.request.urlopen(req, timeout=65) as response:
        result = json.load(response)
    print(result["text"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
