from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .graph import KnowledgeGraph


SkillResult = dict[str, Any]


def _valid_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _contains(text: str, *needles: str) -> bool:
    normalized = text.casefold()
    return any(needle.casefold() in normalized for needle in needles)


def esg_carbon_action_path(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    traversal = payload.pop("__traversal_provenance", None)
    question = str(payload.get("question", "")).strip()
    role = str(payload.get("userRole", "")).strip().upper()
    as_of_date = str(payload.get("asOfDate", "")).strip()
    focus = str(payload.get("focus", "MEASUREMENT")).strip().upper()
    allowed_focus = {
        "MEASUREMENT": "CO2E",
        "SCOPE": "OPERATIONAL_BOUNDARY",
        "SDGS": "SDGS",
        "MARKET": "CARBON_CREDIT",
        "FOREST_CARBON": "FOREST_CARBON_PROJECT",
    }
    missing = []
    if not question:
        missing.append("question")
    if role not in {"LEARNER", "ESG_MANAGER", "FOREST_OWNER_OPERATOR", "REVIEWER"}:
        missing.append("userRole")
    if not _valid_date(as_of_date):
        missing.append("asOfDate")
    if focus not in allowed_focus:
        missing.append("focus")
    if _contains(question, "구매해줘", "결제해", "등록부 변경", "탄소중립 확정"):
        verdict, trace = "STOP", ["EXTERNAL_ACTION_OR_UNMEASURED_ASSERTION_BLOCKED"]
    elif missing:
        verdict, trace = "REVIEW", ["REQUIRED_CONTEXT_MISSING"]
    elif focus in {"MARKET", "FOREST_CARBON"} and not payload.get("measurementEvidence"):
        verdict, trace = "REVIEW", ["MARKET_PREREQUISITES_MISSING"]
        missing.extend(["organization_boundary", "activity_data", "direct_reduction_and_residual_evidence"])
    else:
        verdict, trace = "PROCEED", ["DIRECTED_GROUNDED_PATH_SELECTED"]
    target_concept = allowed_focus.get(focus, "CO2E")
    if traversal and traversal.get("status") == "COMPLETED":
        active_path = traversal.get("active_path", {})
        node_ids = list(active_path.get("node_ids", []))
        edge_ids = list(active_path.get("edge_ids", []))
        path = {
            "status":"PATH_FOUND" if node_ids and len(edge_ids) == len(node_ids) - 1 else "INVALID_TRAVERSAL_PATH",
            "node_ids":node_ids,
            "edge_ids":edge_ids,
            "edges":[graph.edges[edge_id] for edge_id in edge_ids if edge_id in graph.edges],
        }
        trace.append("AGENT_SELECTED_TRAVERSAL_PATH_USED")
    else:
        path = graph.shortest_path("ESG", target_concept)
        if path.get("status") != "PATH_FOUND":
            path = graph.shortest_path("ESG", target_concept, bidirectional=True)
            if path.get("status") == "PATH_FOUND":
                trace.append("BIDIRECTIONAL_GROUNDED_RELATION_FALLBACK")
    if path.get("status") != "PATH_FOUND":
        verdict = "STOP"
        trace.append("GROUNDED_ESG_ACTION_PATH_UNAVAILABLE")
        missing.append(f"grounded_path:ESG:{target_concept}")
    if "GROUNDED_ESG_ACTION_PATH_UNAVAILABLE" in trace:
        answer = "현재 승인된 CCS 관계 안에서는 요청한 종료점까지의 연속 경로를 확인할 수 없어 답변을 중단합니다."
    elif verdict == "STOP":
        answer = "설명과 준비 경로는 만들 수 있지만 실제 거래·결제·등록부 변경이나 근거 없는 탄소중립 확정은 실행하지 않습니다."
    elif verdict == "REVIEW":
        answer = "요청한 연결을 검토하려면 비어 있는 조직 경계·측정·직접감축·잔여배출 근거를 먼저 확인해야 합니다."
    else:
        answer = "관찰된 CCS 관계에서 요청 목적까지의 연속된 ESG 행동 경로를 확인했습니다."
    return {
        "verdict": verdict,
        "answer": answer,
        "ordered_nodes": path.get("node_ids", []),
        "reason_per_edge": [{"edge_id": edge["id"], "reason": edge["reason"]} for edge in path.get("edges", [])],
        "next_action": {"missing_evidence": sorted(set(missing)), "human_review_required": True},
        "missing_evidence": sorted(set(missing)),
        "rule_trace": trace,
    }


def scope_activity_classification(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    description = str(payload.get("activity_description", "")).strip()
    boundary = str(payload.get("organization_boundary", "")).strip()
    control = str(payload.get("source_ownership_or_control", "")).strip().upper()
    energy = str(payload.get("purchased_energy_type", "")).strip().upper()
    value_chain = str(payload.get("value_chain_relation", "")).strip().upper()
    missing, matches, trace = [], [], []
    if (
        _contains(description, "scope 1")
        and _contains(description, "scope 2")
        and _contains(description, "scope 3")
        and _contains(description, "합")
    ):
        return {"verdict":"STOP","answer":"Scope 3는 Scope 1과 Scope 2의 합이 아닙니다.","candidate_scope":None,"rule_trace":["SCOPE3_NOT_SUM"],"missing_evidence":[]}
    fuel_combustion = _contains(description, "연소", "보일러", "태워") and _contains(
        description, "도시가스", "천연가스", "lng", "lpg", "경유", "휘발유", "연료"
    )
    if fuel_combustion and control == "OWNED_CONTROLLED" and energy != "NONE":
        return {
            "verdict":"REVIEW",
            "answer":"소유·통제 설비의 연료 연소는 외부에서 구매한 전기·스팀·열·냉방 사용과 구분해야 합니다. purchased_energy_type을 NONE으로 정정할 수 있는지 원자료를 확인해 주세요.",
            "candidate_scope":None,
            "rule_trace":["OWNED_FUEL_COMBUSTION_CONFLICTS_WITH_PURCHASED_ENERGY_TYPE"],
            "missing_evidence":["purchased_energy_type_correction_or_source_evidence"],
        }
    if not description:
        missing.append("activity_description")
    if not boundary:
        missing.append("organization_boundary")
    purchased_energy = energy in {"ELECTRICITY", "STEAM", "HEAT", "COOLING"}
    if control not in {"OWNED_CONTROLLED", "NOT_OWNED_CONTROLLED", "UNKNOWN"}:
        missing.append("source_ownership_or_control")
    elif control == "UNKNOWN" and not purchased_energy:
        missing.append("source_ownership_or_control")
    if energy not in {"ELECTRICITY", "STEAM", "HEAT", "COOLING", "NONE", "UNKNOWN"} or energy == "UNKNOWN":
        missing.append("purchased_energy_type")
    if value_chain not in {"UPSTREAM", "DOWNSTREAM", "NONE", "UNKNOWN"}:
        missing.append("value_chain_relation")
    elif value_chain == "UNKNOWN" and not purchased_energy and not (control == "OWNED_CONTROLLED" and energy == "NONE"):
        missing.append("value_chain_relation")
    if control == "OWNED_CONTROLLED" and energy == "NONE":
        matches.append("SCOPE_1"); trace.append("OWNED_OR_CONTROLLED_DIRECT_SOURCE")
    if purchased_energy:
        matches.append("SCOPE_2"); trace.append("PURCHASED_CONSUMED_ENERGY")
    if control == "NOT_OWNED_CONTROLLED" and energy == "NONE" and value_chain in {"UPSTREAM", "DOWNSTREAM"}:
        matches.append("SCOPE_3"); trace.append("OTHER_VALUE_CHAIN_INDIRECT")
    unique = sorted(set(matches))
    if len(unique) == 1 and not missing:
        candidate, verdict = unique[0], "PROCEED"
        answer = f"입력된 경계와 활동 관계에 따르면 {candidate.replace('_', ' ')} 후보입니다. 배출량 산정과 보증은 별도입니다."
    else:
        candidate, verdict = None, "REVIEW"
        if len(unique) > 1:
            missing.append("conflicting_scope_relationships")
        if not unique:
            missing.append("classifiable_activity_relationship")
        answer = "단일 Scope 후보를 정하려면 소유·통제, 구매에너지, 가치사슬 관계를 더 확인해야 합니다."
    return {"verdict":verdict,"answer":answer,"candidate_scope":candidate,"rule_trace":trace,"missing_evidence":sorted(set(missing))}


def carbon_market_unit_comparison(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    question = str(payload.get("question", "")).strip()
    purpose = str(payload.get("purpose", "")).strip().upper()
    as_of_date = str(payload.get("asOfDate", "")).strip()
    missing, trace = [], []
    if not question:
        missing.append("question")
    if purpose not in {"REGULATORY_COMPLIANCE", "VOLUNTARY_TARGET", "CONTRIBUTION", "CLAIM_REVIEW", "LEARNING"}:
        missing.append("purpose")
    if not _valid_date(as_of_date):
        missing.append("asOfDate")
    if _contains(question, "동시에 사용", "이중사용", "두 번 상쇄", "탄소중립으로 확정"):
        verdict, trace = "STOP", ["DOUBLE_USE_OR_UNSUPPORTED_CLAIM_BLOCKED"]
    elif missing:
        verdict, trace = "REVIEW", ["REQUIRED_CONTEXT_MISSING"]
    elif _contains(question, "실제로 사용", "제출 가능", "거래 가능", "상쇄에 써") and not (payload.get("unitType") and payload.get("registryStatus")):
        verdict, trace = "REVIEW", ["ACTUAL_USE_STATUS_UNRESOLVED"]
        if not payload.get("unitType"):
            missing.append("unitType")
        if not payload.get("registryStatus"):
            missing.append("registryStatus")
    else:
        verdict, trace = "PROCEED", ["COMPARISON_AXES_SEPARATED"]
    rows = [
        {"concept":"CCM","axis":"시장 유형","condition":"규제대상·인정 단위·제출 규칙"},
        {"concept":"VCM","axis":"시장 유형","condition":"표준·방법론·주장·무결성 규칙"},
        {"concept":"배출권","axis":"규제 단위","condition":"법령·할당·보유·제출 상태"},
        {"concept":"탄소크레딧","axis":"성과 단위","condition":"사업·방법론·검증·인증·등록 상태"},
        {"concept":"상쇄","axis":"사용행위","condition":"직접감축 우선·사용상태·주장 범위"},
    ]
    answer = "CCM·VCM은 시장 유형, 배출권·탄소크레딧은 서로 다른 단위, 상쇄는 조건을 갖춘 사용행위입니다."
    if verdict == "STOP":
        answer = "중복 사용이나 미확인 단위로 상쇄·탄소중립을 확정할 수 없습니다."
    elif verdict == "REVIEW":
        answer = "개념 비교는 가능하지만 실제 사용 가능성은 단위와 등록·사용상태를 더 확인해야 합니다."
    return {"verdict":verdict,"answer":answer,"concept_rows":rows,"claim_cautions":["직접감축 우선","사용상태 확인","이중사용 금지"],"missing_evidence":sorted(set(missing)),"rule_trace":trace}


def forest_esg_impact_mapping(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    summary = str(payload.get("projectSummary", "")).strip()
    as_of_date = str(payload.get("asOfDate", "")).strip()
    collections = {"E":payload.get("environmentEvidence", []),"S":payload.get("socialEvidence", []),"G":payload.get("governanceEvidence", [])}
    missing = ([] if summary else ["projectSummary"]) + ([] if _valid_date(as_of_date) else ["asOfDate"])
    labels = {
        "E":("환경",["흡수·저장","생태","방법론","모니터링"]),
        "S":("사회",["산주·임업인·지역사회","권리·동의","편익·안전"]),
        "G":("지배구조",["기관 역할","검증·인증","등록·계약·감사"]),
    }
    axis_map = {}
    questions = []
    for axis, evidence in collections.items():
        items = evidence if isinstance(evidence, list) else []
        gaps = [] if items else [f"{axis}_evidence"]
        missing.extend(gaps)
        axis_map[axis] = {"label":labels[axis][0],"nodes":labels[axis][1],"evidence":items,"gaps":gaps}
        if gaps:
            questions.append({"axis":axis,"question":f"{labels[axis][0]} 축의 근거는 무엇인가요?"})
    if _contains(summary, "사회는 빼", "권리는 숨", "거버넌스는 빼", "흡수량만으로 esg"):
        verdict, trace = "STOP", ["HIDDEN_AXIS_BLOCKED"]
    elif missing:
        verdict, trace = "REVIEW", ["ONE_OR_MORE_AXIS_GAPS"]
    else:
        verdict, trace = "PROCEED", ["ALL_AXES_HAVE_LINKED_EVIDENCE"]
    answer = "산림탄소는 환경 성과뿐 아니라 참여자 권리와 지배구조 책임을 함께 확인해야 합니다."
    return {"verdict":verdict,"answer":answer,"axis_map":axis_map,"missing_axis_questions":questions,"missing_evidence":sorted(set(missing)),"rule_trace":trace}


def forest_carbon_procedure_guidance(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    project_type = str(payload.get("projectType", "UNKNOWN")).upper()
    current_stage = str(payload.get("currentStage", "UNKNOWN")).upper()
    intended_use = str(payload.get("intendedUse", "UNDECIDED")).upper()
    as_of_date = str(payload.get("asOfDate", ""))
    documents = payload.get("availableDocuments", []) if isinstance(payload.get("availableDocuments", []), list) else []
    codes = ["PLANNING","ELIGIBILITY","REGISTERED","IMPLEMENTING","MONITORING","VERIFIED","CERTIFIED","USE","REGISTRY_MANAGED"]
    labels = ["사업계획","타당성·적격성 검토","사업등록","실행","모니터링","독립 검증","인증","거래 또는 비거래 활용","등록부 상태관리"]
    states = {code:"MISSING" for code in codes}
    for item in documents:
        if isinstance(item, dict) and str(item.get("stage","")).upper() in states:
            state = str(item.get("evidenceState","UNVERIFIED")).upper()
            if state in {"CONFIRMED","UNVERIFIED","MISSING"}:
                states[str(item["stage"]).upper()] = state
    completed=[]
    for code,label in zip(codes,labels):
        if states[code] == "CONFIRMED" and len(completed) == codes.index(code):
            completed.append(label)
        else:
            break
    blocked = labels[len(completed)] if len(completed) < len(labels) else None
    missing=[]
    if project_type == "UNKNOWN": missing.append("projectType")
    if current_stage not in codes: missing.append("currentStage")
    if intended_use not in {"TRANSACTION","NON_TRANSACTIONAL","LEARNING","UNDECIDED"}: missing.append("intendedUse")
    if not _valid_date(as_of_date): missing.append("asOfDate")
    if _contains(str(payload.get("question","")), "거래 가능으로 확정", "사용완료로 확정", "인증 완료로 확정") and not all(states[x] == "CONFIRMED" for x in ("REGISTERED","VERIFIED","CERTIFIED")):
        verdict, trace = "STOP", ["UNSUPPORTED_OFFICIAL_OR_TRADABILITY_ASSERTION"]
    elif missing or blocked:
        verdict, trace = "REVIEW", ["CONTIGUOUS_EVIDENCE_PREFIX_INCOMPLETE"]
    else:
        verdict, trace = "PROCEED", ["FULL_SEQUENCE_CONFIRMED"]
    answer = f"제공 증거를 앞 단계부터 확인했을 때 다음 확인 지점은 '{blocked}'입니다." if blocked else "연속된 아홉 단계 증거가 확인되었습니다."
    return {"verdict":verdict,"answer":answer,"procedure_path":{"completed_stages":completed,"blocked_stage":blocked,"next_stage":blocked,"stage_evidence":[{"stage":label,"evidenceState":states[code]} for code,label in zip(codes,labels)]},"official_confirmation_questions":([] if not blocked else [{"target":"제도 운영기관 또는 등록부","question":f"{blocked} 완료 근거는 무엇인가요?"}]),"missing_evidence":sorted(set(missing + ([blocked] if blocked else []))),"rule_trace":trace}


def forest_carbon_transaction_readiness(payload: dict[str, Any], graph: KnowledgeGraph) -> SkillResult:
    gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}
    stop_gates = {"G1","G2","G4","G5","G7","G9","G10"}
    results, missing, trace = {}, [], []
    stop_found = review_found = False
    for number in range(1,12):
        gate=f"G{number}"; raw=gates.get(gate,{})
        state=str(raw.get("state","MISSING") if isinstance(raw,dict) else raw).upper()
        if state not in {"PRESENT","UNKNOWN","MISSING","NOT_APPLICABLE_WITH_REASON"}: state="UNKNOWN"
        if state == "PRESENT": decision="PROCEED"
        elif gate in stop_gates or (gate == "G3" and state == "MISSING"):
            decision="STOP"; stop_found=True; missing.append(gate)
        else:
            decision="REVIEW"; review_found=True; missing.append(gate)
        results[gate]={"state":state,"verdict":decision}; trace.append(f"{gate}:{state}->{decision}")
    verdict="STOP" if stop_found else ("REVIEW" if review_found else "PROCEED")
    answers={"STOP":"핵심 증거 게이트가 닫히지 않아 거래 단계로 진행하면 안 됩니다.","REVIEW":"권리·목적·세무·주장 승인 증거를 추가 확인해야 합니다.","PROCEED":"내부 준비도 게이트를 통과했지만 실제 거래 유효성 승인은 아닙니다."}
    return {"verdict":verdict,"answer":answers[verdict],"gate_results":results,"missing_evidence":missing,"human_confirmation_targets":missing,"rule_trace":trace}


HANDLERS: dict[str, Callable[[dict[str, Any], KnowledgeGraph], SkillResult]] = {
    "esg_carbon_action_path_v1": esg_carbon_action_path,
    "scope_activity_classification_v1": scope_activity_classification,
    "carbon_market_unit_comparison_v1": carbon_market_unit_comparison,
    "forest_esg_impact_mapping_v1": forest_esg_impact_mapping,
    "forest_carbon_procedure_guidance_v1": forest_carbon_procedure_guidance,
    "forest_carbon_transaction_readiness_v1": forest_carbon_transaction_readiness,
}
