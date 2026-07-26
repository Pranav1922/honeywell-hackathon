"""Milestone 2 tests: the Groq transport, the prompts, the tools and the
supervisor that ties them together.

No test here reaches the network. The Groq SDK is replaced by a scripted
transport, which is what makes it possible to test the cases that matter and are
otherwise unreachable: a malformed tool call, a timeout, a rate limit, a policy
that has to be repaired twice, a model that never answers at all.

The property most of these tests exist to protect is the one in D3 — that no
model output reaches the building without passing the reactive guard.
"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from app import db
from app.agents.base import STRATEGY_HOLD, STRATEGY_SETBACK
from app.agents.client import (
    GROQ_API_KEY_ENV,
    GROQ_DEFAULT_MODEL,
    GROQ_MODEL_ENV,
    ChatResult,
    LLMClient,
    LLMConfigError,
    LLMTransportError,
    extract_json_object,
    parse_tool_arguments,
)
from app.agents.llm import LLMPlanError, LLMSupervisor
from app.agents.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_correction,
    build_observation,
    build_tool_result,
)
from app.agents.rule import BaselineScheduler, ReactiveGuard
from app.agents.tools import TOOL_NAMES, AgentContext, ToolRegistry
from tests.conftest import build_history, build_state

MODEL = "llama-3.3-70b-versatile"
REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

LIMITS = {
    "min_zone_temp_c": 19.0,
    "max_zone_temp_c": 26.0,
    "comfort_pmv_low": -0.5,
    "comfort_pmv_high": 0.5,
    "min_setpoint_gap_c": 1.5,
    "min_ventilation_ach": 0.5,
    "max_ventilation_ach": 6.0,
    "max_co2_ppm": 1000.0,
    "unoccupied_min_temp_c": 12.0,
    "unoccupied_max_temp_c": 32.0,
}
TARGETS = {
    "tariff_per_kwh": 0.18,
    "grid_carbon_kg_per_kwh": 0.42,
    "peak_demand_kw": 10.0,
}

VALID_POLICY = {
    "strategy": "hold",
    "heating_sp_c": 21.0,
    "cooling_sp_c": 25.5,
    "lighting_level": 0.6,
    "ventilation_ach": 1.2,
    "rationale": "Widened the dead-band to the comfort-band edge; PMV stays inside.",
}


# -- test doubles -----------------------------------------------------------


def reply(
    content: str = "",
    tool_calls: tuple[tuple[str, str], ...] = (),
    prompt_tokens: int = 900,
    completion_tokens: int = 40,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    """A Groq chat-completion response, shaped as the SDK returns it."""
    calls = [
        SimpleNamespace(
            id=f"call_{index}",
            type="function",
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        for index, (name, arguments) in enumerate(tool_calls)
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=calls or None),
                finish_reason="tool_calls" if calls else finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


def policy_call(**overrides) -> tuple[tuple[str, str], ...]:
    """A `set_control_policy` tool call carrying a valid policy plus overrides."""
    payload = {**VALID_POLICY, **overrides}
    return (("set_control_policy", json.dumps(payload)),)


class FakeTransport:
    """A scripted stand-in for `groq.Groq`, recording every request it receives."""

    def __init__(self, script=(), default=None, models=(MODEL,)) -> None:
        self.requests: list[dict] = []
        self._script = list(script)
        self._default = default
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[SimpleNamespace(id=name) for name in models]
            )
        )

    def _create(self, **kwargs):
        # Snapshot the conversation: the supervisor appends to the same list
        # across a tool-calling loop, so storing the reference would make every
        # recorded request alias the final state of it.
        self.requests.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if self._script:
            item = self._script.pop(0)
        elif self._default is not None:
            item = self._default(kwargs) if callable(self._default) else self._default
        else:
            raise AssertionError("fake transport ran out of scripted responses")
        if isinstance(item, Exception):
            raise item
        return item


class CompetentSupervisorModel:
    """A minimal state-aware stand-in used for the end-to-end run.

    It reads the occupancy line back out of the observation and answers with a
    wide comfortable band when the zone is in use and a deep setback when it is
    not — which is what a competent supervisor is supposed to do, and enough to
    prove the closed loop carries a model's policy into the physics.
    """

    def __call__(self, request: dict) -> SimpleNamespace:
        observation = request["messages"][-1]["content"]
        match = re.search(r"^occupancy\s+([\d.]+)", observation, re.MULTILINE)
        occupied = bool(match) and float(match.group(1)) > 0.0
        if occupied:
            return reply(
                tool_calls=policy_call(
                    strategy=STRATEGY_HOLD,
                    heating_sp_c=21.0,
                    cooling_sp_c=25.8,
                    lighting_level=0.6,
                    ventilation_ach=1.0,
                    rationale="Occupied: holding the widest comfortable band.",
                )
            )
        return reply(
            tool_calls=policy_call(
                strategy=STRATEGY_SETBACK,
                heating_sp_c=14.0,
                cooling_sp_c=30.0,
                lighting_level=0.0,
                ventilation_ach=0.5,
                rationale="Zone empty: deep setback, lights off, minimum ventilation.",
            )
        )


def build_client(script=(), default=None, **kwargs) -> tuple[LLMClient, FakeTransport]:
    """An `LLMClient` over a scripted transport, bypassing credential lookup."""
    transport = FakeTransport(script, default=default)
    return (
        LLMClient(model=MODEL, client=transport, **kwargs),
        transport,
    )


def build_supervisor(script=(), default=None, cadence_steps=4, **kwargs):
    """The full two-tier controller over a scripted transport."""
    client, transport = build_client(script, default=default, retries=0)
    context = AgentContext(
        timestep_seconds=900,
        limits=dict(LIMITS),
        targets=dict(TARGETS),
        occupied_hours=(8, 18),
        history_window_steps=96,
    )
    supervisor = LLMSupervisor(
        client=client,
        tools=ToolRegistry(context),
        guard=ReactiveGuard(**LIMITS, occupied_hours=(8, 18)),
        fallback=BaselineScheduler(occupied_hours=(8, 18)),
        cadence_steps=cadence_steps,
        **kwargs,
    )
    return supervisor, transport


# -- the Groq client --------------------------------------------------------


def test_a_missing_api_key_fails_immediately_with_an_actionable_message(monkeypatch):
    """A run must not start and silently degrade because a key was forgotten."""
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(LLMConfigError) as excinfo:
        LLMClient()

    message = str(excinfo.value)
    assert GROQ_API_KEY_ENV in message
    assert "console.groq.com" in message
    assert "--controller=baseline" in message


def test_a_blank_api_key_is_treated_as_missing(monkeypatch):
    """`GROQ_API_KEY=` in a .env is the commonest way to have no key at all."""
    monkeypatch.setenv(GROQ_API_KEY_ENV, "   ")

    with pytest.raises(LLMConfigError, match=GROQ_API_KEY_ENV):
        LLMClient()


def test_configuration_comes_from_the_environment(monkeypatch):
    """Nothing machine-specific is hardcoded; the key is never defaulted."""
    monkeypatch.setenv(GROQ_API_KEY_ENV, "gsk_test_key")
    monkeypatch.setenv(GROQ_MODEL_ENV, "llama-3.1-8b-instant")

    client = LLMClient()

    assert client.model == "llama-3.1-8b-instant"

    monkeypatch.delenv(GROQ_MODEL_ENV)
    assert LLMClient().model == GROQ_DEFAULT_MODEL


def test_invalid_transport_settings_are_rejected_at_construction():
    """A zero timeout would put an unbounded call on the control path."""
    with pytest.raises(LLMConfigError, match="timeout_seconds must be positive"):
        LLMClient(api_key="k", timeout_seconds=0)
    with pytest.raises(LLMConfigError, match="retries must be non-negative"):
        LLMClient(api_key="k", retries=-1)


def test_chat_returns_content_tool_calls_and_token_accounting():
    """The dashboard reports latency and tokens, so the client has to carry them."""
    client, transport = build_client([reply("thinking", policy_call())])

    result = client.chat([{"role": "user", "content": "go"}], tools=[{"x": 1}])

    assert isinstance(result, ChatResult)
    assert result.content == "thinking"
    assert result.prompt_tokens == 900
    assert result.completion_tokens == 40
    assert result.latency_ms >= 0
    assert result.attempts == 1
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["name"] == "set_control_policy"
    assert call["arguments"]["heating_sp_c"] == 21.0
    assert call["parse_error"] is None
    assert transport.requests[0]["model"] == MODEL
    assert transport.requests[0]["tool_choice"] == "auto"


def test_tools_are_omitted_from_the_request_when_there_are_none():
    """Some models refuse an empty tools array; do not send one."""
    client, transport = build_client([reply("hello")])

    client.chat([{"role": "user", "content": "go"}])

    assert "tools" not in transport.requests[0]
    assert "tool_choice" not in transport.requests[0]


def test_an_empty_conversation_is_a_caller_bug():
    """Cheaper to fail here than to pay a round trip to find out."""
    client, _ = build_client([reply()])

    with pytest.raises(ValueError, match="messages must not be empty"):
        client.chat([])


def test_malformed_tool_arguments_are_reported_as_data_not_raised():
    """The supervisor has to see the bad JSON to tell the model what was wrong."""
    client, _ = build_client(
        [reply(tool_calls=(("set_control_policy", "{strategy: hold, temp:}"),))]
    )

    result = client.chat([{"role": "user", "content": "go"}])

    assert result.tool_calls[0]["parse_error"] is not None
    assert "not valid JSON" in result.tool_calls[0]["parse_error"]
    assert result.tool_calls[0]["arguments"] == {}


def test_the_assistant_turn_can_be_replayed_with_its_call_ids():
    """The chat API requires the tool-call turn to precede the tool results."""
    client, _ = build_client([reply("", policy_call())])

    message = client.chat([{"role": "user", "content": "go"}]).raw_assistant_message

    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["id"] == "call_0"
    assert message["tool_calls"][0]["function"]["name"] == "set_control_policy"


def test_a_transport_failure_is_retried_and_then_succeeds():
    """A single dropped connection must not cost a control decision."""
    client, transport = build_client(
        [APIConnectionError(request=REQUEST), reply("", policy_call())], retries=2
    )

    result = client.chat([{"role": "user", "content": "go"}])

    assert result.attempts == 2
    assert len(transport.requests) == 2


def test_a_timeout_is_retried_within_the_budget_then_reported():
    """A model that is merely slow is the expected failure, not an exception."""
    client, transport = build_client([APITimeoutError(request=REQUEST)] * 3, retries=2)

    with pytest.raises(LLMTransportError) as excinfo:
        client.chat([{"role": "user", "content": "go"}])

    assert len(transport.requests) == 3
    assert "APITimeoutError" in str(excinfo.value)
    assert "3 attempt(s)" in str(excinfo.value)


def test_a_rate_limit_is_retryable():
    """Groq is a shared hosted endpoint; 429 is transient by definition."""
    client, transport = build_client(
        [
            RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None),
            reply("", policy_call()),
        ],
        retries=1,
    )

    assert client.chat([{"role": "user", "content": "go"}]).attempts == 2
    assert len(transport.requests) == 2


def test_a_client_error_is_not_retried_and_names_the_cause():
    """A bad key or an unknown model fails identically however often it is sent."""
    error = APIStatusError(
        "unauthorized",
        response=httpx.Response(401, request=REQUEST),
        body={"error": {"message": "Invalid API Key"}},
    )
    client, transport = build_client([error], retries=2)

    with pytest.raises(LLMTransportError, match="Invalid API Key"):
        client.chat([{"role": "user", "content": "go"}])

    assert len(transport.requests) == 1


def test_retries_can_be_disabled_entirely():
    """A tight cadence may prefer a fast fallback over a slow retry."""
    client, transport = build_client([APIConnectionError(request=REQUEST)], retries=0)

    with pytest.raises(LLMTransportError):
        client.chat([{"role": "user", "content": "go"}])

    assert len(transport.requests) == 1


def test_available_reports_whether_the_endpoint_serves_the_model():
    """`/api/health` and the run-start check both read this."""
    client, _ = build_client()
    assert client.available() is True

    absent = LLMClient(model="not-a-model", client=FakeTransport(models=(MODEL,)))
    assert absent.available() is False

    class Unreachable:
        models = SimpleNamespace(list=lambda: (_ for _ in ()).throw(OSError("down")))

    assert LLMClient(model=MODEL, client=Unreachable()).available() is False


# -- JSON parsing -----------------------------------------------------------


def test_well_formed_arguments_parse_directly():
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments("   ") == {}


def test_a_fenced_code_block_is_unwrapped():
    """Models add markdown fences to JSON even when told not to."""
    assert parse_tool_arguments('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_embedded_in_prose_is_recovered():
    """Correct reasoning in the wrong envelope should not cost a round trip."""
    text = 'Here is my decision:\n{"strategy": "hold"}\nHope that helps.'

    assert parse_tool_arguments(text) == {"strategy": "hold"}


def test_a_brace_inside_a_string_does_not_end_the_object_early():
    """The rationale is free text and will eventually contain a brace."""
    text = '{"rationale": "set {A} then {B}", "n": 2}'

    assert extract_json_object(text) == {"rationale": "set {A} then {B}", "n": 2}


def test_a_non_object_payload_is_rejected_with_a_reason():
    """A JSON array is valid JSON and still an unusable policy."""
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_tool_arguments("[1, 2, 3]")


def test_unrecoverable_text_raises_with_the_parse_position():
    """The reason is what gets handed back to the model, so it has to be specific."""
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_tool_arguments("the temperature should be about 22 degrees")

    assert extract_json_object("no object here") is None


# -- prompt generation ------------------------------------------------------


def test_the_system_prompt_states_the_output_contract_and_the_priorities():
    """Deliverable 4 claims prompt engineering; this is the reviewable part."""
    assert "set_control_policy" in SYSTEM_PROMPT
    assert "SAFETY" in SYSTEM_PROMPT and "COMFORT" in SYSTEM_PROMPT
    assert "deterministic" in SYSTEM_PROMPT
    for strategy in ("hold", "precool", "preheat", "setback", "peak_shave"):
        assert strategy in SYSTEM_PROMPT


def test_the_observation_carries_every_input_the_agent_is_required_to_see():
    """Temperatures, occupancy, CO2, PMV, energy, tariff, weather, goals, history."""
    observation = build_observation(
        build_state(),
        build_history(96),
        {**LIMITS, **TARGETS, "comfort_band_c": (22.6, 26.9), "total_kwh": 142.3,
         "peak_kw": 9.8, "cadence_steps": 4, "timestep_seconds": 900,
         "occupied_hours": (8, 18), "previous_decisions": []},
    )

    for expected in (
        "zone air temperature",
        "outdoor air temperature",
        "occupancy",
        "CO2",
        "PMV",
        "PPD",
        "instantaneous power",
        "== WEATHER ==",
        "142.30 kWh",
        "0.180 per kWh",
        "kg CO2 per kWh",
        "peak demand",
        "22.6 .. 26.9 C",
        "min_deadband_c",
        "CO2 ceiling",
        "== RECENT TELEMETRY ==",
        "== YOUR PREVIOUS DECISIONS ==",
        "set_control_policy exactly once",
    ):
        assert expected in observation, expected


def test_the_observation_is_deterministic():
    """Identical conditions must produce an identical prompt, or the decision
    cannot be reproducible either."""
    state, history = build_state(), build_history(48)
    targets = {**LIMITS, **TARGETS, "comfort_band_c": (22.6, 26.9)}

    assert build_observation(state, history, targets) == build_observation(
        state, history, targets
    )


def test_the_observation_survives_an_empty_history_and_sparse_targets():
    """The first supervisory call has neither, and must still produce a prompt."""
    observation = build_observation(build_state(step=0), (), {})

    assert "no history yet" in observation
    assert "this is the first window" in observation


def test_previous_decisions_are_rendered_so_the_agent_stays_consistent():
    """Without memory across windows the agent oscillates on identical inputs."""
    observation = build_observation(
        build_state(),
        build_history(8),
        {
            "previous_decisions": [
                {
                    "step": 92,
                    "strategy": "setback",
                    "heating_sp_c": 16.0,
                    "cooling_sp_c": 28.0,
                    "lighting_level": 0.0,
                    "ventilation_ach": 0.5,
                    "rationale": "Zone empty overnight.",
                }
            ]
        },
    )

    assert "step    92  setback" in observation
    assert "Zone empty overnight." in observation


def test_a_tool_result_is_shaped_for_the_chat_api():
    message = build_tool_result("get_comfort_limits", {"pmv_band": [-0.5, 0.5]}, "call_7")

    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_7"
    assert json.loads(message["content"]) == {"pmv_band": [-0.5, 0.5]}


def test_a_correction_hands_the_validation_error_back_verbatim():
    """This is the whole self-correction mechanism: name the broken constraint."""
    message = build_correction("cooling_sp_c must be at least 1.50 C above heating_sp_c")

    assert message["role"] == "user"
    assert "cooling_sp_c must be at least 1.50 C above heating_sp_c" in message["content"]
    assert "REJECTED" in message["content"]


# -- the tool layer ---------------------------------------------------------


@pytest.fixture
def registry() -> ToolRegistry:
    """A registry over a context that has already seen a day of telemetry."""
    context = AgentContext(
        timestep_seconds=900,
        limits=dict(LIMITS),
        targets=dict(TARGETS),
        occupied_hours=(8, 18),
    )
    history = build_history(96)
    for state in history:
        context.observe(state, history[: state.step + 1])
    return ToolRegistry(context)


def test_every_architected_tool_is_registered_in_both_schema_formats(registry):
    """One registry, two projections — the MCP server adds no second source."""
    assert registry.names == TOOL_NAMES

    openai = registry.openai_schemas()
    mcp = registry.mcp_schemas()

    assert [entry["function"]["name"] for entry in openai] == list(TOOL_NAMES)
    assert [entry["name"] for entry in mcp] == list(TOOL_NAMES)
    assert all(entry["type"] == "function" for entry in openai)
    assert all(entry["function"]["description"] for entry in openai)
    assert all(entry["inputSchema"]["type"] == "object" for entry in mcp)


def test_an_unknown_tool_returns_an_error_payload_rather_than_raising(registry):
    """A hallucinated tool name is something the model can be told about."""
    result = registry.dispatch("rm_rf_slash", {"path": "/"})

    assert "unknown tool" in result["error"]
    assert result["available_tools"] == list(TOOL_NAMES)


def test_unknown_arguments_are_refused_so_nothing_unapproved_is_reachable(registry):
    """The tool surface is exactly the declared schema and nothing wider."""
    result = registry.dispatch("get_recent_telemetry", {"eval": "__import__('os')"})

    assert "unknown argument(s) eval" in result["error"]


def test_missing_required_arguments_are_named(registry):
    result = registry.dispatch("evaluate_policy", {"heating_sp_c": 21.0})

    assert "missing required argument(s) cooling_sp_c" in result["error"]


def test_numeric_strings_are_coerced_rather_than_rejected(registry):
    """Models quote numbers; failing over a quotation mark wastes a repair round."""
    result = registry.dispatch(
        "evaluate_policy", {"heating_sp_c": "21.0", "cooling_sp_c": "25.5"}
    )

    assert result["deadband_c"] == 4.5


def test_a_word_where_a_number_belongs_still_fails(registry):
    result = registry.dispatch(
        "evaluate_policy", {"heating_sp_c": "warm", "cooling_sp_c": 25.0}
    )

    assert "heating_sp_c must be a number" in result["error"]


def test_out_of_range_and_out_of_vocabulary_arguments_are_bounded(registry):
    assert "at most" in registry.dispatch(
        "evaluate_policy", {"heating_sp_c": 21.0, "cooling_sp_c": 900.0}
    )["error"]
    assert "must be one of" in registry.dispatch(
        "set_control_policy",
        {
            "strategy": "freeze_everyone",
            "heating_sp_c": 21.0,
            "cooling_sp_c": 25.0,
            "lighting_level": 0.5,
            "ventilation_ach": 1.0,
            "rationale": "x",
        },
    )["error"]


def test_recent_telemetry_is_compacted_not_dumped(registry):
    result = registry.dispatch("get_recent_telemetry", {"window_steps": 96})

    assert "steps in" in result["summary"]
    assert len(result["summary"]) < 1200
    assert result["latest"]["step"] == 95


def test_comfort_limits_expose_the_band_and_the_hard_clamps(registry):
    result = registry.dispatch("get_comfort_limits", {})

    assert result["pmv_band"] == [-0.5, 0.5]
    assert result["hard_zone_temp_c_occupied"] == [19.0, 26.0]
    assert result["min_deadband_c"] == 1.5
    coolest, warmest = result["comfortable_zone_temp_c"]
    assert 15.0 < coolest < warmest < 32.0


def test_the_energy_summary_accumulates_from_observed_telemetry(registry):
    """Derived in the context, so no tool needs a handle on the database."""
    result = registry.dispatch("get_energy_summary", {})

    assert result["total_kwh"] > 0.0
    assert result["peak_kw"] > 0.0
    assert result["peak_demand_threshold_kw"] == 10.0
    assert result["cost_so_far"] == pytest.approx(result["total_kwh"] * 0.18, rel=1e-3)


def test_evaluate_policy_rejects_a_candidate_the_guard_would_override(registry):
    """The point of the tool: catch the clamp before it has to happen."""
    bad = registry.dispatch(
        "evaluate_policy", {"heating_sp_c": 24.0, "cooling_sp_c": 25.0}
    )

    assert bad["would_be_clamped"] is True
    assert any("dead-band" in problem for problem in bad["problems"])

    frigid = registry.dispatch(
        "evaluate_policy", {"heating_sp_c": 13.0, "cooling_sp_c": 18.0}
    )
    assert frigid["would_be_clamped"] is True
    assert frigid["comfort_satisfied_when_occupied"] is False


def test_evaluate_policy_approves_a_wide_comfortable_band(registry):
    good = registry.dispatch(
        "evaluate_policy",
        {
            "heating_sp_c": 21.0,
            "cooling_sp_c": 25.5,
            "lighting_level": 0.5,
            "ventilation_ach": 1.0,
        },
    )

    assert good["would_be_clamped"] is False
    assert good["problems"] == []
    assert good["pmv_at_heating_sp"] < good["pmv_at_cooling_sp"]


def test_simulation_errors_are_severity_filtered_and_deduplicated(registry):
    """The tool that lets the agent react to runtime faults on its own."""
    assert "No simulation diagnostics" in registry.dispatch(
        "get_simulation_errors", {}
    )["digest"]

    for _ in range(50):
        registry.context.record_error("   ** Warning ** meter does not exist")
    registry.context.record_error("   ** Severe  ** Node connection error")

    result = registry.dispatch("get_simulation_errors", {"min_severity": "warning"})

    assert result["raw_line_count"] == 51
    assert "[severe x1]" in result["digest"]
    assert "[warning x50]" in result["digest"]


def test_set_control_policy_is_inert(registry):
    """Dispatching it moves nothing; the supervisor reads the arguments instead."""
    result = registry.dispatch("set_control_policy", dict(VALID_POLICY))

    assert result["accepted_for_validation"] is True
    assert result["heating_sp_c"] == 21.0


def test_tools_refuse_to_answer_before_the_run_has_started():
    """No telemetry means no approved information to expose."""
    empty = ToolRegistry(AgentContext(limits=dict(LIMITS)))

    assert "no telemetry observed yet" in empty.dispatch("get_comfort_limits", {})["error"]


def test_a_duplicate_or_malformed_tool_registration_is_refused(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register("get_comfort_limits", {"type": "object", "properties": {}}, lambda: {})
    with pytest.raises(ValueError, match="object schema with properties"):
        registry.register("new_tool", {"type": "string"}, lambda: {})


# -- the supervisor: policy validation --------------------------------------


def test_a_valid_policy_is_adopted_and_reaches_the_building_through_the_guard():
    """The headline path, and the one D3 depends on."""
    supervisor, transport = build_supervisor([reply("", policy_call())])
    state = build_state(step=96)

    decision = supervisor.decide(state, build_history(96))

    assert decision.fallback_used is False
    assert decision.policy.strategy == "hold"
    assert decision.policy.heating_sp_c == 21.0
    assert decision.action.heating_sp_c == 21.0     # inside the limits, so unclamped
    assert decision.rationale.startswith("Widened the dead-band")
    assert decision.latency_ms is not None
    assert decision.prompt_tokens == 900
    assert decision.retries == 0
    assert len(transport.requests) == 1


def test_the_validity_window_is_set_by_the_supervisor_not_the_model():
    """A model that chose its own expiry could grant itself an endless policy."""
    supervisor, _ = build_supervisor([reply("", policy_call())], cadence_steps=4)

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.policy.expires_at_step == 100


def test_an_invented_expiry_argument_is_refused_then_repaired():
    """Schema enforcement and self-correction, on one round trip each."""
    supervisor, transport = build_supervisor(
        [
            reply("", policy_call(expires_at_step=999_999)),
            reply("", policy_call()),
        ]
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.retries == 1
    assert decision.fallback_used is False
    assert decision.policy.expires_at_step == 100
    correction = transport.requests[1]["messages"][-1]["content"]
    assert "unknown argument(s) expires_at_step" in correction


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"cooling_sp_c": 21.5}, "at least 1.50 C above"),
        ({"heating_sp_c": 5.0, "cooling_sp_c": 9.0}, "outside the safety envelope"),
        ({"cooling_sp_c": 45.0}, "outside the safety envelope"),
        ({"cooling_sp_c": 100.0}, "must be at most"),   # caught by the tool schema
        ({"lighting_level": 4.0}, "must be at most"),
        ({"ventilation_ach": 19.0}, "between 0.0 and 6.00"),
        ({"strategy": "vibes"}, "must be one of"),
        ({"rationale": "   "}, "rationale must not be empty"),
    ],
)
def test_an_invalid_policy_is_rejected_with_the_reason_named(overrides, expected):
    """Every rejection has to be specific enough for the model to repair from."""
    supervisor, transport = build_supervisor(
        [reply("", policy_call(**overrides)), reply("", policy_call())], max_retries=1
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.retries == 1
    assert decision.fallback_used is False
    assert expected in transport.requests[1]["messages"][-1]["content"]


def test_a_missing_field_is_reported_by_name():
    incomplete = dict(VALID_POLICY)
    del incomplete["ventilation_ach"]
    supervisor, transport = build_supervisor(
        [
            reply("", (("set_control_policy", json.dumps(incomplete)),)),
            reply("", policy_call()),
        ]
    )

    supervisor.decide(build_state(step=96), build_history(96))

    assert "ventilation_ach" in transport.requests[1]["messages"][-1]["content"]


def test_self_correction_is_bounded_and_then_falls_back():
    """Bounded repair, then a controlled degradation — never an aborted run."""
    supervisor, transport = build_supervisor(
        [reply("", policy_call(cooling_sp_c=21.2))] * 3, max_retries=1
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is True
    assert "Supervisor unavailable" in decision.rationale
    assert "policy still invalid after 1 self-correction" in decision.rationale
    assert len(transport.requests) == 2  # the original attempt plus one repair


def test_malformed_tool_call_json_is_repaired_rather_than_fatal():
    """The most common real failure mode of a tool-calling model."""
    supervisor, transport = build_supervisor(
        [
            reply("", (("set_control_policy", '{"strategy": "hold", "heating'),)),
            reply("", policy_call()),
        ]
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is False
    assert decision.retries == 1
    assert "not valid JSON" in transport.requests[1]["messages"][-1]["content"]


def test_a_policy_written_as_prose_is_recovered_and_still_validated():
    """Right answer, wrong envelope — recovered, but not exempted from checks."""
    supervisor, _ = build_supervisor(
        [reply(f"Here is the policy:\n```json\n{json.dumps(VALID_POLICY)}\n```")]
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is False
    assert decision.policy.heating_sp_c == 21.0
    assert decision.tool_calls[0]["source"] == "recovered_from_message_content"


def test_a_reply_with_neither_a_tool_call_nor_json_is_corrected():
    supervisor, transport = build_supervisor(
        [reply("I would suggest keeping things comfortable."), reply("", policy_call())]
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is False
    assert "no tool call" in transport.requests[1]["messages"][-1]["content"]


# -- the supervisor: cadence, tools and the guard ---------------------------


def test_the_model_is_consulted_only_on_the_cadence():
    """The whole latency argument: the LLM is off the per-timestep path."""
    supervisor, transport = build_supervisor(
        default=reply("", policy_call()), cadence_steps=4
    )
    history = build_history(16)

    for step in range(16):
        supervisor.decide(build_state(step=step), history[: step + 1])

    assert len(transport.requests) == 4  # steps 0, 4, 8, 12


def test_between_supervisory_calls_the_guard_enforces_the_active_policy():
    """The fast tier keeps working on the steps the model never sees."""
    supervisor, transport = build_supervisor([reply("", policy_call())], cadence_steps=4)
    history = build_history(4)

    supervisor.decide(build_state(step=0), history)
    held = supervisor.decide(build_state(step=1), history)

    assert len(transport.requests) == 1
    assert held.policy.heating_sp_c == 21.0
    assert held.latency_ms is None       # no model call was made on this step


def test_read_tools_are_dispatched_before_the_policy_is_committed():
    """The tool-calling loop, including the tool results fed back."""
    supervisor, transport = build_supervisor(
        [
            reply("", (("get_comfort_limits", "{}"), ("get_energy_summary", "{}"))),
            reply("", (("evaluate_policy", '{"heating_sp_c": 21, "cooling_sp_c": 25.5}'),)),
            reply("", policy_call()),
        ]
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert [call["name"] for call in decision.tool_calls] == [
        "get_comfort_limits",
        "get_energy_summary",
        "evaluate_policy",
        "set_control_policy",
    ]
    assert all("error" not in call["result"] for call in decision.tool_calls)
    # every tool call was answered, in order, before the next request went out
    roles = [message["role"] for message in transport.requests[2]["messages"]]
    assert roles.count("tool") == 3


def test_a_model_that_never_decides_is_stopped_by_the_iteration_ceiling():
    """Bounds worst-case latency even when the model loops on read tools."""
    supervisor, transport = build_supervisor(
        [reply("", (("get_comfort_limits", "{}"),))] * 10, max_tool_iterations=3
    )

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is True
    assert "no valid policy after 3 tool iteration(s)" in decision.rationale
    assert len(transport.requests) == 3


def test_an_unsafe_llm_policy_is_clamped_by_the_guard_and_reported():
    """D3, the single most important safety property in the design."""
    supervisor, _ = build_supervisor(
        [
            reply(
                "",
                policy_call(
                    heating_sp_c=30.0,
                    cooling_sp_c=32.0,
                    lighting_level=1.0,
                    ventilation_ach=6.0,
                    rationale="Bake the occupants to save on cooling.",
                ),
            )
        ]
    )
    state = build_state(step=96, occupancy=1.0)

    decision = supervisor.decide(state, build_history(96))

    assert decision.guard_clamped is True
    assert decision.action.cooling_sp_c <= 26.0
    assert decision.action.heating_sp_c <= 26.0
    assert decision.action.cooling_sp_c - decision.action.heating_sp_c >= 1.5
    assert "clamped" in decision.rationale
    # the policy the model asked for is still recorded, unlaundered
    assert decision.policy.heating_sp_c == 30.0


def test_the_guard_still_clamps_on_the_fallback_path():
    """There is exactly one route from any policy to the building."""
    supervisor, _ = build_supervisor([APIConnectionError(request=REQUEST)])
    # 07:00 on a weekday: the schedule is still in setback but occupancy is due,
    # so the guard has to lift the baseline's 16 C heating set-point.
    state = build_state(step=28, sim_time=build_state().sim_time.replace(hour=7),
                        occupancy=0.0, zone_temp_c=18.0)

    decision = supervisor.decide(state, build_history(28))

    assert decision.fallback_used is True
    assert decision.guard_clamped is True
    assert decision.action.heating_sp_c >= 19.0


def test_a_transport_failure_degrades_the_run_instead_of_ending_it():
    """D5: fallback is a designed path, and the flag keeps results honest."""
    supervisor, _ = build_supervisor([APITimeoutError(request=REQUEST)])

    decision = supervisor.decide(build_state(step=96), build_history(96))

    assert decision.fallback_used is True
    assert "APITimeoutError" in decision.rationale
    assert decision.action.heating_sp_c > 0.0


def test_the_model_is_abandoned_after_repeated_failures():
    """A dead key must not cost the full timeout on every cadence for a year."""
    supervisor, transport = build_supervisor(
        [APIConnectionError(request=REQUEST)] * 10,
        cadence_steps=4,
        max_consecutive_failures=2,
    )
    history = build_history(16)

    decisions = [
        supervisor.decide(build_state(step=step), history[: step + 1])
        for step in (0, 4, 8, 12)
    ]

    assert len(transport.requests) == 2
    assert all(decision.fallback_used for decision in decisions)
    assert "model abandoned" in decisions[-1].rationale


def test_a_recovered_model_resets_the_failure_count():
    """One bad hour must not permanently disable the supervisor."""
    supervisor, _ = build_supervisor(
        [APIConnectionError(request=REQUEST), reply("", policy_call())],
        cadence_steps=4,
        max_consecutive_failures=3,
    )
    history = build_history(8)

    first = supervisor.decide(build_state(step=0), history)
    second = supervisor.decide(build_state(step=4), history)

    assert first.fallback_used is True
    assert second.fallback_used is False


def test_reset_clears_supervisor_state_between_runs():
    """Two runs of one configuration have to agree; leaked state would break that."""
    supervisor, _ = build_supervisor([reply("", policy_call())])
    supervisor.decide(build_state(step=96), build_history(96))

    supervisor.reset()

    assert len(supervisor.reasoning_log) == 0
    assert supervisor._tools.context.total_kwh == 0.0  # noqa: SLF001
    assert supervisor._tools.context.state is None     # noqa: SLF001


def test_construction_arguments_are_validated():
    for kwargs, expected in (
        ({"cadence_steps": 0}, "cadence_steps must be positive"),
        ({"max_tool_iterations": 0}, "max_tool_iterations must be positive"),
        ({"max_retries": -1}, "max_retries must be non-negative"),
    ):
        with pytest.raises(ValueError, match=expected):
            build_supervisor(**kwargs)


# -- structured reasoning logs ---------------------------------------------


def test_every_decision_produces_a_complete_structured_log_record(caplog):
    """The logging requirement, field by field."""
    supervisor, _ = build_supervisor([reply("deciding now", policy_call())])

    with caplog.at_level(logging.INFO, logger="ecoloop.agent"):
        supervisor.decide(build_state(step=96), build_history(96))

    record = supervisor.reasoning_log[-1]
    for field in (
        "timestamp",
        "step",
        "sim_time",
        "prompt_version",
        "model",
        "inputs",
        "response",
        "tool_calls",
        "parsed_policy",
        "execution_time_ms",
        "validation_status",
        "retries",
        "prompt_tokens",
        "completion_tokens",
        "fallback_used",
        "guard_clamped",
    ):
        assert field in record, field

    assert record["prompt_version"] == PROMPT_VERSION
    assert record["model"] == MODEL
    assert record["validation_status"] == "valid"
    assert record["parsed_policy"]["heating_sp_c"] == 21.0
    assert record["inputs"]["pmv"] == pytest.approx(-0.30, abs=0.05)
    assert record["response"] == "deciding now"

    # emitted as one JSON document per decision, so the log can be queried
    emitted = json.loads(caplog.records[-1].getMessage())
    assert emitted["step"] == 96


def test_a_repaired_decision_records_how_many_retries_it_took():
    supervisor, _ = build_supervisor(
        [reply("", policy_call(cooling_sp_c=21.4)), reply("", policy_call())]
    )

    supervisor.decide(build_state(step=96), build_history(96))

    assert supervisor.reasoning_log[-1]["validation_status"] == "valid after 1 retry(ies)"


def test_a_failed_decision_is_logged_with_its_validation_status():
    supervisor, _ = build_supervisor([APITimeoutError(request=REQUEST)])

    supervisor.decide(build_state(step=96), build_history(96))

    record = supervisor.reasoning_log[-1]
    assert record["fallback_used"] is True
    assert record["validation_status"].startswith("failed: LLMTransportError")
    assert record["parsed_policy"] is None


# -- end to end -------------------------------------------------------------


def test_the_supervisor_drives_a_full_closed_loop_and_beats_the_baseline(
    tmp_path, monkeypatch
):
    """Milestone 2's actual claim, through the real loop, database and physics.

    The transport is scripted, but nothing else is: the runner, the guard, the
    RC model, the comfort model and the persistence layer are all the real ones,
    and the policy that moves the set-points comes out of the model's tool call.
    """
    import dataclasses
    from datetime import datetime

    from app.config import Scenario, ScenarioTargets, Settings
    from app.loop import ClosedLoopRunner, build_controller, build_simulator

    horizon = 192
    settings = dataclasses.replace(
        Settings(), database_path=tmp_path / "m2.db", decision_cadence_steps=4
    )
    scenario = Scenario(
        id="test_summer",
        label="Test summer",
        start=datetime(2024, 7, 15),
        days=2,
        timestep_seconds=900,
        weather={"mean_temp_c": 28.0, "daily_swing_c": 9.0, "peak_solar_w_m2": 850.0},
        occupied_hours=(8, 18),
        targets=ScenarioTargets(),
        building={"initial_zone_temp_c": 26.0},
    )

    monkeypatch.setattr(
        "app.loop.LLMClient",
        lambda **kwargs: LLMClient(
            model=MODEL,
            client=FakeTransport(default=CompetentSupervisorModel()),
            retries=0,
        ),
    )

    summaries = {}
    for controller in ("baseline", "llm"):
        conn = db.connect(settings.database_path)
        try:
            run_id = db.create_run(
                conn,
                label=f"m2/{controller}",
                controller=controller,
                simulator="toy",
                scenario=scenario.id,
                model=MODEL if controller == "llm" else None,
                horizon_steps=horizon,
                timestep_seconds=900,
                started_at="2024-01-01T00:00:00+00:00",
            )
        finally:
            conn.close()
        summaries[controller] = (
            run_id,
            ClosedLoopRunner(
                run_id=run_id,
                simulator=build_simulator(scenario, "toy", horizon),
                controller=build_controller(controller, scenario, settings),
                settings=settings,
            ).run(),
        )

    baseline_summary = summaries["baseline"][1]
    llm_run_id, llm_summary = summaries["llm"]

    assert llm_summary.total_kwh < baseline_summary.total_kwh
    assert llm_summary.comfort_violations <= baseline_summary.comfort_violations

    conn = db.connect(settings.database_path)
    try:
        record = db.get_run(conn, llm_run_id)
        rows = db.get_timeseries(conn, llm_run_id)
        decisions = db.get_decisions(conn, llm_run_id)
    finally:
        conn.close()

    assert record["status"] == "complete"
    assert record["model"] == MODEL
    assert len(rows) == horizon
    assert all(row["heating_sp_c"] <= row["cooling_sp_c"] for row in rows)

    supervised = [row for row in decisions if row["latency_ms"] is not None]
    assert supervised, "no supervisory decisions were persisted"
    assert all(row["rationale"] for row in supervised)
    assert all(row["tool_calls"] for row in supervised)
    assert not any(row["fallback_used"] for row in supervised)
    assert {row["strategy"] for row in decisions} >= {"hold", "setback"}
