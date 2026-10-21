Delegate one data slice per child; full spec: rlm.harness.get("subagent", "partition-investigator", global_=True).content

Partition investigator: a child that answers one question for one slice of the data (a date range, or one value of one dimension column) with read-only SQL, and returns evidence the parent can combine.

How the parent spawns one:

    spec = rlm.harness.get("subagent", "partition-investigator", global_=True).content
    handle = await rlm.spawn(task_prompt, name="slice-<short-id>", model="${child_model}", thinking="off")

Use one child per independent slice, at most ${max_children} at once. Slices must not overlap and together must cover everything: use half-open ranges (`transaction_ts >= 'A' AND transaction_ts < 'B'`, with B the next slice's A) or one dimension value each, never `<=`/`BETWEEN` on timestamps. Before reporting, run one whole-population count yourself and check the children's counts add up to it; if they do not, say so. `task_prompt` is this spec's "Task for the child" section with every <placeholder> filled in. After spawning, end your turn: do not poll or loop. Each child's `agent_message` starts your next turn; when all have reported, read their JSON files (do not trust a summary alone).

Task for the child:

You investigate one slice of a question about the `${database}` lakehouse. Use the `lakehouse` skill (API in AGENTS.md). Every skill function, including `agent_message.send`, is a Python coroutine: call it inside the REPL with `await`, for example `df = await lakehouse.query("SELECT COUNT(*) AS n FROM payment_transactions")`. None of them is a separate tool.
- Question: <the exact question>
- Slice: <the SQL predicate that defines the slice, e.g. a transaction_ts range or `column = 'value'`>
- Tables and columns: <the tables, join keys and columns to use>
- Measure: <the metrics to compute, and the comparison group if any>
- Output file: <absolute path ending in .json, given by the parent>

Rules:
1. Run the queries. You must call `lakehouse.query` in the REPL; an answer without tool calls is a failure. Every number you write must come from a query result in this session. If a query fails, record the error; never fill in a plausible value.
2. Aggregate in SQL and apply the slice predicate in every query.
3. Write the output file with Python: `{"slice": ..., "metrics": {...}, "evidence": <(await lakehouse.evidence()).to_dict("records")>, "notes": ...}`.
4. Then send the parent one line: `await agent_message.send("done: <output file> <one-sentence result>", receiver_role="parent")`, and stop. Do not spawn subagents, investigate beyond the slice, or speculate about causes outside it.
