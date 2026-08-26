# APOS Local Coder Examples

APOS 0.1 runs any Local Coder command that follows this contract:

```text
stdin:  APOS task prompt JSON
stdout: unified diff patch or JSON permission request
```

## Ollama

Install Ollama first, then pull a coding model such as `qwen2.5-coder:7b`.

Configure an Ollama model:

```bash
apos connect-ollama --model qwen2.5-coder:7b
```

Then run a task:

```bash
apos run examples/task-spec.sample.json
```

The configured command uses:

```bash
python -m apos.ollama --model qwen2.5-coder:7b
```

The adapter wraps the APOS prompt with stricter Local Coder instructions, calls
`ollama run`, and extracts either a unified diff or APOS permission request from
the model output.

## Generic command

Any script can be connected directly:

```bash
apos connect --coder-command "python path/to/my_coder.py"
```

That command must read stdin and print only the APOS protocol response.
