from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from urllib import error, request


SYSTEM_PROMPT = """You are APOS Local Coder.

You receive one JSON APOS task prompt. Implement the requested change by returning
exactly one APOS protocol response.

Allowed responses:
1. A unified diff patch.
2. A JSON file replacement when a diff is hard to express:
   {"type":"file_replacement","path":"...","content":"complete final file text"}
3. A JSON permission request:
   {"type":"request_permission","path":"...","permission":"read","reason":"..."}

Rules:
- Return only the protocol response.
- Do not use markdown fences.
- Do not explain the change.
- Modify only files listed in task.allowed_files.
- Use file_replacement if a previous diff failed to apply or the edit is easier as a complete file.
- If required information or write access is missing, return a permission request.
- Preserve existing public APIs unless the TaskSpec explicitly permits a change.
"""


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apos.ollama", description="APOS Ollama Local Coder adapter")
    parser.add_argument("--model", required=True, help="Ollama model name, for example qwen2.5-coder:7b")
    parser.add_argument("--ollama-binary", default="ollama", help="Ollama executable path")
    parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama HTTP API host")
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
            ollama_host=args.ollama_host,
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
        print("APOS Ollama adapter error: model did not return a patch, file_replacement, or permission request", file=sys.stderr)
        print(output, file=sys.stderr)
        return 3

    print(protocol_output, end="" if protocol_output.endswith("\n") else "\n")
    return 0


def run_ollama(model: str, apos_prompt: str, ollama_binary: str, ollama_host: str, timeout_seconds: int) -> str:
    prompt = build_model_prompt(apos_prompt)
    try:
        return run_ollama_generate(
            model=model,
            prompt=prompt,
            ollama_host=ollama_host,
            timeout_seconds=timeout_seconds,
        )
    except RuntimeError:
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
    stripped = _strip_terminal_control_sequences(output).strip()
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
        payload = _recover_structured_json_response(value)
        if payload is None:
            return ""
    if payload.get("type") not in {"request_permission", "patch", "file_replacement"}:
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


def _strip_terminal_control_sequences(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _recover_permission_request(value: str) -> dict[str, str] | None:
    if "request_permission" not in value:
        return None
    path = _extract_json_string_field(value, "path")
    if not path:
        return None
    return {
        "type": "request_permission",
        "path": path,
        "permission": _extract_json_string_field(value, "permission") or "read",
        "reason": _extract_json_string_field(value, "reason") or "",
    }


def _recover_structured_json_response(value: str) -> dict[str, str] | None:
    file_replacement = _recover_file_replacement(value)
    if file_replacement is not None:
        return file_replacement
    return _recover_permission_request(value)


def _recover_file_replacement(value: str) -> dict[str, str] | None:
    if "file_replacement" not in value:
        return None
    path = _extract_json_string_field(value, "path")
    if not path:
        return None
    content = _extract_json_string_field(value, "content")
    if not content:
        return None
    return {
        "type": "file_replacement",
        "path": path,
        "content": content,
    }


def _extract_json_string_field(value: str, field: str) -> str:
    if field == "content":
        match = re.search(rf'"{re.escape(field)}"\s*:\s*"(?P<value>.*)"\s*\}}', value, re.DOTALL)
    else:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*"(?P<value>.*?)"\s*(?:,|\}})', value, re.DOTALL)
    if not match:
        return ""
    raw = match.group("value")
    try:
        decoded = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        decoded = raw.replace("\r", " ").replace("\n", " ")
    return str(decoded).strip()


if __name__ == "__main__":
    raise SystemExit(main())
