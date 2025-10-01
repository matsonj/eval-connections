"""Reports and trial balance checks over controllog events/postings (DuckDB/MotherDuck)."""

import os
import duckdb  # type: ignore


def trial_balance(con) -> None:
    # Resource accounts must net to zero per unit
    rows = con.execute(
        """
        WITH sums AS (
          SELECT account_type, unit, SUM(delta_numeric) AS net
          FROM controllog.postings
          WHERE account_type LIKE 'resource.%' OR account_type IN ('truth.state','value.utility')
          GROUP BY 1,2
        )
        SELECT * FROM sums WHERE ABS(net) > 1e-9
        """
    ).fetchall()
    if rows:
        raise RuntimeError(f"Trial balance FAILED: {rows}")


def snapshot_sla(con):
    return con.execute(
        """
        SELECT e.project_id, SUM(p.delta_numeric) AS sla_debt
        FROM controllog.postings p
        JOIN controllog.events e ON e.event_id=p.event_id
        WHERE account_type='sla.promises'
        GROUP BY e.project_id
        """
    ).fetchdf()


def flows_cost_utility(con):
    return con.execute(
        """
        WITH p AS (
          SELECT e.project_id, p.account_type, SUM(p.delta_numeric) AS amt
          FROM controllog.postings p
          JOIN controllog.events e ON e.event_id=p.event_id
          WHERE account_type IN ('resource.money','value.utility')
          GROUP BY e.project_id, p.account_type
        )
        SELECT project_id,
               SUM(CASE WHEN account_type='resource.money' THEN -amt ELSE 0 END) AS cost,
               SUM(CASE WHEN account_type='value.utility'  THEN  amt ELSE 0 END) AS utility
        FROM p GROUP BY project_id
        """
    ).fetchdf()


def ops_latency_by_model(con):
    return con.execute(
        """
        SELECT e.project_id,
               COALESCE(e.payload_json->>'model', p.dims_json->>'model') AS model,
               AVG(-p.delta_numeric) AS wall_ms
        FROM controllog.events e
        JOIN controllog.postings p ON p.event_id=e.event_id
        WHERE e.kind IN ('model_completion')
          AND p.account_type='resource.time_ms'
          AND p.unit='ms'
          AND p.account_id LIKE 'agent:%'
        GROUP BY 1,2
        """
    ).fetchdf()


if __name__ == "__main__":
    db = os.environ.get("MOTHERDUCK_DB", "md:controllog")
    con = duckdb.connect(db)
    trial_balance(con)
    print("Trial balance PASS")
    print("Flows (cost, utility):")
    print(flows_cost_utility(con))
    print("Ops (latency by model):")
    print(ops_latency_by_model(con))
    con.close()



