"use strict";
// pytmux 동기화 서버 등록 페이지 — 의존성 없음(CSP: default-src 'none').
// 하는 일은 셋뿐이다: 패스키 등록/로그인, 1회용 페어링 코드 발급, 기기 목록·폐기.
// **키 자체(vault 마스터 키)는 이 페이지를 지나가지 않는다** — 머신끼리 초대 코드로
// 직접 옮긴다(설계 §5.3). 여기서 다루는 것은 서버 접근 권한뿐이다.

const $ = (id) => document.getElementById(id);
const b64uToBuf = (s) => {
  const t = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(t + "=".repeat((4 - (t.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};
const bufToB64u = (b) =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

function say(text, isErr) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = isErr ? "err" : "";
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

async function register() {
  say("인증기를 확인하세요…");
  const o = await post("/v1/enroll/options");
  const cred = await navigator.credentials.create({
    publicKey: {
      challenge: b64uToBuf(o.challenge),
      rp: o.rp,
      user: { id: b64uToBuf(o.user.id), name: o.user.name,
              displayName: o.user.displayName },
      pubKeyCredParams: o.pubKeyCredParams,
      authenticatorSelection: o.authenticatorSelection,
      attestation: o.attestation,
      timeout: o.timeout,
    },
  });
  await post("/v1/enroll/verify", {
    challenge: o.challenge,
    attestationObject: bufToB64u(cred.response.attestationObject),
    clientDataJSON: bufToB64u(cred.response.clientDataJSON),
  });
  say("패스키를 등록했습니다.");
  await afterLogin();
}

async function login() {
  say("패스키를 확인하세요…");
  const o = await post("/v1/auth/options");
  const cred = await navigator.credentials.get({
    publicKey: {
      challenge: b64uToBuf(o.challenge),
      rpId: o.rpId,
      allowCredentials: [],          // discoverable — 아이디 입력 없음
      userVerification: o.userVerification,
      timeout: o.timeout,
    },
  });
  await post("/v1/auth/verify", {
    challenge: o.challenge,
    credentialId: bufToB64u(cred.rawId),
    authenticatorData: bufToB64u(cred.response.authenticatorData),
    clientDataJSON: bufToB64u(cred.response.clientDataJSON),
    signature: bufToB64u(cred.response.signature),
  });
  say("로그인했습니다.");
  await afterLogin();
}

// 사용자가 코드를 손으로 옮겨 적다 틀리지 않게 **명령 전체**를 만들어 보여주고
// 복사까지 붙인다(코드만 보여주면 앞부분을 기억해서 쳐야 한다).
function enrollCommand(code) {
  return ":token-sync enroll " + code;
}

async function pair() {
  const j = await post("/v1/pairing");
  $("cmd").textContent = enrollCommand(j.code);
  const btn = $("btn-copy");
  btn.textContent = "복사";
  btn.classList.remove("done");
  $("copy-msg").textContent = "";
  // 코드가 실제로 생긴 **뒤에만** 명령칸을 드러낸다(빈 칸을 미리 보여주지 않는다).
  $("pair-result").hidden = false;
  say("이 명령은 10분 뒤 만료되고 한 번만 쓸 수 있습니다.");
}

async function copyCommand() {
  const text = $("cmd").textContent.trim();
  if (!text) {
    $("copy-msg").textContent = "먼저 코드를 만드세요.";
    return;
  }
  let ok = false;
  try {
    // HTTPS(보안 컨텍스트)에서만 쓸 수 있다 — 실패하면 아래 폴백.
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    document.body.removeChild(ta);
  }
  const btn = $("btn-copy");
  btn.textContent = ok ? "복사됨" : "복사 실패";
  btn.classList.toggle("done", ok);
  $("copy-msg").textContent = ok
    ? "붙일 머신의 pytmux 에서 붙여넣으세요."
    : "복사가 막혔습니다 — 명령을 직접 선택해 복사하세요.";
}

async function loadDevices() {
  const r = await fetch("/v1/devices", { credentials: "same-origin" });
  if (!r.ok) return;
  const { devices } = await r.json();
  const tb = $("devices").querySelector("tbody");
  tb.textContent = "";
  for (const d of devices) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = (d.label || "(이름 없음)") + (d.revoked ? " — 폐기됨" : "");
    const act = document.createElement("td");
    if (!d.revoked) {
      const btn = document.createElement("button");
      btn.textContent = "폐기";
      btn.addEventListener("click", async () => {
        await fetch("/v1/devices/" + encodeURIComponent(d.device_id),
                    { method: "DELETE", credentials: "same-origin" });
        await loadDevices();
      });
      act.appendChild(btn);
    }
    tr.append(name, act);
    tb.appendChild(tr);
  }
  $("sec-devices").hidden = devices.length === 0;
}

async function afterLogin() {
  $("sec-pair").hidden = false;
  $("pair-result").hidden = true;      // 로그인 직후엔 지난 코드의 흔적을 남기지 않는다
  await loadDevices();
}

function wrap(fn) {
  return () => fn().catch((e) => say(String(e.message || e), true));
}

$("btn-register").addEventListener("click", wrap(register));
$("btn-login").addEventListener("click", wrap(login));
$("btn-pair").addEventListener("click", wrap(pair));
$("btn-copy").addEventListener("click", wrap(copyCommand));
