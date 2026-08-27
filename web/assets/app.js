const $ = (selector) => document.querySelector(selector);
const form = $("#questionForm");
const input = $("#questionInput");
const conversation = $("#conversation");
const traceList = $("#traceList");
const sendButton = $("#sendButton");
const resetButton = $("#resetButton");
const statusText = $("#statusText");
const runStatus = $("#runStatus");
const rawResult = $("#rawResult");
let running = false;

const eventLabels = {
  agent_started: ["Agent run 시작", "질문에서 anchor 후보를 찾고 로컬 모델 경계를 기록했습니다."],
  llm_turn: ["Local Qwen 판단", "관찰 결과를 바탕으로 다음 행동을 선택했습니다."],
  tool_action: ["도구 행동", "Qwen이 선택한 행동을 실행합니다."],
  observation: ["Observation", "실행 결과가 다음 Qwen turn의 근거가 됩니다."],
  verification: ["Verifier 판정", "claim·개념·edge·출처 연결을 검사했습니다."],
  direct_answer_rejected: ["직접 답변 차단", "근거 제출 형식이 아니므로 답변을 공개하지 않았습니다."],
  candidate_structured: ["로컬 구조화", "같은 Qwen이 자연어 초안을 근거 claim JSON으로 변환했습니다."],
  model_error: ["모델 오류", "오류를 숨기지 않고 실행을 안전하게 중단합니다."],
  agent_completed: ["Agent run 종료", "최종 실행 상태를 저장했습니다."],
};

function setRunning(value) {
  running = value;
  sendButton.disabled = value;
  input.disabled = value;
  document.querySelectorAll("[data-question]").forEach((button) => button.disabled = value);
}

function addMessage(role, text, result = null) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  if (role === "agent") {
    const title = document.createElement("strong");
    title.textContent = result?.status === "PASS" ? "검증된 답변" : "답변을 공개하지 않았습니다";
    node.append(title);
  }
  const content = document.createElement("span");
  content.textContent = text;
  node.append(content);
  if (result) {
    const meta = document.createElement("div");
    meta.className = "answer-meta";
    [
      `MODEL ${result.model_identity?.model || "unknown"}`,
      `${result.skills_invoked?.length || 0} SKILL RUN`,
      `${result.source_refs?.length || 0} SOURCES`,
      result.local_llm_verified ? "LOOPBACK VERIFIED" : "MODEL UNVERIFIED",
    ].forEach((label) => {
      const badge = document.createElement("span");
      badge.textContent = label;
      meta.append(badge);
    });
    node.append(meta);
  }
  conversation.append(node);
  conversation.scrollTop = conversation.scrollHeight;
}

function summarizeEvent(event) {
  if (event.event_type === "agent_started") return `anchors: ${(event.anchor_candidates || []).join(" · ") || "없음"}`;
  if (event.event_type === "llm_turn") return `선택: ${(event.tool_names || []).join(" · ") || "자연어 초안"} · ${event.metrics?.client_elapsed_ms || 0}ms`;
  if (event.event_type === "tool_action") return `${event.tool_name} ${JSON.stringify(event.arguments || {})}`;
  if (event.event_type === "observation") return `${event.tool_name} → ${event.status}`;
  if (event.event_type === "verification") return `${event.verdict} · 누락 ${(event.missing_requirements || []).length} · 미관찰 인용 ${(event.unsupported_evidence_ids || []).length}`;
  if (event.event_type === "candidate_structured") return `JSON schema adapter · attempt ${event.adapter_attempt || 1}`;
  if (event.event_type === "model_error") return `${event.error_type}: ${event.error_message}`;
  if (event.event_type === "agent_completed") return `${event.status} · ${event.stop_reason}`;
  return event.reason || "실행 상태가 기록되었습니다.";
}

function addTrace(event) {
  const placeholder = traceList.querySelector(".trace-placeholder");
  if (placeholder) placeholder.remove();
  const [title, fallback] = eventLabels[event.event_type] || [event.event_type, ""];
  const item = document.createElement("li");
  item.className = `trace-item ${event.event_type === "tool_action" ? "action" : ""} ${event.event_type === "verification" ? "verify" : ""} ${event.event_type === "model_error" ? "error" : ""}`;
  const strong = document.createElement("strong");
  strong.textContent = `${String(event.sequence || "").padStart(2, "0")} · ${title}`;
  const detail = document.createElement("p");
  detail.textContent = summarizeEvent(event) || fallback;
  item.append(strong, detail);
  traceList.append(item);
  traceList.scrollTop = traceList.scrollHeight;
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health", {cache: "no-store"});
    const health = await response.json();
    const badge = $("#modelBadge");
    badge.textContent = health.model_status === "READY" ? `LOCAL ${health.model.model}` : "MODEL OFFLINE";
    badge.className = `badge ${health.model_status === "READY" ? "ready" : "error"}`;
    $("#graphCount").textContent = `${health.graph.node_count} nodes · ${health.graph.edge_count} edges`;
    $("#skillCount").textContent = `${health.skills.length} executable`;
    statusText.textContent = health.model_status === "READY" ? "로컬 Qwen 준비 완료" : "Ollama와 Qwen 모델을 먼저 실행해 주세요.";
    sendButton.disabled = health.model_status !== "READY";
  } catch (error) {
    $("#modelBadge").textContent = "SERVER OFFLINE";
    $("#modelBadge").className = "badge error";
    statusText.textContent = "로컬 서버에 연결할 수 없습니다.";
    sendButton.disabled = true;
  }
}

async function runQuestion(question) {
  setRunning(true);
  traceList.innerHTML = "";
  rawResult.textContent = "실행 중…";
  runStatus.textContent = "실행 중";
  runStatus.className = "run-status running";
  statusText.textContent = "로컬 Qwen이 Observation을 보고 다음 행동을 선택합니다. 시간이 걸릴 수 있습니다.";
  addMessage("user", question);
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question, userRole: "LEARNER", asOfDate: new Date().toISOString().slice(0, 10)}),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const packet = JSON.parse(line);
        if (packet.kind === "event") addTrace(packet.event);
        if (packet.kind === "result") {
          const result = packet.result;
          rawResult.textContent = JSON.stringify(result, null, 2);
          const answer = result.status === "PASS"
            ? result.answer
            : `검증을 통과하지 못해 답변을 공개하지 않았습니다. 종료 사유: ${result.stop_reason}`;
          addMessage("agent", answer, result);
          runStatus.textContent = result.status;
          runStatus.className = `run-status ${result.status === "PASS" ? "pass" : "stop"}`;
          statusText.textContent = result.status === "PASS" ? "검증된 claim만 답변으로 공개했습니다." : "실행 기록은 보존되었으며 답변은 차단되었습니다.";
        }
        if (packet.kind === "error") throw new Error(packet.error.message);
      }
      if (done) break;
    }
  } catch (error) {
    addMessage("agent", `실행 중 오류가 발생했습니다: ${error.message}`);
    runStatus.textContent = "ERROR";
    runStatus.className = "run-status stop";
    statusText.textContent = "오류가 발생했습니다. 실행 원문을 확인해 주세요.";
  } finally {
    setRunning(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question || running) return;
  input.value = "";
  runQuestion(question);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.question;
    input.focus();
  });
});

resetButton.addEventListener("click", () => {
  if (running) return;
  conversation.innerHTML = '<div class="welcome-card"><strong>새 독립 run을 시작할 준비가 됐습니다.</strong><p>이전 질문은 다음 질문의 모델 입력에 포함되지 않습니다.</p></div>';
  traceList.innerHTML = '<li class="trace-placeholder">질문을 실행하면 Qwen의 행동과 Observation이 순서대로 나타납니다.</li>';
  rawResult.textContent = "아직 실행 결과가 없습니다.";
  runStatus.textContent = "대기";
  runStatus.className = "run-status idle";
  input.focus();
});

loadHealth();
