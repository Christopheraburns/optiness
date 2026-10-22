# Dataset: Meridian Financial, 50M on CDW

The demo and FDE dataset in the Cloudera Data Warehouse (BURNDOWN D6). Bankwide facts
only: the planted scenario, its segments and its dates are recorded in the Meridian repo's
private output and are deliberately not described here, because this repo is readable by
the agent's Python.

| Item | Value |
|---|---|
| Run | `bc6c1531-e9be-4976-9932-557f833ec199` (Meridian `config/demo-50m.yaml`) |
| Generated | 2026-10-21/22 on Cloudera AI Workbench (Meridian `scripts/validate_scenario.py`, M2 acceptance passed) |
| Loaded | 2026-10-22 by Meridian M4 `scripts/warehouse.py load` (`impala_staged_load`) from S3 staging |
| Warehouse | CDW Impala virtual warehouse `coordinator-impala-aws-vw.dw-applied-ai-cdp-env.jmgjgh.cloudera.site` |
| Database | `meridian_financial` (10 Iceberg v2 tables); private truth in `meridian_ground_truth` |
| Date range | 2026-08-04 to 2026-10-17, 75 days |

## Rows

| Table | Rows |
|---|---:|
| `payment_transactions` | 50,000,000 in 75 daily partitions (`DAY(transaction_ts)`) |
| `customers` | 500,000 |
| `merchants` | 20,000 |
| `payment_terminals` | 80,000 |
| `payment_processors` | 8 |
| `generation_runs` | 1 (`run_status = complete`) |
| `routing_rules`, `deployment_events`, `processor_incidents`, `transaction_response_codes` | equal to the generated counts (verified by the loader); exact figures not recorded |

Overall: 2,065,414 declines in 50,000,000 authorization attempts (4.13%).

Size: 1.6 GB of public Parquet, of which `payment_transactions` is 1,675,467,651 bytes in
500 files of 100,000 rows. Iceberg sizes in CDW: `SHOW TABLE STATS meridian_financial.<table>`.

## Verification

- **Loader, through Impala:** every table's committed count equals the generated count; Impala
  types match the contract; each table has an Iceberg snapshot; one complete marker row;
  0 duplicate `transaction_id`; 0 orphan transactions against customers, merchants,
  terminals, processors, routing rules and response codes; 75 transaction days.
- **Reconciliation against the private evaluator** (Meridian `scripts/reconcile_impala.py`):
  23/23 comparison groups match exactly. The group-level output stays in Meridian's
  private directory.

## Known gaps

- Loaded with one login (`cburns`) for every role (`--accept-shared-login`,
  `production_ready=False`). Until a Ranger-restricted agent login exists and Meridian's
  `preflight` passes without that flag, the agent must not query CDW as `cburns`: that
  login can read `meridian_ground_truth`.
