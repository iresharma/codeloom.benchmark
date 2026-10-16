"""Read-only Brave/GitHub/HTTPS research recorded on the host."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import write_json

_LOCK = threading.Lock()


def _get(url, headers=None, params=None) -> str:
    host = urlparse(url).hostname
    if urlparse(url).scheme != "https" or not host:
        raise ValueError("Only public HTTPS sources are available")
    for address in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("Private research destination denied")
    response = requests.get(
        url,
        headers=headers or {},
        params=params,
        timeout=30,
        allow_redirects=False,
        stream=True,
    )
    try:
        if not 200 <= response.status_code < 300:
            raise ValueError(f"Source returned HTTP {response.status_code}")
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > 500_000:
                raise ValueError("Research response exceeds 500 KB")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        response.close()


def _github(arguments, policy):
    args = arguments.get("args", [])
    refs = policy.get("repo_refs", {})
    headers = {"Accept": "application/vnd.github+json"}
    key = os.environ.get("BENCHMARK_GITHUB_TOKEN", "")
    if key:
        headers["Authorization"] = "Bearer " + key
    if len(args) >= 2 and args[0] == "api":
        endpoint = args[1]
        parsed = urlparse(endpoint)
        match = re.fullmatch(
            r"repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/(contents(?:/[^?#]*)?|git/trees/[^/?#]+))?",
            parsed.path,
        )
        if not match or parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
            raise ValueError("Only pinned repository metadata, contents and trees are available")
        repo = match[1]
        if repo not in refs:
            raise ValueError("Repository is outside this task's research contract")
        tail = match[2] or ""
        query = parse_qs(parsed.query)
        if set(query) - {"ref", "recursive"}:
            raise ValueError("Unsupported GitHub query")
        if query.get("ref", [refs[repo]]) != [refs[repo]]:
            raise ValueError("GitHub source must use the task's pinned commit")
        if tail.startswith("contents"):
            query["ref"] = [refs[repo]]
        elif tail.startswith("git/trees/"):
            tail = "git/trees/" + refs[repo]
        rest = args[2:]
        if rest and rest not in (
            ["-H", "Accept: application/vnd.github.raw"],
            ["-H", "Accept: application/vnd.github.raw+json"],
        ):
            raise ValueError("Only raw-content Accept headers are supported")
        if rest:
            headers["Accept"] = "application/vnd.github.raw+json"
        url = f"https://api.github.com/repos/{repo}" + ("/" + tail if tail else "")
        if query:
            url += "?" + urlencode(query, doseq=True)
        return _get(url, headers), url, refs[repo]
    raise ValueError("Unsupported read-only GitHub operation")


def handle(folder, payload, policy, counts, trial_id):
    if not isinstance(payload, dict):
        raise ValueError("Research request must be an object")
    with _LOCK:
        count = counts.get(trial_id, 0)
        if count >= 100:
            raise ValueError("Research request quota reached")
        counts[trial_id] = count + 1
    operation, arguments = payload.get("operation"), payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("Research arguments must be an object")
    url, ref, text, ok = None, None, "", False
    try:
        if operation == "search":
            key = os.environ.get("BRAVE_API_KEY", "").strip()
            if not key:
                raise ValueError("BRAVE_API_KEY unavailable on research host")
            query = str(arguments.get("query", ""))
            if not 1 <= len(query) <= 1000:
                raise ValueError("Search query must contain 1–1000 characters")
            url = "https://api.search.brave.com/res/v1/web/search"
            raw = _get(
                url,
                {"X-Subscription-Token": key, "Accept": "application/json"},
                {"q": query, "count": min(10, max(1, int(arguments.get("count", 5))))},
            )
            results = json.loads(raw).get("web", {}).get("results", [])
            text = "\n\n".join(
                f"{r.get('title', '')}\nurl: {r.get('url', '')}\n{r.get('description', '')}"
                for r in results
            )
            if not results:
                raise ValueError("Search returned no results")
        elif operation == "fetch":
            url = str(arguments.get("url", ""))
            parsed = urlparse(url)
            if parsed.hostname not in policy.get("hosts", []) or parsed.username or parsed.port not in (
                None,
                443,
            ):
                raise ValueError("Source host outside task contract")
            text = _get(url)
        elif operation == "github":
            text, url, ref = _github(arguments, policy)
        else:
            raise ValueError("Unknown research operation")
        ok = bool(text.strip())
    except (ValueError, OSError, requests.RequestException) as exc:
        text = "error: " + str(exc)
    result = {
        "ok": ok,
        "text": text,
        "operation": operation,
        "url": url,
        "commit": ref,
        "retrieved_at": time.time(),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "arguments": arguments,
    }
    write_json(folder / f"{count:04d}.json", result)
    return result
