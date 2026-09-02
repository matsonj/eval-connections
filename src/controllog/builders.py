from typing import Any, Dict, Optional

from .sdk import event, post, new_id


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

