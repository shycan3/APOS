from __future__ import annotations

import argparse
import json
import subprocess
import sys
from urllib import error, request


SYSTEM_PROMPT = """You are APOS Local Coder.

You receive one JSON APOS task prompt. Implement the requested change by returning
exactly one APOS protocol response.

Allowed responses:
1. A unified diff patch.
2. A JSON permission request:
   {"type":"request_permission","path":"...","permission":"read","reason":"..."}

Rules:
- Return only the protocol response.
- Do not use markdown fences.
- Do not explain the change.
- Modify only files listed in task.allowed_files.
- If required information or write access is missing, return a permission request.
- Preserve existing public APIs unless the TaskSpec explicitly permits a change.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apos.ollama", description="APOS Ollama Local Coder adapter")
    parser.add_argument("--model", required=True, help="Ollama model name, for example qwen2.5-coder:7b")
    parser.add_argument("--ollama-binary", default="ollama", help="Ollama executable path")
    parser.add_argument("--timeout", type=int, default=300, help="Ollama command timeout in seconds")
    args = parser.parse_args(argv)

    apos_prompt = sys.stdin.read()
    if not apos_prompt.strip():
        print("APOS Ollama adapter error: empty stdin", file=sys.stderr)
        return 2

    try:
        output = run_ollama(
            model=args.model,
            apos_prompt=apos_prompt,
            ollama_binary=args.ollama_binary,
            timeout_seconds=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"APOS Ollama adapter error: ollama timed out after {args.timeout}s", file=sys.stderr)
        return 124
    except OSError as exc:
        print(f"APOS Ollama adapter error: could not start ollama: {exc}", file=sys.stderr)
        return 127
    except RuntimeError as exc:
        print(f"APOS Ollama adapter error: {exc}", file=sys.stderr)
        return 1

    protocol_output = extract_protocol_output(output)
    if not protocol_output:
        print("APOS Ollama adapter error: model did not return a patch or permission request", file=sys.stderr)
        print(output, file=sys.stderr)
        return 3

    print(protocol_output, end="" if protocol_output.endswith("\n") else "\n")
    return 0


def run_ollama(model: str, apos_prompt: str, ollama_binary: str, timeout_seconds: int) -> str:
    prompt = build_model_prompt(apos_prompt)
    return run_ollama_prompt(model=model, prompt=prompt, ollama_binary=ollama_binary, timeout_seconds=timeout_seconds)


def run_ollama_prompt(model: str, prompt: str, ollama_binary: str, timeout_seconds: int) -> str:
    completed = subprocess.run(
        [ollama_binary, "run", model],
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"ollama run failed: {detail}")
    return completed.stdout


def run_ollama_generate(
    model: str,
    prompt: str,
    ollama_host: str,
    timeout_seconds: int,
    json_format: bool = False,
) -> str:
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if json_format:
        payload["format"] = "json"
    data = json.dumps(payload).encode("utf-8")
    url = ollama_host.rstrip("/") + "/api/generate"
    http_request = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise RuntimeError(f"ollama HTTP generate failed: {exc}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ollama HTTP generate returned invalid JSON: {exc}") from exc
    generated = result.get("response")
    if not isinstance(generated, str):
        raise RuntimeError("ollama HTTP generate response did not include text")
    return generated


def build_model_prompt(apos_prompt: str) -> str:
    return f"{SYSTEM_PROMPT}\n\nAPOS TASK PROMPT JSON:\n{apos_prompt}\n"


def extract_protocol_output(output: str) -> str:
    stripped = output.strip()
    if not stripped:
        return ""

    fenced = _extract_fenced_block(stripped)
    if fenced:
        json_output = _extract_json(fenced)
        if json_output:
            return json_output
        diff_output = _extract_diff(fenced)
        if diff_output:
            return diff_output

    json_output = _extract_json(stripped)
    if json_output:
        return json_output

    diff_output = _extract_diff(stripped)
    if diff_output:
        return diff_output
    return ""


def _extract_json(value: str) -> str:
    if not value.lstrip().startswith("{"):
        return ""
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return ""
    if payload.get("type") not in {"request_permission", "patch"}:
        return ""
    return json.dumps(payload, ensure_ascii=False)


def _extract_diff(value: str) -> str:
    lines = value.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- "):
            start = index
            break
    if start is None:
        return ""
    return "\n".join(lines[start:]).rstrip() + "\n"


def _extract_fenced_block(value: str) -> str:
    lines = value.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            start = index + 1
            break
    if start is None:
        return ""
    for index in range(start, len(lines)):
        if lines[index].strip().startswith("```"):
            return "\n".join(lines[start:index]).strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
