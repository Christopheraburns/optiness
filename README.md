# Optiness: Prime Agent on Cloudera AI for Mistral Studio

Optiness ("optimal" + "harness") is the harness-and-lakehouse integration built on the
open-source Prime Agent RLM harness. A Mistral Studio agent investigates a payments decline
spike in the Meridian Financial dataset (50 million Apache Iceberg records) by calling a Prime Agent (Recursive Language Model
harness) hosted in Cloudera AI Workbench. The root agent plans in a persistent
Python REPL, runs Impala SQL, and delegates partition-scoped investigations to
Ministral children. Every finding carries the SQL and row counts that back it.

Planning documents: [PLAN.md](PLAN.md) (analysis, architecture, screenshot
checklist) and [BURNDOWN.md](BURNDOWN.md) (line items and status).

## Layout

| Path | What |
|---|---|
| image/ | Runtime container image shared by Docker Desktop and the Workbench Runtime Catalog |
| services/common/ | Env contract reader, Impala connection factory, logging |
| services/bridge/ | FastMCP server Mistral Studio calls (start/status/findings) |
| services/console/ | Streamlit investigation console over Prime Agent session logs |
| skills/ | Prime Agent executable skills: lakehouse, atlas |
| config/ | Harness templates (prompt note, subagent spec) and the Atlas stub catalog |
| scripts/ | render_config.py and smoke tests |
| data/ | Dataset schema, generator, loader |
| deploy/compose/ | Local stack: Impala quickstart, bridge, console, tunnel |
| deploy/cml/ | cmlapi deployment of the Workbench applications |

## Portability contract

Same image, same entrypoints, same environment variable names in both
targets. Only values change. See `.env.example` for every variable and
`BURNDOWN.md` for the table of what swaps per target.

## Quick start (local)

    cp .env.example .env            # fill in MISTRAL_API_KEY at minimum
    make up                         # Impala quickstart + bridge + console
    make data                       # generate and load DATA_SCALE rows
    make smoke                      # Prime Agent answers one SQL question

## Quick start (Cloudera AI Workbench)

1. Register `docker.io/christopheraburns/prime-agent-runtime:0.10.0` as a runtime.
2. Create a project from this repository with that runtime.
3. Set the `.env.example` variables as project environment variables.
4. `make deploy-cml` creates the bridge and console applications.
