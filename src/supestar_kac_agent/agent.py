from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent_tools import ToolEnvironment
from .graph import KnowledgeGraph
from .ollama_client import OllamaClient
from .policy import load_policy, project_root
from .run_store import AgentRunStore
from .verifier import anchors_connected, verify_candidate


EventSink = Callable[[dict[str, Any]], None]


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_violations(value: Any, schema: dict[str, Any], path: str = "arguments") -> list[str]:
    violations: list[str] = []
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            return [f"{path}:object_required"]
        for required in schema.get("required", []):
            if required not in value:
                violations.append(f"{path}.{required}:required")
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                violations.extend(_schema_violations(value[key], child_schema, f"{path}.{key}"))
    elif expected_type == "array":
        if not isinstance(value, list):
            return [f"{path}:array_required"]
        if len(value) < int(schema.get("minItems", 0)):
            violations.append(f"{path}:min_items")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            violations.extend(_schema_violations(item, item_schema, f"{path}[{index}]"))
    elif expected_type == "string" and not isinstance(value, str):
        violations.append(f"{path}:string_required")
    if "enum" in schema and value not in schema["enum"]:
        violations.append(f"{path}:not_in_allowed_enum")
    return violations


def _system_prompt(*, anchors: list[str], catalog: list[dict[str, Any]], role: str, as_of_date: str) -> str:
    return "\n".join([
        "당신은 Local Qwen으로 실행되는 수페스타 KAC Agent입니다.",
        "당신은 질문에 바로 답하는 문장 생성기가 아니라, 도구 Observation을 보고 다음 행동을 선택하는 Agent loop 안에 있습니다.",
        "질문별 고정 경로는 없습니다. 현재 관찰 결과에 따라 필요한 개념, 관계, 원자 Skill을 스스로 선택하세요.",
        "일반 사전지식으로 사실을 채우지 말고 CCS 개념·edge·Skill output만 근거로 사용하세요.",
        "관계 질문이면 양쪽 anchor를 observe_concept로 관찰하고, 필요하면 expand_relations의 toward_concept로 실제 연결 경로를 관찰하세요.",
        "최종 후보 전에는 관련 KAC Skill을 최소 하나 실제 실행하세요. 입력이 부족하면 Skill의 REVIEW를 관찰하고 그 한계를 답변에 반영하세요.",
        "최종 답변은 반드시 submit_answer_candidate 도구로 제출하고, claim마다 Observation의 정확한 evidence_id를 적으세요.",
        "answer는 초안입니다. 외부에 공개되는 최종 답변은 검증을 통과한 claim text만 조립하므로 각 claim을 완전한 문장으로 쓰세요.",
        "claims 전체에서 질문의 anchor 후보를 모두 직접 다루고, 실제 실행된 SkillRun evidence_id를 최소 하나 반드시 인용하세요.",
        "claim에 언급한 각 CCS 개념은 그 개념에 직접 닿는 concept·edge·Skill evidence_id를 인용하세요.",
        "하나의 claim에서 질문 anchor 사이의 관계를 말하면 주변 edge 하나가 아니라, 관찰된 두 anchor 사이의 전체 edge 경로를 인용하세요.",
        "검증기가 REVIEW를 반환하면 누락된 관찰이나 Skill을 수행한 뒤 새 후보를 제출하세요.",
        "unsupported_evidence_ids는 사용자에게 물을 항목이 아니라 아직 도구로 관찰하지 않은 시스템 근거입니다. 관련 개념·관계를 다시 관찰하세요.",
        "빈 응답은 허용되지 않습니다. 매 turn에는 현재 상태에 필요한 도구를 호출하세요.",
        "도구 선택 목적은 짧고 감사 가능한 문장으로만 표현하고 비공개 내부 추론은 노출하지 마세요.",
        f"사용자 역할: {role}",
        f"기준일: {as_of_date}",
        "질문에서 어휘적으로 발견된 anchor 후보(경로 지시가 아님): " + json.dumps(anchors, ensure_ascii=False),
        "등록된 원자 Skill catalog: " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
    ])


def _repair_observed_concept_citations(
    candidate: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any] | None:
    """Add only verifier-proposed, already observed evidence IDs.

    This does not rewrite a claim or invent knowledge. It is limited to missing
    citations where the verifier has already identified an observed concept,
    relation path, or executed SkillRun evidence ID for that exact claim.
    """
    missing = verification.get("missing_requirements", [])
    uncovered_requirements = [
        item for item in missing
        if str(item).startswith("claim_uncovered_concept:")
    ]
    relation_requirements = [
        item for item in missing
        if str(item).startswith("claim_relation_path:")
    ]
    needs_executed_skill_citation = "cited_executed_skill_output" in missing
    if (
        not uncovered_requirements and not relation_requirements
    ) or (
        verification.get("unsupported_evidence_ids")
        or verification.get("forbidden_confusions")
        or not all(
            str(item).startswith("claim_uncovered_concept:")
            or str(item).startswith("claim_grounding_overlap:")
            or str(item).startswith("claim_relation_path:")
            or str(item) == "cited_executed_skill_output"
            for item in missing
        )
    ):
        return None
    repaired = deepcopy(candidate)
    claims = repaired.get("claims") if isinstance(repaired.get("claims"), list) else []
    changed = False
    repair_map = verification.get("repair_evidence_by_concept", {})
    for item in uncovered_requirements:
        _, index_text, concept_id = str(item).split(":", 2)
        index = int(index_text)
        if index >= len(claims) or not isinstance(claims[index], dict):
            return None
        candidates = repair_map.get(concept_id, [])
        if not candidates:
            return None
        evidence_ids = claims[index].get("evidence_ids")
        evidence_ids = list(evidence_ids) if isinstance(evidence_ids, list) else []
        stable_skill_evidence = next(
            (
                value for value in candidates
                if str(value).startswith("skill:")
                and not str(value).startswith("skill:skill-run-")
            ),
            None,
        )
        if needs_executed_skill_citation and stable_skill_evidence:
            preferred = stable_skill_evidence
            needs_executed_skill_citation = False
        else:
            preferred = next((value for value in candidates if str(value).startswith("concept:")), candidates[0])
        if preferred not in evidence_ids:
            evidence_ids.append(preferred)
            claims[index]["evidence_ids"] = evidence_ids
            changed = True
    relation_repair_map = verification.get("repair_relation_evidence_by_claim", {})
    for item in relation_requirements:
        index = int(str(item).rsplit(":", 1)[1])
        if index >= len(claims) or not isinstance(claims[index], dict):
            return None
        path_evidence_ids = relation_repair_map.get(str(index), [])
        if not path_evidence_ids:
            return None
        evidence_ids = claims[index].get("evidence_ids")
        evidence_ids = list(evidence_ids) if isinstance(evidence_ids, list) else []
        for evidence_id in path_evidence_ids:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
                changed = True
        claims[index]["evidence_ids"] = evidence_ids
    return repaired if changed else None


def _normalize_executed_skill_evidence_ids(
    candidate: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Repair only a missing `skill:` namespace for an exact observed ID."""
    normalized = deepcopy(candidate)
    replacements: list[dict[str, str]] = []
    claims = normalized.get("claims") if isinstance(normalized.get("claims"), list) else []
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("evidence_ids"), list):
            continue
        values = []
        for raw in claim["evidence_ids"]:
            evidence_id = str(raw)
            replacement = evidence_id
            if evidence_id not in evidence:
                namespaced = f"skill:{evidence_id}"
                if namespaced in evidence and (
                    evidence_id.startswith("skill-run-")
                    or evidence_id.endswith(":latest")
                ):
                    replacement = namespaced
                    replacements.append({"from":evidence_id, "to":replacement})
            values.append(replacement)
        claim["evidence_ids"] = values
    return normalized, replacements


def _bind_trusted_skill_context(
    arguments: dict[str, Any],
    *,
    question: str,
    role: str,
    as_of_date: str,
    catalog_by_name: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind request-envelope context that the model must not guess or alter."""
    bound = deepcopy(arguments)
    skill_name = str(bound.get("skill_name", ""))
    contract = catalog_by_name.get(skill_name, {})
    accepted_inputs = set(contract.get("required_inputs", [])) | set(contract.get("optional_inputs", []))
    inputs = bound.get("inputs") if isinstance(bound.get("inputs"), dict) else {}
    inputs = dict(inputs)
    changes: list[dict[str, Any]] = []
    for field, trusted_value in (
        ("question", question),
        ("userRole", role),
        ("asOfDate", as_of_date),
    ):
        if field not in accepted_inputs or inputs.get(field) == trusted_value:
            continue
        changes.append({
            "field":field,
            "model_value":inputs.get(field),
            "bound_value":trusted_value,
        })
        inputs[field] = trusted_value
    bound["inputs"] = inputs
    return bound, changes


def _lifecycle_gate(
    environment: ToolEnvironment,
    anchors: list[str],
    graph: KnowledgeGraph,
    *,
    force_submit: bool = False,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Expose only tools that can advance the current evidence lifecycle.

    The gate is generic and verifier-driven: it never maps a question to a
    particular skill or answer. Qwen still chooses the concrete relation,
    skill, and inputs inside the currently valid stage.
    """
    observed_concepts = {
        evidence_id.split(":", 1)[1]
        for evidence_id in environment.evidence
        if evidence_id.startswith("concept:")
    }
    observed_edges = {
        evidence_id
        for evidence_id in environment.evidence
        if evidence_id.startswith("edge:")
    }
    unobserved_anchors = [anchor for anchor in anchors if anchor not in observed_concepts]
    connected = anchors_connected(anchors, observed_edges, graph)
    state = {
        "unobserved_anchors":unobserved_anchors,
        "anchors_connected":connected,
        "skill_run_count":len(environment.skill_runs),
    }
    if force_submit:
        return "SUBMIT_REPAIR", environment.definitions(
            allowed_tool_names={"submit_answer_candidate"},
        ), state
    if unobserved_anchors:
        return "OBSERVE_REQUIRED_ANCHORS", environment.definitions(
            allowed_tool_names={"observe_concept"},
            concept_choices=unobserved_anchors,
        ), state
    if len(anchors) >= 2 and not connected:
        return "CONNECT_OBSERVED_ANCHORS", environment.definitions(
            allowed_tool_names={"expand_relations"},
            relation_endpoint_choices=anchors,
        ), state
    if not environment.skill_runs:
        applicable_skills = sorted({
            skill_name
            for anchor in anchors
            for skill_name in graph.nodes.get(anchor, {}).get("applicable_skills", [])
        })
        return "EXECUTE_KAC_SKILL", environment.definitions(
            allowed_tool_names={"invoke_kac_skill"},
            skill_choices=applicable_skills or None,
        ), state
    return "SUBMIT_CANDIDATE", environment.definitions(
        allowed_tool_names={"submit_answer_candidate"},
    ), state


def run_agent(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    runs_root: Path | None = None,
    db_path: Path | None = None,
    client: OllamaClient | Any | None = None,
    event_sink: EventSink | None = None,
) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    policy = load_policy(root)
    question = str(request.get("question", "")).strip()
    if not question:
        raise ValueError("question is required")
    role = str(request.get("userRole", "LEARNER")).strip().upper()
    as_of_date = str(request.get("asOfDate", date.today().isoformat())).strip()
    run_id = f"agent-run-{uuid.uuid4()}"
    run_dir = (runs_root or root / "runs") / run_id
    store = AgentRunStore(run_dir, db_path or root / ".state" / "supestar_agent.sqlite3")
    graph = KnowledgeGraph(root)
    anchors = graph.anchor_ids(question)
    environment = ToolEnvironment(root=root, run_dir=run_dir)
    model_policy = policy["local_llm"]
    client = client or OllamaClient(model_policy["default_endpoint"], model_policy["default_model"])
    model_identity = client.identity()
    prompt = _system_prompt(anchors=anchors, catalog=environment.catalog, role=role, as_of_date=as_of_date)
    prompt_hash = _hash(prompt)
    messages: list[dict[str, Any]] = [
        {"role":"system","content":prompt},
        {"role":"user","content":question},
    ]
    max_steps = int(policy["autonomy"]["max_steps"])
    max_tool_calls = int(policy["autonomy"]["max_tool_calls"])
    tool_calls_used = 0
    consecutive_direct_answer_rejections = 0
    candidate: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    verification_signatures: dict[str, int] = {}
    inference_metrics: list[dict[str, Any]] = []
    force_submit_next = False

    def emit(event: dict[str, Any]) -> dict[str, Any]:
        record = store.append_event(run_id, event)
        if event_sink:
            event_sink(record)
        return record

    request_record = {
        "run_id":run_id,
        "created_at":datetime.now(timezone.utc).isoformat(),
        "request":request,
        "anchor_candidates":anchors,
        "graph_fingerprint":graph.fingerprint,
        "prompt_hash":prompt_hash,
    }
    store.write("request.json", request_record)
    store.write("model_identity.json", model_identity)
    emit({"event_type":"agent_started","run_id":run_id,"anchor_candidates":anchors,"model":model_identity})

    status = "STOP"
    stop_reason = "MAX_STEPS_REACHED"
    try:
        for step in range(1, max_steps + 1):
            lifecycle_gate, definitions, lifecycle_state = _lifecycle_gate(
                environment,
                anchors,
                graph,
                force_submit=force_submit_next,
            )
            emit({
                "event_type":"lifecycle_gate_selected",
                "step":step,
                "gate":lifecycle_gate,
                "allowed_tool_names":[item["function"]["name"] for item in definitions],
                **lifecycle_state,
            })
            try:
                response = client.chat(messages, definitions)
            except Exception as error:
                stop_reason = "MODEL_INFERENCE_ERROR"
                emit({
                    "event_type":"model_error",
                    "step":step,
                    "error_type":type(error).__name__,
                    "error_message":str(error)[:500],
                })
                break
            message = response["message"]
            metrics = {"step":step, **response.get("metrics", {})}
            inference_metrics.append(metrics)
            calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
            emit({
                "event_type":"llm_turn",
                "step":step,
                "tool_names":[(call.get("function") or {}).get("name") for call in calls],
                "assistant_content_hash":_hash(message.get("content", "")),
                "assistant_content_preview":str(message.get("content", ""))[:240],
                "metrics":metrics,
            })
            messages.append(message)
            if not calls and hasattr(client, "select_tool_action"):
                recovery_definitions = definitions
                if recovery_definitions:
                    try:
                        recovered = client.select_tool_action(
                            question=question,
                            rejected_draft=str(message.get("content", "")),
                            anchors=anchors,
                            evidence_catalog=environment.evidence_catalog(),
                            skill_catalog=environment.catalog,
                            allowed_tools=recovery_definitions,
                        )
                        recovery_metrics = {"step":step, **recovered.get("metrics", {})}
                        inference_metrics.append(recovery_metrics)
                        action = recovered["action"]
                        calls = [{"function":{"name":action["tool_name"], "arguments":action["arguments"]}}]
                        messages[-1] = {"role":"assistant", "content":"", "tool_calls":calls}
                        emit({
                            "event_type":"action_structured",
                            "step":step,
                            "adapter":"OLLAMA_JSON_SCHEMA",
                            "tool_name":action["tool_name"],
                            "action_hash":_hash(action),
                            "metrics":recovery_metrics,
                        })
                    except Exception as error:
                        emit({
                            "event_type":"model_error",
                            "step":step,
                            "phase":"tool_action_recovery",
                            "error_type":type(error).__name__,
                            "error_message":str(error)[:500],
                        })
            if not calls:
                consecutive_direct_answer_rejections += 1
                emit({"event_type":"direct_answer_rejected","step":step,"reason":"submit_answer_candidate tool was not used"})
                if consecutive_direct_answer_rejections >= 4:
                    stop_reason = "MODEL_DID_NOT_USE_REQUIRED_TOOLS"
                    break
                observed_concepts = sorted(
                    evidence_id.split(":", 1)[1]
                    for evidence_id in environment.evidence
                    if evidence_id.startswith("concept:")
                )
                unobserved_anchors = [anchor for anchor in anchors if anchor not in observed_concepts]
                observed_edges = {
                    evidence_id for evidence_id in environment.evidence if evidence_id.startswith("edge:")
                }
                prerequisites_ready = (
                    not unobserved_anchors
                    and bool(environment.skill_runs)
                    and anchors_connected(anchors, observed_edges, graph)
                )
                if prerequisites_ready and hasattr(client, "structure_candidate"):
                    adapter_draft = str(message.get("content", ""))
                    adapter_feedback = verification
                    adapter_error = False
                    for adapter_attempt in range(1, 3):
                        try:
                            structured = client.structure_candidate(
                                question=question,
                                draft=adapter_draft,
                                evidence_catalog=environment.evidence_catalog(),
                                verification_feedback=adapter_feedback,
                            )
                        except Exception as error:
                            stop_reason = "CANDIDATE_STRUCTURING_ERROR"
                            emit({
                                "event_type":"model_error",
                                "step":step,
                                "phase":"candidate_structuring",
                                "adapter_attempt":adapter_attempt,
                                "error_type":type(error).__name__,
                                "error_message":str(error)[:500],
                            })
                            adapter_error = True
                            break
                        structuring_metrics = {
                            "step":step,
                            "adapter_attempt":adapter_attempt,
                            **structured.get("metrics", {}),
                        }
                        inference_metrics.append(structuring_metrics)
                        candidate = structured["candidate"]
                        emit({
                            "event_type":"candidate_structured",
                            "step":step,
                            "adapter":"OLLAMA_JSON_SCHEMA",
                            "adapter_attempt":adapter_attempt,
                            "candidate_hash":_hash(candidate),
                            "metrics":structuring_metrics,
                        })
                        verification = verify_candidate(
                            candidate,
                            anchors=anchors,
                            evidence=environment.evidence,
                            skill_runs=environment.skill_runs,
                            graph=graph,
                        )
                        emit({
                            "event_type":"verification",
                            "step":step,
                            "adapter":"OLLAMA_JSON_SCHEMA",
                            "adapter_attempt":adapter_attempt,
                            **verification,
                        })
                        if verification["verdict"] == "PASS":
                            status, stop_reason = "PASS", "STRUCTURED_ANSWER_CANDIDATE_VERIFIED"
                            break
                        adapter_draft = json.dumps(candidate, ensure_ascii=False)
                        adapter_feedback = verification
                    if status == "PASS" or adapter_error:
                        break
                    messages.append({
                        "role":"user",
                        "content":(
                            "JSON 스키마 후보도 REVIEW입니다. 다음 행동을 선택하세요. 검증 결과="
                            + json.dumps(verification, ensure_ascii=False)
                        ),
                    })
                force_submit_next = prerequisites_ready
                messages.append({
                    "role":"user",
                    "content":(
                        "응답이 비어 있거나 직접 답변하여 승인되지 않았습니다. 지금 반드시 도구를 하나 이상 호출하세요. "
                        f"미관찰 anchor={json.dumps(unobserved_anchors, ensure_ascii=False)}, "
                        f"관찰된 evidence_id={json.dumps(sorted(environment.evidence), ensure_ascii=False)}, "
                        f"실행된 Skill 수={len(environment.skill_runs)}, 제출 전제 충족={prerequisites_ready}. "
                        "미관찰 anchor가 있으면 observe_concept, 관계 근거가 부족하면 expand_relations, "
                        "도메인 판단이 필요하면 invoke_kac_skill을 선택하세요. 제출 전제가 충족되었다면 "
                        "직전 자연어는 초안일 뿐이므로, 다음 응답에서 반드시 submit_answer_candidate를 호출하고 "
                        "관찰된 evidence_id만 claim에 연결하세요."
                    ),
                })
                continue
            consecutive_direct_answer_rejections = 0
            force_submit_next = False
            tool_messages = []
            completed = False
            fatal_tool_error: str | None = None
            definition_by_name = {
                item["function"]["name"]:item["function"]
                for item in definitions
            }
            for call in calls:
                tool_calls_used += 1
                function = call.get("function") or {}
                tool_name = str(function.get("name", ""))
                arguments = function.get("arguments") if isinstance(function.get("arguments"), dict) else {}
                context_bindings: list[dict[str, Any]] = []
                if tool_name == "invoke_kac_skill":
                    arguments, context_bindings = _bind_trusted_skill_context(
                        arguments,
                        question=question,
                        role=role,
                        as_of_date=as_of_date,
                        catalog_by_name=environment.catalog_by_name,
                    )
                    if context_bindings:
                        emit({
                            "event_type":"trusted_skill_context_bound",
                            "step":step,
                            "skill_name":arguments.get("skill_name"),
                            "bindings":context_bindings,
                            "rule":"REQUEST_ENVELOPE_OVERRIDES_MODEL_ARGUMENTS",
                        })
                action = {
                    "event_type":"tool_action",
                    "step":step,
                    "tool_name":tool_name,
                    "arguments":arguments,
                    "action_hash":_hash({"step":step,"tool_name":tool_name,"arguments":arguments}),
                }
                emit(action)
                allowed_definition = definition_by_name.get(tool_name)
                validation_arguments = arguments
                if tool_name == "submit_answer_candidate":
                    validation_arguments, _ = _normalize_executed_skill_evidence_ids(
                        arguments,
                        environment.evidence,
                    )
                argument_violations = (
                    _schema_violations(
                        validation_arguments,
                        allowed_definition.get("parameters", {}),
                    )
                    if allowed_definition else []
                )
                if allowed_definition is None:
                    observation = {
                        "status":"REJECTED_TOOL_OUTSIDE_LIFECYCLE_GATE",
                        "tool_name":tool_name,
                        "allowed_tool_names":sorted(definition_by_name),
                    }
                elif argument_violations:
                    observation = {
                        "status":"REJECTED_ARGUMENTS_OUTSIDE_LIFECYCLE_GATE",
                        "tool_name":tool_name,
                        "violations":argument_violations,
                    }
                elif tool_calls_used > max_tool_calls:
                    observation = {"status":"STOP","error":"TOOL_CALL_BUDGET_EXCEEDED"}
                else:
                    try:
                        observation = environment.execute(tool_name, arguments)
                    except Exception as error:
                        observation = {
                            "status":"STOP",
                            "error":"UNHANDLED_TOOL_EXECUTION_ERROR",
                            "tool_name":tool_name,
                            "error_type":type(error).__name__,
                            "error_message":str(error)[:500],
                        }
                if observation.get("status") == "STOP" and observation.get("error"):
                    fatal_tool_error = str(observation["error"])
                if tool_name == "submit_answer_candidate" and observation.get("status") == "CANDIDATE_RECEIVED":
                    candidate = observation["candidate"]
                    candidate, evidence_replacements = _normalize_executed_skill_evidence_ids(
                        candidate,
                        environment.evidence,
                    )
                    if evidence_replacements:
                        emit({
                            "event_type":"candidate_evidence_normalized",
                            "step":step,
                            "normalization":"EXACT_EXECUTED_SKILL_ID_NAMESPACE_ONLY",
                            "replacements":evidence_replacements,
                            "candidate_hash":_hash(candidate),
                        })
                    verification = verify_candidate(
                        candidate,
                        anchors=anchors,
                        evidence=environment.evidence,
                        skill_runs=environment.skill_runs,
                        graph=graph,
                    )
                    observation = {
                        "status":"CANDIDATE_VERIFIED",
                        "verification":verification,
                        "available_evidence_ids":sorted(environment.evidence),
                        "repair_rule":"REVIEW이면 사용자에게 묻기 전에 missing_requirements에 맞는 도구 관찰을 수행하고, unsupported_evidence_ids는 실제 관찰된 evidence_id로 교체하세요.",
                    }
                    emit({"event_type":"verification","step":step,**verification})
                    verification_signature = _hash({
                        "missing":verification.get("missing_requirements", []),
                        "unsupported":verification.get("unsupported_evidence_ids", []),
                        "forbidden":verification.get("forbidden_confusions", []),
                    })
                    verification_signatures[verification_signature] = verification_signatures.get(verification_signature, 0) + 1
                    if verification_signatures[verification_signature] >= 2:
                        emit({
                            "event_type":"repeated_verification_blocked",
                            "step":step,
                            "verification_signature":verification_signature,
                            "occurrences":verification_signatures[verification_signature],
                            "rule":"DO_NOT_REEXECUTE_SKILL_FOR_REPEATED_CITATION_ERROR",
                        })
                    repaired_candidate = _repair_observed_concept_citations(candidate, verification)
                    if repaired_candidate is not None:
                        repaired_verification = verify_candidate(
                            repaired_candidate,
                            anchors=anchors,
                            evidence=environment.evidence,
                            skill_runs=environment.skill_runs,
                            graph=graph,
                        )
                        emit({
                            "event_type":"candidate_evidence_repaired",
                            "step":step,
                            "repair_type":"OBSERVED_EVIDENCE_CITATIONS_ONLY",
                            "before_candidate_hash":_hash(candidate),
                            "after_candidate_hash":_hash(repaired_candidate),
                            "added_evidence_ids":sorted(
                                {
                                    evidence_id
                                    for claim in repaired_candidate.get("claims", [])
                                    for evidence_id in claim.get("evidence_ids", [])
                                }
                                - {
                                    evidence_id
                                    for claim in candidate.get("claims", [])
                                    for evidence_id in claim.get("evidence_ids", [])
                                }
                            ),
                        })
                        emit({"event_type":"verification","step":step,"repair_attempt":1,**repaired_verification})
                        candidate = repaired_candidate
                        verification = repaired_verification
                        observation["candidate_repaired"] = True
                        observation["verification"] = verification
                    if verification["verdict"] == "PASS":
                        completed = True
                    elif not any(
                            requirement.startswith("anchor_observation:")
                            or requirement == "observed_relation_path_between_anchors"
                            or requirement == "executed_kac_skill"
                            for requirement in verification["missing_requirements"]
                        ):
                        force_submit_next = True
                emit({
                    "event_type":"observation",
                    "step":step,
                    "tool_name":tool_name,
                    "status":observation.get("status"),
                    "observation":observation,
                    "observation_hash":_hash(observation),
                })
                tool_messages.append({"role":"tool","tool_name":tool_name,"content":json.dumps(observation,ensure_ascii=False)})
                if completed or fatal_tool_error:
                    break
            messages.extend(tool_messages)
            if completed:
                status, stop_reason = "PASS", "ANSWER_CANDIDATE_VERIFIED"
                break
            if fatal_tool_error:
                stop_reason = fatal_tool_error
                break
            if tool_calls_used > max_tool_calls:
                stop_reason = "TOOL_CALL_BUDGET_EXCEEDED"
                break
        source_refs = verification.get("source_refs", []) if verification else []
        package = {
            "object_type":"AutonomousKACAgentRun",
            "run_id":run_id,
            "status":status,
            "stop_reason":stop_reason,
            "question":question,
            "answer":verification.get("verified_answer") if verification and status == "PASS" else None,
            "claims":candidate.get("claims", []) if candidate and status == "PASS" else [],
            "verification":verification,
            "anchor_candidates":anchors,
            "observed_evidence_ids":sorted(environment.evidence),
            "skills_invoked":[run["skill_name"] for run in environment.skill_runs],
            "skill_run_ids":[run["skill_run_id"] for run in environment.skill_runs],
            "source_refs":source_refs,
            "model_identity":model_identity,
            "prompt_hash":prompt_hash,
            "inference_metrics":inference_metrics,
            "llm_called":bool(inference_metrics),
            "local_llm_verified":bool(inference_metrics) and model_identity.get("endpoint_scope") == "LOOPBACK_ONLY",
            "internet_used":False,
            "question_specific_route_map_used":False,
            "tool_calls_used":tool_calls_used,
            "event_count":len(store.events) + 1,
            "output_state":"OUTPUT_VERIFIED" if status == "PASS" else "NO_VERIFIED_OUTPUT",
            "outcome_state":"OUTCOME_NOT_OBSERVED",
            "run_directory":str(run_dir),
        }
        emit({"event_type":"agent_completed","status":status,"stop_reason":stop_reason,"answer_hash":_hash(package.get("answer")) if package.get("answer") else None})
        package["event_count"] = len(store.events)
        store.finalize(run_id, status, package)
        return package
    finally:
        store.close()
