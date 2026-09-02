"""Reports and trial balance checks over controllog events/postings (DuckDB/MotherDuck)."""

from connections_eval.utils.motherduck import (  # noqa: F401  (re-export)
    connect_motherduck,
    trial_balance,
)


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
    ).fetchall()


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
    ).fetchall()


if __name__ == "__main__":
    con = connect_motherduck()
    trial_balance(con)
    print("Trial balance PASS")
    print("Flows (project_id, cost, utility):")
    for row in flows_cost_utility(con):
        print(f"  {row}")
    print("Ops (project_id, model, wall_ms):")
    for row in ops_latency_by_model(con):
        print(f"  {row}")
    con.close()
