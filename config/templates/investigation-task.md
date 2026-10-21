Investigation ${run_id}

Question: ${question}

Work according to the method in AGENTS.md. This run's plan, in order:

1. Plan before delegating, with your own tool calls (no children yet):
   - List the tables (`await lakehouse.tables()`) and read each one's catalog entry. Note which tables record events, changes, configurations or incidents: candidate explanations are checked against them.
   - Measure the symptom yourself in one aggregated query over the table's whole date range (from `lakehouse.partitions`): per day, the count and the rate in question. From that series write down the start and end of any change, a baseline period outside it, and the candidate explanations to test. Do not choose dates without this query.
2. Load the subagent spec and read all of it before spawning: `spec = rlm.harness.get("subagent", "partition-investigator", global_=True).content`, then `print(spec)` (never a truncated slice of it). Follow it exactly: build each child's prompt from its "Task for the child" section, spawn with `model="${child_model}"` and `thinking="off"`, half-open ranges only.
3. Use ${children} subagents, one per independent slice of the data (a date range, or the values of one dimension column), chosen from what step 1 found. Give every child the baseline period from step 1 as its comparison group, and an output file under `${run_dir}/` (for example `${run_dir}/slice-1.json`); use absolute paths.
4. After spawning, end your turn: reply with one line, such as `Spawned ${children} children; waiting for their reports.`, and make no further tool calls. Do not poll, loop or check for files: each child sends you a message when it is done, and that message starts your next turn. Continue only when all ${children} have reported (or failed); then read their files.
5. If a child fails or its file is missing or unreadable, say so in your findings; do not substitute numbers of your own for its slice. Check each explanation you keep or reject against the event, change and incident tables from step 1, with your own queries if the children did not cover them.
6. Every rate or share you report gives its numerator and denominator from a query in this run. Never assume a distribution (for example an even split) in place of measuring it.

Only after every child has reported or failed, write your final findings to `${run_dir}/findings.md` and also reply with them.
