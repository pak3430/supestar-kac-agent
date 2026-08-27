from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent_tools import ToolEnvironment
from .graph import KnowledgeGraph
from .ollama_client import OllamaClient
from .policy import load_policy, project_root
from .run_store import AgentRunStore
from .verifier import verify_candidate


EventSink = Callable[[dict[str, Any]], None]


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        "claim에 언급한 각 CCS 개념은 그 개념에 직접 닿는 concept·edge·Skill evidence_id를 인용하세요.",
        "검증기가 REVIEW를 반환하면 누락된 관찰이나 Skill을 수행한 뒤 새 후보를 제출하세요.",
        "unsupported_evidence_ids는 사용자에게 물을 항목이 아니라 아직 도구로 관찰하지 않은 시스템 근거입니다. 관련 개념·관계를 다시 관찰하세요.",
        "빈 응답은 허용되지 않습니다. 매 turn에는 현재 상태에 필요한 도구를 호출하세요.",
        "도구 선택 목적은 짧고 감사 가능한 문장으로만 표현하고 비공개 내부 추론은 노출하지 마세요.",
        f"사용자 역할: {role}",
        f"기준일: {as_of_date}",
        "질문에서 어휘적으로 발견된 anchor 후보(경로 지시가 아님): " + json.dumps(anchors, ensure_ascii=False),
        "등록된 원자 Skill catalog: " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
    ])


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
            definitions = environment.definitions()
            if force_submit_next:
                definitions = [
                    item for item in definitions
                    if item["function"]["name"] == "submit_answer_candidate"
                ]
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
                    and (len(anchors) < 2 or bool(observed_edges))
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
            for call in calls:
                tool_calls_used += 1
                function = call.get("function") or {}
                tool_name = str(function.get("name", ""))
                arguments = function.get("arguments") if isinstance(function.get("arguments"), dict) else {}
                action = {
                    "event_type":"tool_action",
                    "step":step,
                    "tool_name":tool_name,
                    "arguments":arguments,
                    "action_hash":_hash({"step":step,"tool_name":tool_name,"arguments":arguments}),
                }
                emit(action)
                if tool_calls_used > max_tool_calls:
                    observation = {"status":"STOP","error":"TOOL_CALL_BUDGET_EXCEEDED"}
                else:
                    observation = environment.execute(tool_name, arguments)
                if tool_name == "submit_answer_candidate" and observation.get("status") == "CANDIDATE_RECEIVED":
                    candidate = observation["candidate"]
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
                    if verification["verdict"] == "PASS":
                        completed = True
                    elif (
                        not verification["unsupported_evidence_ids"]
                        and not any(
                            requirement.startswith("anchor_observation:")
                            or requirement == "observed_relation_path_between_anchors"
                            or requirement == "executed_kac_skill"
                            for requirement in verification["missing_requirements"]
                        )
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
                if completed:
                    break
            messages.extend(tool_messages)
            if completed:
                status, stop_reason = "PASS", "ANSWER_CANDIDATE_VERIFIED"
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
