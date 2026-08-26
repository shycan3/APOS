# APOS Project

## Purpose

This repository contains APOS, an AI Project Operating System prototype.

## Current Scope

APOS 0.1 focuses on one reliable task loop:

- accept a human-written TaskSpec
- bootstrap project memory and optional Ollama configuration
- draft TaskSpec JSON from explicit CLI inputs
- refine TaskSpec JSON with the configured Ollama model
- constrain writable files
- request a patch from a Local Coder command
- apply only authorized changes
- continue through pre-approved permission requests
- run verification commands
- record preflight PASS results for tasks that already satisfy verification
- retry on failure
- roll back failed attempt patches before retrying
- commit successful task results
- write inspectable run logs under `.apos/runs/`
- list and inspect run logs with `apos runs list` and `apos runs show`
- generate compact quality reports with `apos report`
- classify failure reasons in quality reports
- validate, inspect, and run benchmark suites with `apos benchmark`
- list and inspect benchmark results with `apos benchmark results`
- record runner profile metadata in benchmark results
- compare benchmark results with `apos benchmark compare`

## Rules

- Keep the Cloud Controller out of 0.1.
- Prefer patch-based coder output over direct file mutation.
- Treat test/build execution as the success signal.
- Keep project memory as current state, not an append-only reasoning log.
