# APOS Project

## Purpose

This repository contains APOS, an AI Project Operating System prototype.

## Current Scope

APOS 0.1 focuses on one reliable task loop:

- accept a human-written TaskSpec
- constrain writable files
- request a patch from a Local Coder command
- apply only authorized changes
- run verification commands
- retry on failure
- roll back failed attempt patches before retrying
- commit successful task results
- write inspectable run logs under `.apos/runs/`
- list and inspect run logs with `apos runs list` and `apos runs show`
- generate compact quality reports with `apos report`

## Rules

- Keep the Cloud Controller out of 0.1.
- Prefer patch-based coder output over direct file mutation.
- Treat test/build execution as the success signal.
- Keep project memory as current state, not an append-only reasoning log.
