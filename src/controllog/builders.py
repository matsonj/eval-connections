from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from .sdk import event, post, new_id


@contextmanager
def agent_run(*, task_id: str, agent_id: str, run_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Context manager capturing a logical agent run.

    Yields a dict with run metadata and helper methods bound via closures.
    """
    run_info = {"task_id": task_id, "agent_id": agent_id, "run_id": run_id}
    try:
        yield run_info
    finally:
        # Nothing to clean up for JSONL transport
        ...


def model_response(
    *,
    task_id: str,
    agent_id: str,
    run_id: Optional[str],
    project_id: Optional[str],
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    wall_ms: int,
    reward: Optional[float] = None,
    cost_money: Optional[float] = None,
    upstream_cost_money: Optional[float] = None,
    payload: Optional[Dict[str, Any]] = None,
    state_transition: Optional[Dict[str, str]] = None,
    request_text: Optional[str] = None,
    response_text: Optional[str] = None,
) -> None:
    """Emit a balanced model_response event with postings.

    Accounts balanced per spec:
      - resource.tokens: provider ↔ project (tokens unit)
      - resource.time_ms: agent ↔ project (ms unit)
      - truth.state: task WIP->DONE (tasks unit)
      - value.utility: task ↔ project (points unit, optional)
      - resource.money: vendor ↔ project (money unit, optional)
    """
    total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
    postings = [
        # tokens conservation
        post("resource.tokens", f"provider:{provider}", "+tokens", -total_tokens, {"model": model}),
        post("resource.tokens", f"project:{project_id}", "+tokens", +total_tokens, {"model": model}),
        # time conservation
        post("resource.time_ms", f"agent:{agent_id}", "ms", -int(wall_ms or 0), {"kind": "wall"}),
        post("resource.time_ms", f"project:{project_id}", "ms", +int(wall_ms or 0), {"kind": "wall"}),
    ]

    if state_transition is not None:
        frm = state_transition.get("from", "WIP")
        to = state_transition.get("to", "DONE")
        postings.extend(
            [
                post("truth.state", f"task:{task_id}", "tasks", -1, {"from": frm}),
                post("truth.state", f"task:{task_id}", "tasks", +1, {"to": to}),
            ]
        )

    if reward is not None:
        postings.extend(
            [
                post("value.utility", f"task:{task_id}", "points", +float(reward), {"metric": "reward"}),
                post("value.utility", f"project:{project_id}", "points", -float(reward), {"metric": "reward"}),
            ]
        )

    if cost_money is not None:
        postings.extend(
            [
                post("resource.money", f"vendor:openrouter", "$", -float(cost_money), {"model": model}),
                post("resource.money", f"project:{project_id}", "$", +float(cost_money), {"model": model}),
            ]
        )

    if upstream_cost_money is not None:
        # optionally track upstream vendor as separate vendor
        postings.extend(
            [
                post("resource.money", f"vendor:upstream", "$", -float(upstream_cost_money), {"model": model}),
                post("resource.money", f"project:{project_id}", "$", +float(upstream_cost_money), {"model": model}),
            ]
        )

    # Merge default payload with any extra payload
    payload_base: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "wall_ms": wall_ms,
    }
    if request_text is not None:
        payload_base["request_text"] = request_text
    if response_text is not None:
        payload_base["response_text"] = response_text

    event(
        kind="model_response",
        actor={"agent_id": agent_id, "task_id": task_id},
        run_id=run_id,
        payload={**payload_base, **(payload or {})},
        postings=postings,
        project_id=project_id,
        source="runtime",
    )


def model_prompt(
    *,
    task_id: str,
    agent_id: str,
    run_id: Optional[str],
    project_id: Optional[str],
    provider: str,
    model: str,
    prompt_tokens: int,
    request_text: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    exchange_id: Optional[str] = None,
) -> None:
    """Emit a model_prompt event with balanced token postings.

    Posts resource.tokens only; no time or money here.
    """
    postings = [
        post("resource.tokens", f"provider:{provider}", "+tokens", -int(prompt_tokens or 0), {"model": model, "phase": "prompt"}),
        post("resource.tokens", f"project:{project_id}", "+tokens", +int(prompt_tokens or 0), {"model": model, "phase": "prompt"}),
    ]
    payload_base: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "phase": "prompt",
    }
    if request_text is not None:
        payload_base["request_text"] = request_text

    if exchange_id is None:
        exchange_id = new_id()

    event(
        kind="model_prompt",
        actor={"agent_id": agent_id, "task_id": task_id},
        run_id=run_id,
        payload={**payload_base, **(payload or {}), "exchange_id": exchange_id},
        postings=postings,
        project_id=project_id,
        source="runtime",
        idempotency_key=f"{exchange_id}:prompt",
    )


def model_completion(
    *,
    task_id: str,
    agent_id: str,
    run_id: Optional[str],
    project_id: Optional[str],
    provider: str,
    model: str,
    completion_tokens: int,
    wall_ms: int,
    response_text: Optional[str] = None,
    cost_money: Optional[float] = None,
    upstream_cost_money: Optional[float] = None,
    payload: Optional[Dict[str, Any]] = None,
    exchange_id: Optional[str] = None,
) -> None:
    """Emit a model_completion event with completion tokens, time, and optional money.

    Balanced postings for resource.tokens and resource.time_ms; money optional.
    """
    postings = [
        post("resource.tokens", f"provider:{provider}", "+tokens", -int(completion_tokens or 0), {"model": model, "phase": "completion"}),
        post("resource.tokens", f"project:{project_id}", "+tokens", +int(completion_tokens or 0), {"model": model, "phase": "completion"}),
        post("resource.time_ms", f"agent:{agent_id}", "ms", -int(wall_ms or 0), {"kind": "wall"}),
        post("resource.time_ms", f"project:{project_id}", "ms", +int(wall_ms or 0), {"kind": "wall"}),
    ]
    if cost_money is not None:
        postings.extend(
            [
                post("resource.money", f"vendor:openrouter", "$", -float(cost_money), {"model": model}),
                post("resource.money", f"project:{project_id}", "$", +float(cost_money), {"model": model}),
            ]
        )
    if upstream_cost_money is not None:
        postings.extend(
            [
                post("resource.money", f"vendor:upstream", "$", -float(upstream_cost_money), {"model": model}),
                post("resource.money", f"project:{project_id}", "$", +float(upstream_cost_money), {"model": model}),
            ]
        )

    payload_base: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "completion_tokens": completion_tokens,
        "wall_ms": wall_ms,
        "phase": "completion",
    }
    if response_text is not None:
        payload_base["response_text"] = response_text

    if exchange_id is None:
        exchange_id = new_id()

    event(
        kind="model_completion",
        actor={"agent_id": agent_id, "task_id": task_id},
        run_id=run_id,
        payload={**payload_base, **(payload or {}), "exchange_id": exchange_id},
        postings=postings,
        project_id=project_id,
        source="runtime",
        idempotency_key=f"{exchange_id}:completion",
    )


def state_move(*, task_id: str, from_: str, to: str, project_id: Optional[str], agent_id: Optional[str] = None, run_id: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    postings = [
        post("truth.state", f"task:{task_id}", "tasks", -1, {"from": from_}),
        post("truth.state", f"task:{task_id}", "tasks", +1, {"to": to}),
    ]
    event(
        kind="state_move",
        actor={"agent_id": agent_id, "task_id": task_id} if agent_id else {"task_id": task_id},
        run_id=run_id,
        payload=payload or {"reason": None},
        postings=postings,
        project_id=project_id,
        source="runtime",
    )


def utility(*, task_id: str, project_id: str, metric: str, value: float, agent_id: Optional[str] = None, run_id: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    postings = [
        post("value.utility", f"task:{task_id}", "points", +float(value), {"metric": metric}),
        post("value.utility", f"project:{project_id}", "points", -float(value), {"metric": metric}),
    ]
    event(
        kind="utility",
        actor={"agent_id": agent_id, "task_id": task_id} if agent_id else {"task_id": task_id},
        run_id=run_id,
        payload=payload or {"metric": metric, "value": value},
        postings=postings,
        project_id=project_id,
        source="runtime",
    )


