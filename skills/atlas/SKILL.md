---
name: atlas
description: Read the data catalog (Apache Atlas, or its local stand-in) for the Meridian Financial tables. Use to find tables by keyword, read a table's description, owner, columns and classifications, and trace lineage (load sources and foreign-key relationships) before planning SQL.
---

# Atlas

Read-only access to the metadata catalog from the Python kernel. Every function is async: `await` it
(several can run at once with `asyncio.gather`). The same calls
work against Apache Atlas on Cloudera and the local stub (`METADATA_BACKEND`).
Use it to plan an investigation: which tables exist, what they mean, who owns
them, and how they join.

```python
hits = await atlas.search("routing")                   # DataFrame of matching tables
t = await atlas.entity("payment_transactions")         # dict: description, owner, columns, classifications
lin = await atlas.lineage("payment_transactions")      # dict: inputs, outputs, processes
tags = await atlas.classifications("customers")        # list of classification names
```

## API

- `search(term, limit=20)`: tables whose name, qualified name, description or
  column names contain `term`. DataFrame: `qualified_name`, `name`, `owner`,
  `classifications`, `description`.
- `entity(table)`: one table as a dict: `qualified_name`, `name`, `description`,
  `owner`, `columns` (list of `{name, type}`), `classifications`.
- `lineage(table)`: dict with `inputs` and `outputs` (qualified names) and
  `processes` (each edge; `fk_<column>` edges are join keys, `impala_insert_*`
  edges are load jobs).
- `classifications(table)`: list of classification names.

`table` is a bare name (`payment_transactions`), `db.table`, or a full Atlas
qualified name (`db.table@cluster`). A name that matches nothing raises
`LookupError` listing the closest catalog entries.
