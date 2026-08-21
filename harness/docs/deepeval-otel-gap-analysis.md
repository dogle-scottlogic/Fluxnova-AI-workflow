# DeepEval Data Requirements vs. OTEL Coverage — Gap Analysis

> **Note (post-refactor):** this analysis was written while OTel ingestion (`otel_client`/`otel_receiver`)
> and the harness's own workflow runner lived inside `harness/src/fluxnova/`. That code has since moved
> into standalone `fluxnova-listener`/`fluxnova-runner` packages (see the root `README.md` for the current
> architecture) — module paths below (e.g. `fluxnova.otel_receiver`, `fluxnova.bpmn`) now correspond to
> `fluxnova_listener.otel_receiver`, `fluxnova_listener.bpmn`, etc. The findings/rationale are unchanged.

## Purpose

The harness currently drives DeepEval (`harness/src/deep_eval/main.py`) from a single JSON
report fetched via `Client.get_agent_history()`
(`GET /agent-history/process/{instanceId}/subprocess/{subprocessId}`), backed by the
`ACT_HI_AGENT_*` history tables in `agentic-subprocess`.

The `agentic-subprocess` plugin also emits OTEL GenAI-semconv metrics and traces (see
[`GENAI_SEMCONV_ALIGNMENT.md`](../../../fluxnova-plugins/agentic-subprocess/docs/observability/GENAI_SEMCONV_ALIGNMENT.md)
in the plugin repo). This document maps every field the DeepEval suite consumes back to its
source in the agent-history report, and checks whether that same data is already available as an
OTEL span attribute or metric — to assess whether the REST call could be replaced by an
OTLP-backend query instead.

Sources reviewed:
- `harness/src/deep_eval/main.py` + `conftest.py` (what fields are actually read)
- `harness/.fluxnova/loanAssessmentProcess/<id>.json` (shape of a real report)
- `agent-history/.../otel/AgentOtelTracing.java` + `AgentOtelMetrics.java` (what's actually emitted)
- `docs/observability/GENAI_SEMCONV_ALIGNMENT.md` (spec alignment + known gaps)
- `bpmn/loan-assesment.bpmn` (deploy-time static config — added in this pass)
- `harness/config/loan-assesment.yml` + `fluxnova/config.py` (harness-side static config, compared against the BPMN)

## Legend

- ✅ **Available** — same data (or a directly equivalent form) already exists in a trace/metric,
  or is retrievable from an existing core Fluxnova engine REST API (not just the plugin's
  `/agent-history` endpoint).
- 📄 **Static (BPMN)** — not runtime data at all; it's deploy-time config baked into the BPMN
  definition and can be read directly from the `.bpmn` file, or via the engine's own
  `GET process-definition/{id}/xml` REST API, with no OTEL/agent-history call needed.
- ⚠️ **Partial** — a related signal exists, but it's approximate, lossy, or only usable as a proxy.
- ❌ **Gap** — not emitted to OTEL today, not present in the BPMN, and not reconstructable from any
  existing core engine REST API either; would require plugin/BPMN changes to close.
- 🔜 **Pending (plugin work in progress)** — a genuine gap today, but content-capture instrumentation
  for `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, and
  `gen_ai.tool.definitions` is being added to `AgentOtelTracing`/`AgentOtelMetrics` in the
  `agentic-subprocess` plugin (per the spec's opt-in content-capture attributes) — this status
  marks fields expected to flip to ✅ once that work lands, so this isn't a stale/permanent gap.

## Field-by-field breakdown

| DeepEval field (source)                                  | Used by                                                                                                 | OTEL equivalent today                                                                 | Status | Notes |
|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|--------|-------|
| `goal` (system prompt / agent instructions)                | `test_decision_quality`, `test_evidence_citation`, `test_output_matches_golden`, `test_tool_correctness` (as `input`) | `agent:config@systemPrompt` in the BPMN `AdHocSubProcess_LoanAssessmentAgent` extension elements; `gen_ai.system_instructions` also **being added** to `AgentOtelTracing` | 📄 Static (BPMN) | Not a runtime signal at all — it's a fixed string authored at design time and deployed with the process, readable directly from the `.bpmn` file. The in-progress plugin update will also emit it as `gen_ai.system_instructions` on the `invoke_agent` span, giving a second (OTEL-native) route to the same static value — doesn't change the status, since the BPMN route already closes this. |
| `finalOutput` (agent's final text response)                | Nearly every test — `actual_output` for `ToolCorrectnessMetric` and all `GEval` metrics                   | `gen_ai.output.messages` — **being added** to `AgentOtelTracing` (opt-in content capture)  | 🔜 Pending | This *is* genuine runtime output (the LLM's generated text) — nothing in the BPMN or core engine APIs can substitute for it. Was the single biggest blocker; tracked here as pending now that the plugin update to emit `gen_ai.output.messages` on the `chat`/`invoke_agent` spans is underway. Once emitted, this closes to ✅ for every test that reads `finalOutput`. |
| `inputVariables` — **variable names/schema** (which variables exist: `customerId`, `applicantType`, `hasCollateral`, `requestedAmount`, etc.) | Schema used by all the value-dependent tests below | `agent:context` variable list in the same BPMN extension element                       | 📄 Static (BPMN) | The full list of context variable *names* the agent subprocess uses is declared in the BPMN (`agent:variable name="..."` per variable) — this is fixed at design time and doesn't need a runtime signal to discover. |
| `inputVariables` — **actual runtime values** (`customerId=C001`, `hasCollateral=false`, `requestedAmount=50000`, etc.) | `test_tool_argument_correctness`, `test_no_collateral_check_when_no_collateral`, `test_no_bank_statement_for_employed_applicant`, `test_requested_amount_passed_to_affordability`, golden-matching | ✅ Core engine `history/variable-instance` REST API — already called by the harness (`Client.get_variables()`) | ✅ Available | Not a GenAI/OTEL concept, but not a gap either: these are ordinary Camunda process variables, and the harness *already has a working method* for fetching them, independently of `/agent-history`. |
| `iterations` (agent loop count)                             | `test_step_efficiency`                                                                                    | ✅ `gen_ai.invoke_agent.inference_calls` metric — now records the authoritative `AgentSubprocessHistoryEvent.iterationCount` (the orchestrator's real `_agentLoopIndex` counter) instead of a derived chat-call count | ✅ Available | **Fixed.** Previously this metric double-counted as a proxy — it summed observed `chat` operations per invocation and *assumed* 1 chat call per loop, with no way to detect if that assumption ever broke. `AgentOtelMetrics.recordSubprocess()` has been updated to record `event.getIterationCount()` directly, the same exact value the plugin already tracks internally and persists to `ACT_HI_AGENT_SUBPROCESS.ITERATION_COUNT_`/`/agent-history`'s `iterations` field. This is now an exact match, not an approximation, and needs no BPMN/trace-structure cross-referencing. (Counting `chat` child spans under `invoke_agent` remains available too, as a structurally-verifiable cross-check, but is no longer necessary.) |
| `model` / `provider`                                        | Indirectly (model config for metrics), used in report metadata                                            | ✅ `gen_ai.request.model` / `gen_ai.response.model` / `gen_ai.provider.name`             | ✅ Available | Exact match on both `invoke_agent` and `chat` spans. |
| `totalPromptTokens` / `totalCompletionTokens`                | Not asserted on directly today, but useful context/cost metric                                             | ✅ `gen_ai.client.token.usage` metric, and `gen_ai.usage.input_tokens`/`output_tokens` span attrs | ✅ Available | Exact match, recorded per-call and accumulated per-invocation. |
| `startTime` / `endTime` / `executionTime`                    | Not asserted on directly, informational                                                                    | ✅ Span start/end timestamps; `gen_ai.invoke_agent.duration` / `gen_ai.client.operation.duration` metrics | ✅ Available | Exact match. |
| `processInstanceId` / `subprocessExecutionId`                | Used as the report lookup key, not a test assertion                                                        | ✅ `gen_ai.conversation.id` (= process instance id) on `invoke_agent` span                | ✅ Available | `subprocessExecutionId` itself isn't an attribute, but `conversation.id` is sufficient to correlate a trace back to a run. |
| `toolCalls[].toolName`                                       | `test_no_collateral_check_when_no_collateral`, `test_no_bank_statement_for_employed_applicant`, `test_tool_correctness`, tool-correctness metrics | ✅ `gen_ai.tool.name` on `execute_tool` span, cross-referenced against the BPMN's static `serviceTask id → name` map if needed | ✅ Available | The span attribute is set via `firstNonBlank(toolName, toolElementId)`, so on its own it's ambiguous about which one you got — but the BPMN gives a complete, unique `elementId ↔ display name` mapping for every service task, so whichever value lands in the attribute can be deterministically resolved to both forms. |
| `toolCalls[].toolElementId`                                   | `test_requested_amount_passed_to_affordability` (matches on element id, not name)                          | Same as above — resolvable via the BPMN's static id/name map, given either value from the `execute_tool` span | ✅ Available | Previously flagged as lossy; the BPMN cross-reference removes the ambiguity as long as *one* of name/id makes it into the span (which it always does today). |
| `toolCalls[].toolCallId`                                      | Correlation only, not asserted on                                                                          | ✅ `gen_ai.tool.call.id`                                                                 | ✅ Available | Exact match. |
| `toolCalls[].toolInput` — **parameter names/schema** (e.g. `Check Credit Score` takes `customerId`; `Assess Affordability` takes `customerId` + `requestedAmount`) | Structural expectation checked by `test_tool_argument_correctness`, `test_requested_amount_passed_to_affordability` | `camunda:inputParameter` list per `serviceTask` in the BPMN                              | 📄 Static (BPMN) | Which variables *should* be passed to which tool is fully declared in the BPMN (`camunda:inputOutput` on each `serviceTask`) — this is currently hand-duplicated in the DeepEval test code (`test_tool_argument_correctness`'s hardcoded `expected` list) and could be generated from the BPMN instead. |
| `toolCalls[].toolInput` — **actual bound values** (e.g. `customerId="C001"`, `requestedAmount=50000` for *this* run)         | `test_tool_argument_correctness`, `test_requested_amount_passed_to_affordability`                          | ✅ Reconstructable: BPMN gives the parameter *names* per tool (above); `history/variable-instance` gives the actual bound *values*, each tagged with the `activityInstanceId` that last set it | ✅ Available | Every `camunda:inputParameter` in this BPMN is a direct `${variableName}` reference (no expressions/transforms), and those variables (`customerId`, `requestedAmount`, …) don't change value across the run — so joining the static BPMN parameter map with `get_variables()` reconstructs exact tool-call inputs without any content-capture instrumentation. Would need the richer `history/detail` API instead if a variable were overwritten multiple times before different tool calls in the same run. |
| `toolCalls[].toolOutput`                                      | Not asserted on today, but present in the report                                                            | ✅ Same `history/variable-instance` API — every tool's `camunda:outputParameter`s (`creditScore`, `fraudRiskScore`, `employmentVerified`, `bankStatementScore`, `collateralValue`, `debtToIncomeRatio`, `affordabilityPassed`, …) are ordinary process variables | ✅ Available | `HistoricVariableInstanceDto` includes `activityInstanceId`, so each output value is already attributable to the service task that produced it — no plugin/OTEL involvement needed. |
| `toolCalls[].status` (`COMPLETED`/`FAILED`)                   | `test_all_tool_calls_completed`                                                                             | ✅ Span `StatusCode.OK`/`StatusCode.ERROR` + `error.type=tool_error` on failure           | ✅ Available | Exact equivalent — failed tool calls set `ERROR` status and `error.type`. |
| `toolCalls[].durationMs`                                      | Not asserted on today                                                                                       | ✅ `gen_ai.execute_tool.duration` metric, span duration                                  | ✅ Available | Exact match. |
| `step-history` (full prompt/response transcript per loop)     | Not read by any current DeepEval test (available in report, unused)                                        | `gen_ai.input.messages` / `gen_ai.output.messages` — **being added** to `AgentOtelTracing`; span start/end timestamps and loop-adjacent `chat` spans already give the structural shape | 🔜 Pending (content) / ✅ (structure) | Structural shape (loop boundaries, call sequence) is already reconstructable from span parent/child + timestamps. The message *content* is tracked here as pending: once `gen_ai.input.messages`/`gen_ai.output.messages` are emitted per `chat` span, the full transcript becomes reconstructable from spans too, closing this fully to ✅. |
| Golden dataset matching keys (`applicantType`, `hasCollateral` **values**) | `_match_golden()` — used to select the right golden scenario                                                | ✅ Same `history/variable-instance` API as `inputVariables` values, above                  | ✅ Available | Duplicate of the `inputVariables` values row; called out separately since it drives dataset selection, not just an assertion. |

## Data already derivable directly from the BPMN (no runtime signal needed)

Cross-referencing `bpmn/loan-assesment.bpmn` against the harness config and DeepEval code
surfaced a second, orthogonal finding: several fields currently sourced from the runtime
agent-history report (or hand-duplicated in `harness/config/loan-assesment.yml`) are actually
**static deploy-time config**, not runtime output at all, and can be read straight from the BPMN:

| Data                                                              | Where it lives in the BPMN                                                                  | Currently duplicated where?                                                                 |
|--------------------------------------------------------------------|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `goal` / system prompt text                                        | `agent:config@systemPrompt` attribute on `AdHocSubProcess_LoanAssessmentAgent`                 | Echoed verbatim into every agent-history report                                                |
| Context variable names (`customerId`, `applicantType`, `creditScore`, …) | `agent:context` → `agent:variable name="..."` list                                        | Not currently duplicated elsewhere, but the report's `inputVariables` keys always match this list |
| Service task element ID ↔ display name (`ServiceTask_FraudScreening` ↔ `Run Fraud Screening`) | `serviceTask id="..." name="..."` per task                                | Hand-duplicated in `available_tools:` in `harness/config/loan-assesment.yml`                    |
| Expected input parameter names per tool (`customerId`, `applicationId`, `requestedAmount`) | `camunda:inputOutput` → `camunda:inputParameter` per `serviceTask`                | Hand-duplicated as a hardcoded `expected` list inside `test_tool_argument_correctness`           |
| Model/provider defaults (`llama3.1` / `ollama`)                     | `agent:config@model` / `@provider`                                                             | Also present in the runtime report (`model`/`provider` fields) — BPMN gives the *configured* default; the report confirms what actually ran |

**Caveat:** the conditional tool-selection *logic* (EMPLOYED → Verify Employment, else → Analyse
Bank Statements; hasCollateral → Value Collateral) only exists in the BPMN as unstructured prose
inside `systemPrompt` — it is **not** machine-parseable from the BPMN. The structured, executable
version of that same logic lives in `expected_tools:` in the YAML config (`ExpectedToolRule` +
its `if` expressions), which remains the source of truth for automated assertions; the BPMN prose
and the YAML rules are two independent encodings of the same intent today, one for the LLM and one
for the test harness.

## Gaps closed by existing core Fluxnova engine REST APIs (`fluxnova-bpm-platform`)

The previous passes only looked at the `agentic-subprocess` plugin's own `/agent-history` endpoint
and OTEL output. `fluxnova-bpm-platform` (`engine-rest`) exposes a much broader set of *generic*
process-engine history APIs that have nothing to do with the AI plugin — reviewing them closes
several of the value-level gaps above without any new instrumentation at all.

### `history/variable-instance` — already wired up, closes more than expected

`Client.get_variables()` already calls this endpoint (for `wait_for_completion()`'s return value),
but the harness has never used it to source DeepEval's `inputVariables` or `toolCalls[].toolInput`
/`toolOutput`. Two things make it more useful than first assumed:

1. Every variable the BPMN's service tasks read or write (`customerId`, `requestedAmount`,
   `hasCollateral`, `creditScore`, `fraudRiskScore`, `employmentVerified`, `annualIncome`,
   `bankStatementScore`, `collateralValue`, `debtToIncomeRatio`, `affordabilityPassed`, …) is an
   ordinary Camunda process variable — there's nothing agent- or LLM-specific about them, so this
   generic history API returns all of them for free, with no dependency on the AI plugin.
2. `HistoricVariableInstanceDto` includes `activityInstanceId` — the id of the activity that last
   set the variable — so each value is already attributable to the specific service task
   (tool call) that produced it, joinable against `history/activity-instance` for the activity id
   → element id mapping if needed.

Combined with the BPMN's static `camunda:inputParameter`/`camunda:outputParameter` map (see
above), this closes the `inputVariables` values row and the `toolCalls[].toolInput`/`toolOutput`
value rows in the table above — **using an API the harness already calls**, not a new one.
(Caveat: this only holds cleanly because none of these variables are overwritten multiple times
before different tool calls within a single run; if that changed, `history/detail` — see below —
would be needed to disambiguate *which* variable update corresponds to *which* tool call.)

### `history/detail` — a finer-grained alternative, not currently needed

`HistoricDetailResourceImpl`/`HistoricVariableUpdateDto` expose the full variable *update history*
(every set, not just the latest value), each tagged with `activityInstanceId` and `time`. This is
strictly more powerful than `history/variable-instance` — useful if a variable is set more than
once per run (e.g. a retried tool, or a variable reused across loop iterations) and you need to
pin an exact value to an exact tool-call timestamp rather than just "the last value written."
Not needed for the current loan-assessment BPMN, but worth knowing about if the process grows more
complex.

### `process-definition/{id}/xml` — an API-based alternative to parsing the local BPMN file

`ProcessDefinitionResource` exposes `GET /process-definition/{id}/xml`, returning the deployed
BPMN's raw XML. Everything in the "derivable directly from the BPMN" section above (`goal`,
service-task id↔name map, expected input parameter names) can therefore also be fetched from a
running engine by `processDefinitionId` — useful if the harness ever needs to run against a
process it doesn't have local `.bpmn` file access to (e.g. evaluating a deployment made by someone
else), rather than requiring `bpmn_path` to be resolvable on disk.

### `history/job-log` + incidents — narrows (doesn't fully close) a *different*, plugin-documented gap

`GENAI_SEMCONV_ALIGNMENT.md`'s "Known gaps" section flags that `invoke_agent`/`chat` spans always
end `StatusCode.OK`, even if the underlying LLM call job ultimately fails, because neither history
event carries a status/error field. `HistoricJobLogResourceImpl` (`HistoricJobLogDto`) exposes
`jobExceptionMessage`, `failureLog`/`successLog` flags, `activityId`, and retry counts per
async-continuation job; unresolved failures also surface via the incident REST API. This doesn't
retrofit the *span* itself with error status, but it does mean job-level failure information for
the ad-hoc subprocess's execution is independently obtainable from the engine today — a
DeepEval-adjacent health check (e.g. "did this run hit an unhandled engine-level error") could be
built on `history/job-log` without waiting on the plugin's own known-gap fix.

**What core APIs still cannot supply:** `finalOutput` (the LLM's generated summary text) and the
full `step-history` transcript are not persisted as Camunda process variables anywhere in this
BPMN — they only exist in the plugin's own `ACT_HI_AGENT_SUBPROCESS`/`ACT_HI_AGENT_STEP` tables
(exposed via `/agent-history`). One tempting shortcut was investigated and rejected: the agent
orchestrator internally stores the in-flight conversation transcript in a local execution variable
(`_agentConversationHistory`, in `AgentStateManager`), which in principle *could* be visible via
`history/variable-instance`/`history/detail` if history level is `FULL`. This is not a viable
alternative, though: it's an internal, underscore-prefixed implementation detail of the
orchestrator's state machine, not a published/stable contract, and the plugin's own design
deliberately persists the durable audit trail to its dedicated tables instead of relying on
process variables for this reason.

**Update — plugin work now in progress:** `finalOutput` and `step-history` are being tracked as
🔜 **Pending** rather than a permanent ❌ gap, since the `agentic-subprocess` plugin is being
updated to emit the spec's opt-in content-capture attributes — `gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.system_instructions`, and `gen_ai.tool.definitions` — on
`AgentOtelTracing`'s spans. Once that lands, `gen_ai.output.messages` on the `chat`/`invoke_agent`
spans directly supplies `finalOutput`, and `gen_ai.input.messages` (combined with the
loop-adjacent `chat` span structure already available today) reconstructs the full
`step-history` transcript — closing both rows to ✅ without needing `/agent-history` at all.

## Summary

| Category                                                                 | Count |
|---------------------------------------------------------------------------|-------|
| ✅ Available today (metrics/traces, BPMN cross-reference, or core engine REST APIs) | 12    |
| 📄 Static (BPMN) — no runtime signal needed at all                        | 3     |
| 🔜 Pending (plugin content-capture work in progress)                      | 2     |
| ❌ Gap (genuinely runtime, no known path to close)                        | 0     |

**Bottom line:** after cross-referencing the BPMN and the core `fluxnova-bpm-platform` REST APIs
(not just OTEL and `/agent-history`), the real gap has narrowed substantially. The
*quantitative/structural* side of DeepEval (tokens, durations, tool status/counts, model/provider,
loop-count, tool name/id resolution) was already covered by OTEL, and `iterations` is now an
**exact** match rather than a proxy — `gen_ai.invoke_agent.inference_calls` has been fixed to
record the orchestrator's authoritative `_agentLoopIndex` counter directly (shipped). The *value*
side — `inputVariables`, tool call inputs/outputs, golden-matching keys — turns out to be fully
available too, via the plain `history/variable-instance` API the harness already calls, since none
of that data is actually LLM-specific: it's ordinary Camunda process variables written by the
BPMN's own service tasks. What remained as a genuine gap — the **LLM-generated content**,
`finalOutput` and the full `step-history` transcript — is no longer an open-ended blocker: it's
now tracked as 🔜 pending against the in-progress `AgentOtelTracing` content-capture update, with a
clear, already-scoped path to ✅ once that plugin change ships.

## What would need to change to close the remaining gaps

1. **Content capture in `AgentOtelTracing`** *(in progress)* — add (opt-in, per spec) attributes
   for `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, and
   `gen_ai.tool.definitions` to the `chat`/`invoke_agent`/`execute_tool` spans. This is the change
   that moves `finalOutput` and `step-history` from 🔜 Pending to ✅ Available; `goal` doesn't
   strictly need it (already 📄 Static via the BPMN) but gains a second, OTEL-native route once
   `gen_ai.system_instructions` is emitted too.
   - An alternative/complementary route for just the final *decision* (not the full justification
     prose) is a structured decision variable written by a downstream BPMN step — as
     `ai-steel-thread-demo`'s `approvalDecision` pattern does, and as this repo's own
     `mock_workers.generate-assessment-report.recommendedDecision` config already half-anticipates.
     This would only help `test_output_matches_golden`; `test_decision_quality`/
     `test_evidence_citation` need the actual reasoning text, so still depend on (1).
2. **Harness-side BPMN lookup** — add a small helper that parses `bpmn_path` (or calls
   `GET process-definition/{id}/xml`) for `systemPrompt`, the `serviceTask` id/name/input-parameter
   map, and `agent:context` variable names, replacing the hand-duplicated `available_tools:` config
   and the hardcoded parameter list in `test_tool_argument_correctness`.
3. **Harness-side core-API sourcing** — replace the `inputVariables`/`toolCalls[].toolInput`/
   `toolOutput` portions of `Client.get_agent_history()` with the harness's existing
   `Client.get_variables()` (`history/variable-instance`), joined against the BPMN lookup from (2)
   for the input/output parameter names per tool. `toolCalls[].toolName`/`status`/`durationMs` can
   similarly be sourced from OTEL (`gen_ai.tool.*` attributes, `gen_ai.execute_tool.duration`)
   instead of `/agent-history`.
4. **Harness-side OTLP query layer** — query whatever OTLP backend stores the traces (e.g.
   Tempo/Jaeger by `gen_ai.conversation.id`) for both the fields already available today (3) and,
   once (1) ships, `gen_ai.input.messages`/`gen_ai.output.messages` for `finalOutput`/`step-history`.

Once (1) ships alongside (2)–(4), `/agent-history` should no longer be required for any current
DeepEval test — every field it currently supplies (`goal`, `finalOutput`, `inputVariables`,
`toolCalls[].*`, `step-history`) would have an OTEL, BPMN, or core-engine-API equivalent. Until
(1) lands, tests that read `finalOutput`/reasoning text (`test_decision_quality`,
`test_evidence_citation`, `test_output_matches_golden`, `ToolCorrectnessMetric`'s `actual_output`)
still need `/agent-history`.

## Comparison with `ai-steel-thread-demo`'s approach

`C:\dev\ai-steel-thread-demo` runs the same `agentic-subprocess` plugin but layers its **own**
observability code on top instead of relying on the plugin's built-in `AgentOtelTracing`/
`AgentOtelMetrics`. It was reviewed to see whether any of its patterns close the gaps above.

**What it does differently:**
- Generic Fluxnova/BPMN spans (`fluxnova.process`, `fluxnova.activity`, `fluxnova.task`,
  `fluxnova.external-task` — see `OpenTelemetryTracingSupport`) rather than `gen_ai.*` semconv
  spans. These carry process/activity/task IDs, not GenAI-specific attributes.
- Custom Micrometer metrics (`fluxnova.llm.tokens`, `.calls`, `.duration`, `.errors` in
  `LlmTokenUsageMetrics`) covering the same ground as the plugin's `gen_ai.client.token.usage` /
  `gen_ai.client.operation.duration` — parallel implementation, not new data.
- Persists token usage **and** sanitized last-call metadata (model, provider, version, duration,
  status, `error.type`, and a sorted list of context variable *names* used — `contextKeys`) as
  regular **Camunda process variables** (`ProcessScopedLlmTokenUsageWriter`,
  `LlmCallMetadataVariables`, `LlmTokenUsageVariables`), scoped per activity id. This makes that
  metadata retrievable via the plain `history/variable-instance` REST API the harness already
  calls (`Client.get_variables()`) — an alternative delivery mechanism for data your OTEL setup
  already covers, not a new signal.
- `Mi1DataLeakagePrevention` explicitly flags `agentLastResponse` and `agentConversation*` as
  sensitive and **redacts** them for its own status API. This reinforces rather than closes the
  content-capture gap — this codebase treats raw agent output/conversation as something to mask,
  not expose.
- `Mi5AcceptanceService` runs a separate, deterministic acceptance-test suite (policy invariants
  and pure-function assertions like credit-risk banding thresholds) rather than an LLM-output
  eval — a different testing strategy from DeepEval, not an OTEL/tracing technique, so it doesn't
  bear on this gap analysis directly.

**The one pattern that does narrow a gap:** `LoanApprovalBusinessExecutionListener` +
`LoanApprovalBusinessMetrics` read structured business process variables at process completion
(`approvalDecision`, `creditScore`, `aiRiskScore`, `isFraudulent`, `isInvalid`) and emit them as
Micrometer **metric labels/values** (counters split by `decision`, summaries for the numeric
scores). This turns the final business decision into queryable telemetry instead of leaving it
embedded only in LLM prose.

Your own `harness/config/loan-assesment.yml` already anticipates this pattern —
`mock_workers.generate-assessment-report.recommendedDecision: APPROVE` — but the current
`loan-assesment.bpmn` has no downstream step that actually writes a `recommendedDecision` (or
similar) process variable from the agent's output, so nothing populates it today. If the BPMN were
extended to add a task after the ad-hoc subprocess that extracts/writes a structured decision
variable, that value could be sourced as a metric label (mirroring this demo's pattern) instead of
parsing `finalOutput` text, which would close the gap for `test_output_matches_golden`'s
APPROVE/REJECT comparison specifically. It would **not** close `test_decision_quality` or
`test_evidence_citation`, since those evaluate the *reasoning/justification* in the prose, not
just the final decision label — that still requires the actual output text, which neither repo
captures today.

**Net effect on the gap count:** no fields move from ❌ to ✅ as a direct result of this repo's
existing code. It does surface one concrete, low-effort improvement worth adopting: writing a
structured decision variable from the agent step (as the harness config already half-expects) and
sourcing it as a metric rather than free text, which would meaningfully narrow (but not eliminate)
the `finalOutput`-dependent gap.

## Migration plan: replacing `/agent-history` with OTEL + BPMN + core APIs

This section is a high-level plan for swapping the harness off `Client.get_agent_history()` and
onto the sources this document has identified (OTEL traces/metrics, static BPMN, and core
`history/variable-instance`), now that the `iterations` fix has shipped and the content-capture
plugin work is in progress. The goal is a like-for-like replacement: today's `agent_report` dict
(read from a JSON file by `conftest.py`'s `agent_report` fixture) has a fixed shape that
`main.py`'s tests depend on — the plan preserves that shape so **no test code needs to change**,
only how the dict gets built.

### Where the swap happens

Today, `harness/src/fluxnova/main.py` calls `client.get_agent_history(instance_id, subprocess_id)`
once the process completes, and writes the raw response to
`harness/.fluxnova/loanAssessmentProcess/<id>.json`. `deep_eval/conftest.py` then just loads that
file. The swap is entirely inside `fluxnova/main.py` (and a couple of new modules) — replace the
single API call with a small "report builder" that assembles the same JSON shape from four
sources instead of one:

| Report field(s)                          | New source                                                                 |
|-------------------------------------------|-----------------------------------------------------------------------------|
| `goal`                                    | Parse `bpmn_path` (or `GET process-definition/{id}/xml`) for `agent:config@systemPrompt` |
| `inputVariables` (names + values)         | BPMN `agent:context` variable names + `client.get_variables()` (`history/variable-instance`, already implemented) |
| `iterations`                              | Query the OTLP metrics backend for `gen_ai.invoke_agent.inference_calls`, filtered to this run's `gen_ai.agent.name`/correlation key |
| `toolCalls[].toolName/status/durationMs`  | Query the OTLP traces backend for `execute_tool` spans under this run's `invoke_agent` trace |
| `toolCalls[].toolInput/toolOutput`        | BPMN `camunda:inputOutput` parameter names + `client.get_variables()`, joined by `activityInstanceId` (already implemented pattern from the core-API section above) |
| `finalOutput` / `step-history` content    | `gen_ai.output.messages` / `gen_ai.input.messages` span attributes — **blocked on the in-progress plugin content-capture work** |

### Phased approach

1. **Prerequisite: pick and stand up an OTLP query path.** ✅ **Resolved.** Rather than depend on
   any specific backend's query API (MLflow, Tempo, Jaeger, ...) — which would tie the harness to
   whatever the collector happens to be configured with — the harness runs its own minimal OTLP/HTTP
   trace receiver (`fluxnova.otel_receiver`, `otel-receiver` CLI entry point), added as a second,
   additive exporter (`otlphttp/harness`) in the collector's `traces` pipeline alongside whatever
   visualisation backend is configured (see "Harness OTLP receiver" in
   `GENAI_SEMCONV_ALIGNMENT.md` for the exact collector YAML). It persists each received span as one
   JSON line to a local store file, keyed by `trace_id`. **Correlation key:** confirmed —
   `gen_ai.conversation.id` (= process instance id) is set on the `invoke_agent` span; `OtelClient`
   resolves the correlation id to a `trace_id`, then returns every span sharing that `trace_id`
   (covering `chat`/`execute_tool` children too, since they share the same trace).
2. **Add a thin `OtelClient` alongside the existing `fluxnova.client.Client`.** ✅ **First pass
   implemented** (`fluxnova/otel_client.py`, `fluxnova/otel_receiver.py`, tests in
   `tests/test_otel_client.py`/`tests/test_otel_receiver.py`). Mirrors `Client`'s style (small,
   typed methods, dataclass return types) but reads spans from the local OTLP store instead of
   calling the engine REST API: `get_invoke_agent_metrics(correlation_id)` (now reads
   `gen_ai.invoke_agent.inference_calls`/`.tool_calls` directly off the `invoke_agent` span, per the
   plugin's span-attribute addition — no child-span-counting proxy needed),
   `get_tool_call_spans(correlation_id)` (one entry per `execute_tool` child span), and
   `get_llm_messages(correlation_id)` (stubbed — raises `NotImplementedError` until the
   content-capture plugin work in step 7 ships). Verified end-to-end against a live gzip-encoded
   OTLP/HTTP export through the real receiver process.
3. **Add a small BPMN-lookup helper** (`fluxnova.bpmn` or similar) that parses the deployed BPMN
   once per config load (or fetches it via `process-definition/{id}/xml`) and exposes
   `system_prompt()`, `context_variable_names()`, and `tool_input_output_params(activity_id)` —
   consolidating the static lookups this document's BPMN and core-API sections already worked out
   by hand, so they're computed once instead of re-derived per field. ✅ **Implemented**
   (`fluxnova/bpmn.py`, `BpmnLookup`, parses the local `.bpmn` file directly — no
   `process-definition/{id}/xml` round-trip needed since the harness already has the file on
   disk). Tests in `tests/test_bpmn.py`.
4. **Write a `build_agent_report()` function** that composes (1)–(3) plus the existing
   `client.get_variables()` call into the same dict shape `agent_report` has today, and swap the
   single line in `fluxnova/main.py` that currently calls `client.get_agent_history(...)` to call
   this instead. Keep `Client.get_agent_history()` itself in place (unused) rather than deleting
   it immediately — useful as a fallback/diff source during validation. ✅ **Implemented**
   (`fluxnova/report.py`, tests in `tests/test_report.py`); `main.py` now calls
   `build_agent_report()` instead of `client.get_agent_history()`, which remains in `client.py`,
   unused.
5. **Dual-run and diff, don't cut over blind.** For a batch of real runs, build the report both
   ways (old `/agent-history` call and new composed builder) and diff field-by-field. `goal`,
   `inputVariables`, `toolCalls[].toolName/status`, and `iterations` should match exactly today —
   any mismatch indicates a correlation-key or timing bug (e.g. OTLP export not yet flushed when
   the harness queries it) rather than a real data gap, since this document has already confirmed
   the underlying values are equivalent. `finalOutput`/`step-history` won't match until the
   content-capture plugin work ships — expect those two to remain `/agent-history`-sourced longest.
   ✅ **Verified** against a real captured run (`a24d4bd4-9d5d-11f1-b4ee-acb480d858e4`): `goal`,
   `inputVariables`, `iterations`, and every `toolCalls[]` field (including `finalOutput` — see
   step 7) matched the existing `/agent-history` report exactly.
6. **Cut over field-by-field, not all at once.** Since `finalOutput`/`step-history` are still
   pending, an incremental swap (start with `goal`, `inputVariables`, `iterations`,
   `toolCalls[].toolName/status/durationMs`/`toolInput`/`toolOutput`) lets most of the report move
   off `/agent-history` immediately, while it temporarily remains the only source for the two
   still-pending fields. **Superseded by step 7** — the content-capture plugin work landed sooner
   than expected, so `main.py` cuts over to `build_agent_report()` for every field at once instead
   of incrementally.
7. **Finish the cutover once content capture ships.** Add `get_llm_messages()` to `OtelClient`,
   wire `finalOutput`/`step-history` to it, re-run the dual-run diff from step 5 one more time for
   those two fields specifically, then remove the `client.get_agent_history()` call from
   `fluxnova/main.py` entirely. The plugin's `/agent-history` REST endpoint itself can stay in
   place for other consumers (e.g. manual debugging, the DB audit trail) — this plan only removes
   the *harness's* dependency on it, not the endpoint. ✅ **Implemented.** The plugin's
   content-capture work shipped in `fluxnova-plugins` (uncommitted `AgentOtelTracing`/
   `AgentOtelContentCaptureProperties` changes) — `chat` spans now carry `gen_ai.output.messages`.
   `OtelClient.get_llm_messages()` reads it; `build_agent_report()` uses the last `chat` span's
   output text as `finalOutput`, confirmed to match the real report byte-for-byte in the step 5
   dual-run diff. `step-history` (full loop transcript) remains unimplemented — not read by any
   current DeepEval test, so left for a future pass if a consumer needs it.

### Key risk to flag early

Steps 1–2 (the OTLP query path and correlation key) are the biggest unknown and should be
validated first, before writing any of the field-mapping code in steps 2–4: unlike the BPMN and
core-API sources (which the harness can already reach via existing HTTP calls), there is currently
**no established way for the harness to query the OTLP backend at all** — that plumbing has to be
built from scratch, whereas everything else in this plan reuses code/API patterns the harness
already has.
