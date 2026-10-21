Investigation ${run_id}

Question: ${question}

Work according to the method in AGENTS.md. This run's delegation plan:

- Use ${children} subagents, one per independent slice of the data (a date range, or the values of one dimension column), chosen from what you learn in your planning step.
- Delegate only through the `partition-investigator` subagent spec. Load it before your first spawn:
  `spec = rlm.harness.get("subagent", "partition-investigator", global_=True).content`
  Build each child's prompt from the spec's "Task for the child" section and spawn exactly as the spec says, with `model="${child_model}"` and `thinking="off"`.
- Give each child an output file under `${run_dir}/` (for example `${run_dir}/slice-1.json`); use absolute paths.
- After spawning, end your turn: reply with one line, such as `Spawned ${children} children; waiting for their reports.`, and make no further tool calls. Do not poll, loop or check for files: each child sends you a message when it is done, and that message starts your next turn. Continue only when all ${children} have reported (or failed); then read their files.
- If a child fails or its file is missing, say so in your findings; do not substitute numbers of your own for its slice.

Only after every child has reported or failed, write your final findings to `${run_dir}/findings.md` and also reply with them.
