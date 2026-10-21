
# Investigation Findings: Payment Transactions

**Question**: Compute payment attempts and decline rate for each half of the date range, and report both halves side by side with the SQL each child ran.

## Date Ranges
- **Half 1**: 2026-08-04 to 2026-09-10
- **Half 2**: 2026-09-11 to 2026-10-17

## Results

### Half 1 (2026-08-04 to 2026-09-10)
- **Total Attempts**: 1019051
- **Declined Transactions**: 35735
- **Decline Rate**: 3.51%
- **SQL**:
  ```sql
  
SELECT
    COUNT(*) AS total_attempts,
    SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) AS declined_transactions,
    (SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) * 100.0 / COUNT(*)) AS decline_rate
FROM payment_transactions
WHERE transaction_ts BETWEEN '2026-08-04' AND '2026-09-10 23:59:59'

  ```

### Half 2 (2026-09-11 to 2026-10-17)
- **Total Attempts**: 980949
- **Declined Transactions**: 46904
- **Decline Rate**: 4.78%
- **SQL**:
  ```sql
  
SELECT
    COUNT(*) AS total_attempts,
    SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) AS declined_transactions,
    (SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) * 100.0 / COUNT(*)) AS decline_rate
FROM payment_transactions
WHERE transaction_ts BETWEEN '2026-09-11' AND '2026-10-17 23:59:59'

  ```

## Comparison
| Metric               | Half 1 (2026-08-04 to 2026-09-10) | Half 2 (2026-09-11 to 2026-10-17) |
|----------------------|-----------------------------------|-----------------------------------|
| Total Attempts       | 1019051 | 980949 |
| Declined Transactions | 35735 | 46904 |
| Decline Rate         | 3.51% | 4.78% |

## Evidence
- All metrics are derived from the SQL queries executed in this session.
- The SQL queries and row counts are provided above.
- The `approved` column was used to determine declined transactions (where `approved = FALSE`).
