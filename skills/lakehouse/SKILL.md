---
name: lakehouse
description: Read-only SQL over the Meridian Financial Iceberg tables in Impala. Use to list and describe tables, profile columns, read Iceberg partitions and snapshots, explain plans, and run SELECT queries that return pandas DataFrames with the SQL, row count and timing attached as evidence.
---

# Lakehouse

Query the `meridian_financial` database (Apache Iceberg tables served by Impala)
from the Python kernel. Every call is read-only: anything other than a single
SELECT / WITH / SHOW / DESCRIBE / EXPLAIN statement is refused before it is sent.
Results stay in the kernel as DataFrames; print summaries, not whole frames.

```python
tables = lakehouse.tables()                       # DataFrame: name
cols = lakehouse.describe("payment_transactions") # DataFrame: name, type, comment
prof = lakehouse.profile("merchants")             # one row per column: nulls, distinct, min, max

daily = lakehouse.query("""
    SELECT CAST(transaction_ts AS DATE) AS day, COUNT(*) AS attempts,
           AVG(CASE WHEN approved THEN 0 ELSE 1 END) AS decline_rate
    FROM payment_transactions GROUP BY 1 ORDER BY 1""")
print(daily.attrs["row_count"], daily.attrs["elapsed_ms"])
lakehouse.show(daily, 10)                         # capped print

parts = lakehouse.partitions("payment_transactions")  # Iceberg daily partitions
snaps = lakehouse.snapshots("payment_transactions")   # commit history
plan = lakehouse.explain("SELECT COUNT(*) FROM payment_transactions")
```

## API

- `query(sql, max_rows=None)`: run one read-only statement and return a DataFrame
  of at most `max_rows` rows (default `IMPALA_MAX_ROWS`, 5000). `df.attrs` holds
  `sql`, `row_count`, `truncated` and `elapsed_ms`. If `truncated` is True,
  aggregate in SQL instead of raising the cap.
- `tables()`, `describe(table)`: schema discovery.
- `profile(table)`: total rows plus per-column non-null count, approximate
  distinct count (`NDV`), and min/max for numeric, decimal, date and timestamp
  columns. One full scan; run it once per table and keep the result.
- `partitions(table)`, `snapshots(table)`: Iceberg metadata tables. Use
  partitions to plan work by day; snapshots show when data was committed.
- `explain(sql)`: the Impala plan as text, without running the query.
- `show(df, n=20)`: print the first `n` rows.
- `evidence()`: every statement this kernel ran, as a DataFrame of `sql`,
  `rows`, `truncated`, `elapsed_ms`. Cite these rows in findings.

Tables are named bare (`payment_transactions`) or qualified
(`meridian_financial.payment_transactions`). Filter on `transaction_ts` ranges
so Impala prunes daily partitions.
