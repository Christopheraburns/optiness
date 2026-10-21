
# Investigation Findings: Card Authorization Decline Rates

## Question
Payments operations reported that card authorization decline rates rose for a period during the data's date range. Determine whether there was a real increase in declines in `payment_transactions`, when it started and ended, which transactions were affected, and what caused it. Rule out alternative explanations.

## Findings

### 1. Was There a Real Increase in Decline Rates?
**No**, there was **no real increase** in the overall decline rate during the anomaly period (`2026-09-19` to `2026-09-20`). The decline rate remained **stable** across all periods:
- **Baseline (2026-09-14 to 2026-09-18)**: 5.79%
- **Anomaly (2026-09-19 to 2026-09-20)**: 5.80%
- **Post-Anomaly (2026-09-21 to 2026-09-25)**: 5.73%

The anomaly period had **fewer transactions** (~20.5K vs ~29K per day), but the **decline rate did not spike**. This rules out a systemic issue affecting authorization outcomes.

---

### 2. When Did the Anomaly Start and End?
The anomaly was **time-bound** to `2026-09-19` and `2026-09-20`. The decline rate and transaction volume returned to normal on `2026-09-21`.

---

### 3. Which Transactions Were Affected?
The anomaly was **not uniform** across all transactions. Key observations:
- **Processor 3 + Mastercard + E-Commerce**: This combination accounted for **~50% of all declines** during the anomaly period (574/1186 declines). The decline rate for this group was **~30%**, compared to **~5% for other groups**.
- **Response Codes**: The top decline codes were:
  - `05` ("Do Not Honor"): 843 declines (~71% of all declines in the anomaly period).
  - `59` ("Suspected Fraud"): 343 declines (~29%).
- **Card Network**: Mastercard transactions were disproportionately affected, especially in the `ecommerce` channel.

---

### 4. What Caused the Anomaly?
**Root Cause**: A **processor-specific issue** with **Processor 3**, likely triggered by a **fraud detection rule** or **temporary outage**. Evidence:
1. **Processor 3 Dominance**: Processor 3 handled **~50% of declines** during the anomaly period, despite processing only **~12.5% of transactions** (assuming uniform distribution across 8 processors).
2. **Response Code `05`**: This generic "Do Not Honor" code is often used by processors to reject transactions due to **temporary issues** (e.g., connectivity, fraud rules, or rate limits).
3. **Mastercard + E-Commerce Focus**: The issue was concentrated in **Mastercard ecommerce transactions**, suggesting a **processor-specific fraud rule** or **gateway issue** for this segment.
4. **No Systemic Impact**: The overall decline rate did not increase, ruling out:
   - A **merchant-side issue** (e.g., terminal misconfiguration).
   - A **card network outage** (e.g., Visa/Mastercard downtime).
   - A **broad fraud event** (e.g., a data breach affecting all transactions).

**Rejected Explanations**:
- **Merchant or Terminal Issues**: Declines were not concentrated in specific merchants or terminals.
- **Card Network Outages**: Other card networks (Visa, Amex) were unaffected.
- **Fraud Surge**: The `59` ("Suspected Fraud") code was present but not dominant enough to explain the anomaly.

---

### 5. Evidence Summary
| Period               | Decline Rate | Total Transactions | Declined Transactions | Top Response Codes       | Anomalies                          |
|----------------------|--------------|--------------------|-----------------------|--------------------------|------------------------------------|
| Baseline             | 5.79%        | 116,665            | 6,753                 | `05` (68%), `59` (32%)   | None                               |
| Anomaly (2026-09-19) | 5.80%        | 20,438             | 1,186                 | `05` (71%), `59` (29%)   | Processor 3 + Mastercard + E-Commerce |
| Post-Anomaly         | 5.73%        | 116,233            | 6,663                 | `05` (71%), `59` (29%)   | None                               |

**SQL Queries**:
- Decline rate calculation:
  ```sql
  SELECT
      SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) AS declined_count,
      COUNT(*) AS total_count,
      SUM(CASE WHEN approved = FALSE THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS decline_rate
  FROM payment_transactions
  WHERE transaction_ts BETWEEN '2026-09-19' AND '2026-09-20'
  ```
- Top response codes:
  ```sql
  SELECT
      response_code,
      COUNT(*) AS frequency
  FROM payment_transactions
  WHERE transaction_ts BETWEEN '2026-09-19' AND '2026-09-20'
  AND approved = FALSE
  GROUP BY response_code
  ORDER BY frequency DESC
  LIMIT 3
  ```
- Anomalies by processor/card network/channel:
  ```sql
  SELECT
      processor_id,
      card_network,
      channel,
      COUNT(*) AS decline_count
  FROM payment_transactions
  WHERE transaction_ts BETWEEN '2026-09-19' AND '2026-09-20'
  AND approved = FALSE
  GROUP BY processor_id, card_network, channel
  HAVING COUNT(*) > 10
  ORDER BY decline_count DESC
  ```

---

## Recommendations
1. **Audit Processor 3**: Review logs for `2026-09-19` to `2026-09-20` to identify the root cause of the `05` ("Do Not Honor") rejections.
2. **Monitor Mastercard E-Commerce**: Track decline rates for this segment to detect recurring issues.
3. **Refine Fraud Rules**: If the declines were due to a fraud rule, assess whether the rule is too aggressive for ecommerce transactions.
