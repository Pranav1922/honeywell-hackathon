"""The LLM supervisor — slow tier of the two-tier control design.

Runs on a cadence rather than every timestep, because EnergyPlus callbacks
execute on the simulation thread and an annual run has tens of thousands of
steps. Between supervisory calls, an internal `ReactiveGuard` enforces the
active policy.

Failure is a designed path, not an exception handler: invalid model output is
returned to the model as a tool result for bounded self-repair, and when retries
or the timeout are exhausted the run continues under `BaselineScheduler` with
`fallback_used` recorded on the decision.

Two invariants hold on every path through this module:

**The supervisor never touches the simulator.** It produces a `ControlPolicy`
and nothing else. The `ControlAction` that reaches the building always comes out
of `ReactiveGuard.decide`, including on the fallback path, so there is no branch
on which a model response bypasses the guard's hard limits.

**Every decision is explained and logged.** One structured record per
supervisory call — inputs, prompt version, model, raw response, parsed policy,
execution time and validation status — emitted as JSON on the `ecoloop.agent`
logger and retained in memory for the dashboard.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Sequence

from app.agents.base import ControlPolicy, Decision
from app.agents.client import (
    LLMClient,
    LLMConfigError,
    LLMTransportError,
    extract_json_object,
)
from app.agents.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_correction,
    build_observation,
    build_tool_result,
    current_pmv,
)
from app.agents.tools import STRATEGIES, ToolRegistry
from app.sim.base import BuildingState
from app.utils.timeutil import is_cadence_due

LOGGER = logging.getLogger("ecoloop.agent")

POLICY_TOOL = "set_control_policy"
POLICY_FIELDS = (
    "strategy",
    "heating_sp_c",
    "cooling_sp_c",
    "lighting_level",
    "ventilation_ach",
    "rationale",
)
MAX_RATIONALE_CHARS = 600
REASONING_LOG_ENTRIES = 64
PREVIOUS_DECISIONS_SHOWN = 6


class LLMPlanError(RuntimeError):
    """The model produced no valid policy within its iteration and retry budget."""


@dataclasses.dataclass(frozen=True)
class _Plan:
    """The outcome of one supervisory call to the model."""

    policy: ControlPolicy
    rationale: str
    tool_calls: list[dict[str, Any]]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    retries: int
    response: str
    iterations: int


class LLMSupervisor:
    """Tool-calling supervisory controller backed by an open-source model."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        tools: ToolRegistry,
        guard,                      # ReactiveGuard, enforces every timestep
        fallback,                   # BaselineScheduler, used when the model fails
        cadence_steps: int = 4,     # supervise hourly at a 15-minute timestep
        max_tool_iterations: int = 5,
        max_retries: int = 2,
        max_consecutive_failures: int = 3,
    ) -> None:
        """Wire the slow tier to the fast tier.

        Args:
            client: The model transport. The only thing here that does I/O.
            tools: Registry the model may call, holding the run context.
            guard: `ReactiveGuard`. Enforces the policy and clamps it every
                timestep; the final authority on what reaches the building.
            fallback: `BaselineScheduler`. Supplies the policy when the model
                cannot.
            cadence_steps: How many timesteps one policy covers.
            max_tool_iterations: Ceiling on round trips per supervisory call,
                including tool calls. Bounds worst-case latency.
            max_retries: Self-correction attempts after a validation failure.
            max_consecutive_failures: After this many consecutive failed
                supervisory calls the model is abandoned for the rest of the run
                and the fallback takes over permanently. Without it a dead API
                key costs the full timeout on every cadence for the whole
                horizon, which turns a degraded run into an unusable one.
        """
        if cadence_steps <= 0:
            raise ValueError(f"cadence_steps must be positive, got {cadence_steps}")
        if max_tool_iterations <= 0:
            raise ValueError(
                f"max_tool_iterations must be positive, got {max_tool_iterations}"
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be non-negative, got {max_retries}")

        self._client = client
        self._tools = tools
        self._guard = guard
        self._fallback = fallback
        self._cadence_steps = cadence_steps
        self._max_tool_iterations = max_tool_iterations
        self._max_retries = max_retries
        self._max_consecutive_failures = max_consecutive_failures

        self._consecutive_failures = 0
        self._model_abandoned = False
        self._previous: deque[dict[str, Any]] = deque(maxlen=PREVIOUS_DECISIONS_SHOWN)
        self.reasoning_log: deque[dict[str, Any]] = deque(maxlen=REASONING_LOG_ENTRIES)

    @property
    def model(self) -> str:
        """The model identifier, recorded against the run."""
        return self._client.model

    def decide(
        self, state: BuildingState, history: Sequence[BuildingState]
    ) -> Decision:
        """Delegate to the guard; re-plan through the model when the cadence is due.

        Called every timestep. The expensive path is taken only on the cadence,
        and the cheap path is a guard call in microseconds — which is what makes
        an extended horizon feasible with a model that takes seconds to answer.
        """
        self._tools.context.observe(state, history)

        if not is_cadence_due(state.step, self._cadence_steps):
            return self._guard.decide(state, history)
        if self._model_abandoned:
            return self._fallback_decision(
                state,
                history,
                reason=(
                    f"model abandoned after {self._max_consecutive_failures} "
                    "consecutive failures"
                ),
                latency_ms=0,
            )

        try:
            plan = self._plan(state, history)
        except (LLMTransportError, LLMConfigError, LLMPlanError, ValueError) as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_consecutive_failures:
                self._model_abandoned = True
            self._log(
                state=state,
                model=self._client.model,
                latency_ms=0,
                response="",
                policy=None,
                tool_calls=[],
                retries=0,
                validation_status=f"failed: {type(exc).__name__}: {exc}",
                fallback_used=True,
            )
            return self._fallback_decision(
                state, history, reason=f"{type(exc).__name__}: {exc}", latency_ms=0
            )

        self._consecutive_failures = 0
        return self._adopt(state, history, plan)

    def reset(self) -> None:
        """Clear controller state between runs."""
        self._guard.reset()
        self._fallback.reset()
        self._tools.context.reset()
        self._consecutive_failures = 0
        self._model_abandoned = False
        self._previous.clear()
        self.reasoning_log.clear()

    # -- planning -----------------------------------------------------------

    def _plan(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
    ) -> _Plan:
        """Run the tool-calling loop and return the validated plan.

        Bounded twice: `max_tool_iterations` caps round trips, `max_retries` caps
        self-correction attempts. Both are needed — a model can loop on read
        tools without ever deciding, and it can also decide repeatedly and
        wrongly. Either alone leaves a way to spin.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_observation(
                    state, history, self._observation_targets(state)
                ),
            },
        ]
        schemas = self._tools.openai_schemas()

        calls: list[dict[str, Any]] = []
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        retries = 0
        last_response = ""

        for iteration in range(1, self._max_tool_iterations + 1):
            result = self._client.chat(messages, tools=schemas)
            latency_ms += result.latency_ms
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            last_response = result.content or last_response
            messages.append(result.raw_assistant_message)

            if result.tool_calls:
                proposal, error = self._run_tool_calls(result.tool_calls, calls, messages)
            else:
                proposal, error = self._recover_from_content(result.content, calls)

            if proposal is not None:
                try:
                    policy = self._validate(proposal, state)
                except ValueError as exc:
                    error = str(exc)
                else:
                    return _Plan(
                        policy=policy,
                        rationale=_clean_rationale(proposal.get("rationale")),
                        tool_calls=calls,
                        latency_ms=latency_ms,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        retries=retries,
                        response=last_response,
                        iterations=iteration,
                    )

            if error is not None:
                retries += 1
                if retries > self._max_retries:
                    raise LLMPlanError(
                        f"policy still invalid after {self._max_retries} "
                        f"self-correction attempt(s): {error}"
                    )
                messages.append(build_correction(error))

        raise LLMPlanError(
            f"no valid policy after {self._max_tool_iterations} tool iteration(s)"
        )

    def _run_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Dispatch every requested tool, returning any committed policy.

        Each call gets a tool-result message, including the failures — the chat
        API requires a reply per call id, and a model that is told *why* its call
        failed repairs it, while one that is told nothing repeats it.
        """
        proposal: dict[str, Any] | None = None
        error: str | None = None

        for call in tool_calls:
            name = call["name"]
            if call["parse_error"] is not None:
                payload: dict[str, Any] = {"error": call["parse_error"]}
                arguments: Any = call["arguments_raw"]
            else:
                arguments = call["arguments"]
                payload = self._tools.dispatch(name, arguments)

            calls.append({"name": name, "arguments": arguments, "result": payload})
            messages.append(build_tool_result(name, payload, call["id"]))

            if name != POLICY_TOOL:
                continue
            if "error" in payload:
                error = payload["error"]
            else:
                # The registry echoes back type-coerced values; use those rather
                # than the raw arguments so "22.5" has already become 22.5.
                proposal = payload

        return proposal, error

    def _recover_from_content(
        self, content: str, calls: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Salvage a policy the model wrote as prose instead of as a tool call.

        Correct reasoning in the wrong envelope is common and cheap to fix. The
        recovered object still goes through the registry, so it is validated
        exactly as a real tool call would be — recovery relaxes the transport, not
        the checks.
        """
        recovered = extract_json_object(content or "")
        if recovered is None:
            return None, (
                "you made no tool call and your reply contained no JSON policy "
                "object; call set_control_policy with the six required fields"
            )

        payload = self._tools.dispatch(POLICY_TOOL, recovered)
        calls.append(
            {
                "name": POLICY_TOOL,
                "arguments": recovered,
                "result": payload,
                "source": "recovered_from_message_content",
            }
        )
        if "error" in payload:
            return None, payload["error"]
        return payload, None

    def _validate(self, payload: dict, state: BuildingState) -> ControlPolicy:
        """Schema- and range-check a proposed policy. Raises on violation so the
        error text can be fed back to the model for self-correction.

        Checked here: presence, strategy vocabulary, finiteness, the dead-band,
        and the absolute safety envelope. *Not* checked here: whether the
        set-points are comfortable for the current occupancy — that is the
        guard's judgement, made every timestep against conditions this policy
        will outlive.

        The validity window is set by the supervisor, never by the model. A model
        that could choose its own expiry could grant itself an indefinite policy.
        """
        missing = [field for field in POLICY_FIELDS if payload.get(field) is None]
        if missing:
            raise ValueError(
                f"missing required field(s): {', '.join(missing)}. "
                f"Every one of {', '.join(POLICY_FIELDS)} is required."
            )

        strategy = str(payload["strategy"]).strip().lower()
        if strategy not in STRATEGIES:
            raise ValueError(
                f"strategy {payload['strategy']!r} is not recognised; "
                f"use one of: {', '.join(STRATEGIES)}"
            )

        heating_sp_c = _finite("heating_sp_c", payload["heating_sp_c"])
        cooling_sp_c = _finite("cooling_sp_c", payload["cooling_sp_c"])
        lighting_level = _finite("lighting_level", payload["lighting_level"])
        ventilation_ach = _finite("ventilation_ach", payload["ventilation_ach"])

        limits = self._tools.context.limits
        floor_c = float(limits.get("unoccupied_min_temp_c", 12.0))
        ceiling_c = float(limits.get("unoccupied_max_temp_c", 32.0))
        min_gap_c = float(limits.get("min_setpoint_gap_c", 1.5))
        max_vent = float(limits.get("max_ventilation_ach", 6.0))

        if cooling_sp_c - heating_sp_c < min_gap_c:
            raise ValueError(
                f"cooling_sp_c ({cooling_sp_c:.2f}) must be at least "
                f"{min_gap_c:.2f} C above heating_sp_c ({heating_sp_c:.2f}); "
                "a narrower dead-band makes heating and cooling fight each other"
            )
        for field, value in (
            ("heating_sp_c", heating_sp_c),
            ("cooling_sp_c", cooling_sp_c),
        ):
            if not floor_c <= value <= ceiling_c:
                raise ValueError(
                    f"{field} ({value:.2f}) is outside the safety envelope "
                    f"{floor_c:.1f}-{ceiling_c:.1f} C"
                )
        if not 0.0 <= lighting_level <= 1.0:
            raise ValueError(
                f"lighting_level ({lighting_level:.2f}) must be between 0.0 and 1.0"
            )
        if not 0.0 <= ventilation_ach <= max_vent:
            raise ValueError(
                f"ventilation_ach ({ventilation_ach:.2f}) must be between 0.0 and "
                f"{max_vent:.2f} air changes per hour"
            )
        if not str(payload["rationale"]).strip():
            raise ValueError("rationale must not be empty; explain the trade-off made")

        return ControlPolicy(
            strategy=strategy,
            heating_sp_c=heating_sp_c,
            cooling_sp_c=cooling_sp_c,
            lighting_level=lighting_level,
            ventilation_ach=ventilation_ach,
            expires_at_step=state.step + self._cadence_steps,
        )

    # -- decision assembly --------------------------------------------------

    def _adopt(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
        plan: _Plan,
    ) -> Decision:
        """Hand the policy to the guard and return the guard's decision.

        The action is the guard's, not the model's. If the guard had to clamp it,
        that is stated in the rationale rather than hidden — an override is
        evidence about the agent's judgement and belongs on the dashboard.
        """
        self._guard.set_policy(plan.policy)
        decision = self._guard.decide(state, history)

        rationale = plan.rationale
        if decision.guard_clamped:
            rationale += (
                " [Reactive guard clamped these set-points to hard comfort and "
                "equipment limits before they reached the building.]"
            )

        self._remember(state, plan)
        self._log(
            state=state,
            model=self._client.model,
            latency_ms=plan.latency_ms,
            response=plan.response,
            policy=plan.policy,
            tool_calls=plan.tool_calls,
            retries=plan.retries,
            validation_status=(
                "valid" if plan.retries == 0 else f"valid after {plan.retries} retry(ies)"
            ),
            fallback_used=False,
            guard_clamped=decision.guard_clamped,
            prompt_tokens=plan.prompt_tokens,
            completion_tokens=plan.completion_tokens,
            iterations=plan.iterations,
        )

        return dataclasses.replace(
            decision,
            rationale=rationale,
            tool_calls=plan.tool_calls,
            latency_ms=plan.latency_ms,
            prompt_tokens=plan.prompt_tokens,
            completion_tokens=plan.completion_tokens,
            retries=plan.retries,
        )

    def _fallback_decision(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
        reason: str,
        latency_ms: int,
    ) -> Decision:
        """Continue the run on the fixed schedule, still through the guard.

        The fallback policy is installed in the guard rather than applied
        directly, so the safety property holds identically on the degraded path:
        there is exactly one route from any policy to the building, and it is
        clamped.
        """
        fallback = self._fallback.decide(state, history)
        policy = dataclasses.replace(
            fallback.policy, expires_at_step=state.step + self._cadence_steps
        )
        self._guard.set_policy(policy)
        decision = self._guard.decide(state, history)

        return dataclasses.replace(
            decision,
            rationale=(
                f"{fallback.rationale} Supervisor unavailable, so the fixed "
                f"schedule is driving under guard enforcement ({_clip(reason)})."
            ),
            latency_ms=latency_ms,
            fallback_used=True,
        )

    # -- observation and logging -------------------------------------------

    def _observation_targets(self, state: BuildingState) -> dict[str, Any]:
        """Everything the model is allowed to know beyond the raw sensor state."""
        context = self._tools.context
        coolest_c, warmest_c = self._tools.comfort_band_now()
        return {
            **context.limits,
            **context.targets,
            "comfort_band_c": (coolest_c, warmest_c),
            "occupied_hours": context.occupied_hours,
            "total_kwh": context.total_kwh,
            "peak_kw": context.peak_kw,
            "cadence_steps": self._cadence_steps,
            "timestep_seconds": context.timestep_seconds,
            "history_window_steps": context.history_window_steps,
            "previous_decisions": list(self._previous),
        }

    def _remember(self, state: BuildingState, plan: _Plan) -> None:
        """Keep a short trail of decisions so the next window stays consistent."""
        self._previous.append(
            {
                "step": state.step,
                "strategy": plan.policy.strategy,
                "heating_sp_c": plan.policy.heating_sp_c,
                "cooling_sp_c": plan.policy.cooling_sp_c,
                "lighting_level": plan.policy.lighting_level,
                "ventilation_ach": plan.policy.ventilation_ach,
                "rationale": plan.rationale,
            }
        )

    def _log(
        self,
        state: BuildingState,
        model: str,
        latency_ms: int,
        response: str,
        policy: ControlPolicy | None,
        tool_calls: list[dict[str, Any]],
        retries: int,
        validation_status: str,
        fallback_used: bool,
        guard_clamped: bool = False,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        iterations: int = 0,
    ) -> dict[str, Any]:
        """Emit one structured reasoning record and retain it in memory.

        JSON on a dedicated logger rather than formatted text, because these
        records are meant to be queried after the fact — "which decisions were
        clamped", "what did latency do at hour 14" — and a log you have to
        re-parse to answer that is a log you will not use.
        """
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": "supervisory_decision",
            "step": state.step,
            "sim_time": state.sim_time.isoformat(),
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "inputs": {
                "zone_temp_c": round(state.zone_temp_c, 3),
                "outdoor_temp_c": round(state.outdoor_temp_c, 3),
                "occupancy": round(state.occupancy, 3),
                "co2_ppm": round(state.co2_ppm, 1),
                "pmv": round(current_pmv(state), 3),
                "power_kw": round(state.power_kw, 3),
                "total_kwh": round(self._tools.context.total_kwh, 4),
                "peak_kw": round(self._tools.context.peak_kw, 3),
            },
            "response": _clip(response, 1000),
            "tool_calls": tool_calls,
            "parsed_policy": dataclasses.asdict(policy) if policy is not None else None,
            "execution_time_ms": latency_ms,
            "validation_status": validation_status,
            "retries": retries,
            "tool_iterations": iterations,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "fallback_used": fallback_used,
            "guard_clamped": guard_clamped,
        }
        self.reasoning_log.append(record)
        LOGGER.info(json.dumps(record, default=str))
        return record


def _finite(field: str, value: Any) -> float:
    """A finite float, or a validation error naming the field."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number, got {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be a finite number, got {value!r}")
    return number


def _clean_rationale(value: Any) -> str:
    """Bound and tidy the explanation shown on the dashboard."""
    text = " ".join(str(value or "").split())
    return _clip(text, MAX_RATIONALE_CHARS) or "No rationale supplied."


def _clip(text: str, limit: int = 240) -> str:
    """Bound a string so one long generation cannot dominate a log or a row."""
    return text if len(text) <= limit else text[: limit - 3] + "..."
