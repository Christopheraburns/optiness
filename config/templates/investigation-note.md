# Investigation method

Investigations run against the `${database}` lakehouse through two skills: `lakehouse` (read-only SQL, Iceberg metadata) and `atlas` (catalog: descriptions, owners, classifications, lineage). Every skill function is async: `await` it. Use only the functions listed below.

## Skill API (exact; do not guess other names)

- `await lakehouse.query(sql, *, max_rows=None)` -> DataFrame; `df.attrs`: `sql`, `row_count`, `truncated`, `elapsed_ms`.
- `await lakehouse.tables()`, `await lakehouse.describe(table)` -> DataFrame (`name`, `type`, ...).
- `await lakehouse.profile(table)` -> DataFrame per column: `column`, `type`, `non_null`, `distinct`, `min`, `max`; `attrs["rows"]`.
- `await lakehouse.partitions(table)` (`partition`, `record_count`, `file_count`, ...), `await lakehouse.snapshots(table)`.
- `await lakehouse.explain(sql)` -> str. `await lakehouse.show(df, *, n=20)`. `await lakehouse.evidence()` -> DataFrame `sql`, `rows`, `truncated`, `elapsed_ms`.
- `await atlas.search(term, *, limit=20)` -> DataFrame `qualified_name`, `name`, `owner`, `classifications`, `description`.
- `await atlas.entity(table)` -> dict `qualified_name`, `name`, `description`, `owner`, `columns` [{`name`, `type`}], `classifications`.
- `await atlas.lineage(table, *, depth=3)` -> dict `inputs` [qualified-name str], `outputs` [str], `processes` [{`from`, `to`, ...}].
- `await atlas.classifications(table)` -> list[str].

`table` is a bare name such as `payment_transactions`. Read a skill's SKILL.md for detail.

## Method

1. Plan from metadata before querying. Use `atlas.search`, `atlas.entity` and `atlas.lineage` to learn what each table means and how tables join, and `lakehouse.partitions` for the date range and volume. Write the plan down: the question, the candidate explanations, and the comparison that would tell them apart.
2. Measure the symptom first: when it starts and ends, how large it is, and against what baseline. Then test candidate explanations by comparing affected and unaffected groups over the same period. Look for a control group that rules each explanation in or out, and correlate the onset with any event, change or incident records the catalog describes. Report the explanations you rejected and why.
3. Push computation into SQL. Aggregate in Impala and pull summaries, not raw rows; a result with `attrs["truncated"]` set means the query should aggregate further. Filter on `transaction_ts` ranges so Impala prunes daily partitions.
4. Delegate independent, context-heavy work to subagents, split by date range (partitions) or by the values of one dimension column the catalog describes. Run at most ${max_children} children at once; recursion depth is capped at ${rlm_max_depth}. Give each child the exact question, the tables and columns to use, and the evidence to return; have it write results to a file you read back.
5. Evidence: every number you report must come from a tool result in this session, never from memory or estimation. If a query fails, say so; do not fill in a plausible value. Keep `await lakehouse.evidence()` (each statement's SQL, row count and timing) and cite the SQL and row counts behind each finding.
6. Finish with findings: each claim, your confidence, and the evidence that supports it, followed by the explanations you ruled out.
