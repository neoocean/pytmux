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

// ── vault 마스터 키(§5.3a) ────────────────────────────────────────────────
// 키의 주인은 **머신이 아니라 vault(패스키)** 다. 첫 등록 때 브라우저가 만들어
// 패스키 PRF 로 감싸 서버에 두고, 머신 등록 때 페어링 코드로 한 번 더 감싸 넘긴다.
// 그래서 같은 패스키만 있으면 어느 브라우저·몇 대든 invite 없이 붙는다.
//
// 주의: 서버는 감싼 것만 갖는다(열쇠는 인증기 안). PRF 를 지원하지 않는 인증기에서는
// 자동 배포가 불가능하므로 **그 사실을 화면에 즉시 알리고** 복구 코드 경로로 물러선다.
const PRF_SALT = new TextEncoder().encode("pytmux-sync/vault-key/v1");
let VAULT_KEY = null;            // Uint8Array(32) — 이 탭 메모리에만 둔다
let HAS_VAULT = false;           // 서버에 vault 가 이미 있는가(고아 패스키 방지용)

const rawKey = (bits) => new Uint8Array(bits);

async function hkdf(ikm, saltStr, infoStr) {
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt: enc.encode(saltStr), info: enc.encode(infoStr) },
    k, 256);
}

async function aesEncrypt(keyBits, plain) {
  const key = await crypto.subtle.importKey("raw", keyBits, "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain);
  return { iv, ct: new Uint8Array(ct) };
}

async function aesDecrypt(keyBits, iv, ct) {
  const key = await crypto.subtle.importKey("raw", keyBits, "AES-GCM", false, ["decrypt"]);
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct));
}

// 패스키 PRF 결과 → KEK. 인증기가 PRF 를 지원하지 않으면 null.
function prfBits(cred) {
  const ext = cred.getClientExtensionResults ? cred.getClientExtensionResults() : {};
  const r = ext && ext.prf && ext.prf.results && ext.prf.results.first;
  return r ? new Uint8Array(r) : null;
}

// ── 암호구절 폴백(인증기가 PRF 를 지원하지 않을 때) ──────────────────────
// PRF 가 없으면 패스키만으로는 키를 감쌀 수 없다. 그렇다고 서버에 평문으로 두면
// 이 설계의 존재 이유가 사라지므로, **사용자 암호구절**에서 KEK 를 유도해 감싼다.
// 서버는 여전히 암호문만 갖고, 암호구절은 브라우저 밖으로 나가지 않는다.
//
// 주의: 감싼 블롭이 서버에 있으므로 **약한 암호는 오프라인 대입에 취약**하다.
// PBKDF2 반복을 크게 잡고(60만), 화면에서도 길게 잡으라고 말한다.
const PBKDF2_ITER = 600000;

async function passKek(pass, salt) {
  const base = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveBits"]);
  return crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: PBKDF2_ITER }, base, 256);
}

function askPassphrase(why) {
  // window.prompt 대신 인라인 입력 — 붙여넣기·비밀번호 관리자와 잘 맞는다.
  return new Promise((resolve) => {
    $("pass-why").textContent = why;
    $("sec-pass").hidden = false;
    const input = $("pass-input");
    input.value = "";
    input.focus();
    const done = () => {
      const v = input.value;
      if (!v) return;
      $("btn-pass").removeEventListener("click", done);
      input.removeEventListener("keydown", onKey);
      $("sec-pass").hidden = true;
      input.value = "";
      resolve(v);
    };
    const onKey = (e) => { if (e.key === "Enter") done(); };
    $("btn-pass").addEventListener("click", done);
    input.addEventListener("keydown", onKey);
  });
}

async function storeVaultKeyWithPassphrase() {
  const pass = await askPassphrase(
    "이 인증기는 키 자동 배포(PRF)를 지원하지 않습니다 — 키를 감쌀 암호구절을 정하세요.");
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const kek = await passKek(pass, salt);
  const { iv, ct } = await aesEncrypt(kek, VAULT_KEY);
  const wrapped = new Uint8Array(iv.length + ct.length);
  wrapped.set(iv, 0); wrapped.set(ct, iv.length);
  const meta = "pbkdf2-v1:" + bufToB64u(salt) + ":" + PBKDF2_ITER;
  const r = await fetch("/v1/vault/key", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wrapped: bufToB64u(wrapped), meta }),
  });
  return r.ok;
}

async function loadVaultKeyWithPassphrase(j) {
  const parts = (j.meta || "").split(":");
  if (parts[0] !== "pbkdf2-v1") return false;
  const salt = b64uToBuf(parts[1]);
  const iter = parseInt(parts[2], 10) || PBKDF2_ITER;
  const wrapped = b64uToBuf(j.wrapped);
  for (let tries = 0; tries < 3; tries++) {
    const pass = await askPassphrase(
      tries ? "암호가 맞지 않습니다 — 다시 입력하세요." : "키를 열 암호구절을 입력하세요.");
    const base = await crypto.subtle.importKey(
      "raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveBits"]);
    const kek = await crypto.subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, base, 256);
    try {
      VAULT_KEY = await aesDecrypt(kek, wrapped.slice(0, 12), wrapped.slice(12));
      return true;
    } catch (e) {
      VAULT_KEY = null;      // 복호 실패 = 암호 불일치(AEAD 가 잡아 준다)
    }
  }
  say("암호구절을 3회 틀렸습니다 — 새로고침 후 다시 시도하세요.", true);
  return false;
}

async function storeVaultKey(prf, overwrite) {
  const kek = await hkdf(prf, "pytmux-sync/vault-kek", "wrap");
  const { iv, ct } = await aesEncrypt(kek, VAULT_KEY);
  const wrapped = new Uint8Array(iv.length + ct.length);
  wrapped.set(iv, 0); wrapped.set(ct, iv.length);
  const r = await fetch("/v1/vault/key", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    // overwrite 는 **같은 키를 다시 감쌀 때만** 쓴다(암호구절 → PRF 승격).
    // 다른 키로 덮어쓰면 서버의 기존 레코드가 복호 불능이 되므로 기본은 거부다.
    body: JSON.stringify({ wrapped: bufToB64u(wrapped), meta: "prf-v1",
                           overwrite: !!overwrite }),
  });
  return r.ok;                      // 409 = 이미 있음(덮어쓰지 않는다)
}

async function loadVaultKey(prf) {
  const r = await fetch("/v1/vault/key", { credentials: "same-origin" });
  if (!r.ok) return false;
  const j = await r.json();
  const wrapped = b64uToBuf(j.wrapped);
  const kek = await hkdf(prf, "pytmux-sync/vault-kek", "wrap");
  VAULT_KEY = await aesDecrypt(kek, wrapped.slice(0, 12), wrapped.slice(12));
  return true;
}

async function register() {
  // **만들기 전에** 추가가 가능한 상태인지 확인한다. 로그아웃 상태에서 누르면 서버는
  // "새 vault 생성"으로 보고 거부하는데, 그때 인증기에는 이미 패스키가 만들어져
  // **고아 패스키**가 남는다(그 뒤 로그인에서 그걸 골라 401 이 난다 — 실기동 제보).
  let authed = false;
  try {
    const r = await fetch("/v1/session", { credentials: "same-origin" });
    authed = r.ok ? (await r.json()).authenticated : false;
  } catch (e) { /* 확인 실패는 서버가 판정하게 둔다 */ }
  if (!authed && HAS_VAULT) {
    say("이미 vault 가 있습니다 — 먼저 '패스키로 로그인' 한 뒤 눌러야 같은 vault 에 "
        + "패스키가 추가됩니다(지금 만들면 서버가 모르는 패스키가 남습니다).", true);
    return;
  }
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
      // 이 패스키로 vault 키를 감쌀 수 있게 PRF 를 요청한다(지원 안 하면 무시된다).
      extensions: { prf: { eval: { first: PRF_SALT } } },
    },
  });
  const j = await post("/v1/enroll/verify", {
    challenge: o.challenge,
    attestationObject: bufToB64u(cred.response.attestationObject),
    clientDataJSON: bufToB64u(cred.response.clientDataJSON),
  });
  // PRF 는 **크리덴셜 생성 시** 켜진다 — 그래서 지원 여부를 여기서 바로 알려 준다.
  // (로그인해 봐야 아는 구조라 사용자가 원인을 못 짚었다.)
  const ext = cred.getClientExtensionResults ? cred.getClientExtensionResults() : {};
  const prfOn = !!(ext && ext.prf && ext.prf.enabled);
  if (j && j.recovery_code) showRecovery(j.recovery_code);
  say(prfOn
      ? "패스키를 등록했습니다 — 이 패스키는 키 자동 배포(PRF)를 지원합니다."
      : "패스키를 등록했습니다 — 이 패스키는 PRF 미지원이라 암호구절이 필요합니다.");
  // 새 vault 면 키를 만들어 패스키로 감싸 둔다. 등록 응답 직후의 create 결과에는 PRF
  // 값이 없는 인증기가 많아, **로그인 assertion 한 번**으로 PRF 를 받아 온다.
  await ensureVaultKey();
  await afterLogin();
}

async function ensureVaultKey() {
  // 이미 서버에 감싼 키가 있으면 그것을 풀고, 없으면 새로 만들어 올린다.
  // 어느 쪽이든 **패스키 assertion 한 번**이 필요하다(PRF 는 그때 나온다).
  const o = await post("/v1/auth/options");
  let cred;
  try {
    cred = await navigator.credentials.get({
      publicKey: {
        challenge: b64uToBuf(o.challenge),
        rpId: o.rpId,
        allowCredentials: [],
        userVerification: o.userVerification,
        timeout: o.timeout,
        extensions: { prf: { eval: { first: PRF_SALT } } },
      },
    });
  } catch (e) {
    // 사용자가 인증을 취소했거나 실패했다 — **성공한 척하지 않는다**.
    say(explain(e), true);
    return false;
  }
  // 이 assertion 으로 로그인도 갱신해 둔다(세션 유지).
  await post("/v1/auth/verify", {
    challenge: o.challenge,
    credentialId: bufToB64u(cred.rawId),
    authenticatorData: bufToB64u(cred.response.authenticatorData),
    clientDataJSON: bufToB64u(cred.response.clientDataJSON),
    signature: bufToB64u(cred.response.signature),
  });
  return await unlockOrCreateKey(prfBits(cred));
}

// PRF 가 있으면 패스키로, 없으면 암호구절로 — 어느 쪽이든 **서버는 암호문만** 갖는다.
async function unlockOrCreateKey(prf) {
  // **PRF 가 있으면 언제나 그쪽**이다 — 사용자가 고를 필요도, 암호구절을 칠 필요도
  // 없다. 암호구절은 PRF 를 못 쓰는 인증기에서만 나타나는 폴백이다.
  const r = await fetch("/v1/vault/key", { credentials: "same-origin" });
  const existing = r.ok ? await r.json() : null;
  if (existing) {
    const byPass = (existing.meta || "").startsWith("pbkdf2-v1");
    if (!byPass && !prf) {
      // 키는 PRF 로 감싸져 있는데 지금 패스키에는 PRF 가 없다 — 그 패스키로
      // 로그인하거나, PRF 되는 패스키를 새로 만들어야 한다.
      say("이 키는 PRF 패스키로 감싸져 있습니다 — 그 패스키로 로그인하거나 " +
          "'새 패스키 만들기'로 PRF 패스키를 추가하세요.", true);
      return false;
    }
    const ok = byPass ? await loadVaultKeyWithPassphrase(existing)
                      : await loadVaultKey(prf);
    if (ok && byPass && prf) {
      // 암호구절로 열었는데 이제 PRF 를 쓸 수 있다 → **PRF 로 갈아 감싼다**.
      // 다음부터는 암호구절을 묻지 않는다(사용자가 아무것도 안 해도 좋아진다).
      if (await storeVaultKey(prf, true)) {
        say("이제 이 패스키로 자동 잠금 해제됩니다 — 암호구절은 더 묻지 않습니다.");
      }
    }
    return ok;
  }
  VAULT_KEY = crypto.getRandomValues(new Uint8Array(32));
  const stored = prf ? await storeVaultKey(prf)
                     : await storeVaultKeyWithPassphrase();
  if (!stored) {
    VAULT_KEY = null;
    say("키 보관에 실패했습니다 — 이미 다른 키가 있는지 확인하세요.", true);
    return false;
  }
  if (!prf) {
    say("암호구절로 키를 보관했습니다 — 'PRF 지원' 패스키를 만들면 다음부터 " +
        "암호구절 없이 열립니다.");
  }
  return true;
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
      extensions: { prf: { eval: { first: PRF_SALT } } },
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
  await unlockOrCreateKey(prfBits(cred));
  await afterLogin();
}

// 사용자가 코드를 손으로 옮겨 적다 틀리지 않게 **명령 전체**를 만들어 보여주고
// 복사까지 붙인다(코드만 보여주면 앞부분을 기억해서 쳐야 한다).
function enrollCommand(code) {
  return ":claude-token-sync enroll " + code;
}

// 코드는 **브라우저가** 만든다(§5.3a). 서버가 만들면 그 순간 서버도 코드를 알아
// 감싼 키를 풀 수 있다 — 서버에는 해시만 올린다.
function newPairingCode() {
  const raw = crypto.getRandomValues(new Uint8Array(16));   // 128비트
  const hex = [...raw].map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
  return hex.match(/.{1,4}/g).join("-");
}

async function codeHash(code) {
  const norm = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const d = await crypto.subtle.digest("SHA-256",
                                       new TextEncoder().encode("pairing|" + norm));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 복구 코드는 **딱 한 번** 보여 준다 — 서버는 해시만 갖고 있어 다시 못 보여 준다.
function showRecovery(code) {
  $("recovery-code").textContent = code;
  $("recovery-show").hidden = false;
  $("recovery-note").hidden = false;
  $("btn-recovery-copy").textContent = "복사";
}

// 복구 화면은 **단독**으로 띄운다 — 다른 버튼이 함께 보이면 잠긴 사용자가 무엇을
// 눌러야 할지 또 헷갈린다(이번 잠김에서 실제로 그랬다).
function showRecoverUI(on) {
  $("recover-box").hidden = !on;
  $("passkey-actions").hidden = on;
  $("passkey-hint").hidden = on;
  if (on) $("recover-input").focus();
  else $("recover-input").value = "";
}

async function submitRecover() {
  const code = $("recover-input").value.trim();
  if (!code) return;
  const j = await post("/v1/recover", { code });
  showRecoverUI(false);
  say("복구했습니다 — 새 패스키를 만들어 두세요.");
  if (j.recovery_code) showRecovery(j.recovery_code);
  await restoreSession();
}

async function pair() {
  if (!VAULT_KEY) {
    // 새로고침으로 키가 잠겼다면 여기서 한 번 푼다(지문/얼굴 1회).
    await ensureVaultKey();
  }
  if (!VAULT_KEY) {
    // 서버에 감싼 키가 있는데 못 열었다면(취소·오답) **코드를 만들지 않는다** —
    // 키 없는 코드를 주면 머신은 등록되지만 통계가 영영 안 합쳐진다(조용한 저하).
    let hasKey = false;
    try {
      const r = await fetch("/v1/session", { credentials: "same-origin" });
      hasKey = r.ok ? !!(await r.json()).has_key : false;
    } catch (e) { /* 알 수 없으면 아래에서 보수적으로 처리 */ }
    if (hasKey) {
      say("키가 잠겨 있어 코드를 만들지 않았습니다 — 인증을 완료한 뒤 다시 누르세요.",
          true);
      return;
    }
    // 아직 vault 키 자체가 없는 상태(폴백 운용) — 키 없는 코드임을 분명히 알린다.
    say("이 vault 에는 아직 키가 없습니다 — 코드에 키가 실리지 않으니 첫 머신에서 "
        + "invite/adopt 로 키를 맞춰야 합니다.", true);
  }
  const code = newPairingCode();
  const payload = { code_h: await codeHash(code) };
  if (VAULT_KEY) {
    // 코드에서 파생한 키로 마스터 키를 감싼다 — 머신이 그 코드로 푼다.
    // 파이썬 syncrypto.pair_key 와 **같은 HKDF 파라미터**여야 한다(salt/info 고정).
    const norm = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
    const pk = await hkdf(new TextEncoder().encode(norm),
                          "pytmux-sync/pair", "pair-key");
    const { iv, ct } = await aesEncrypt(pk, VAULT_KEY);
    payload.key_ct = bufToB64u(ct);
    payload.key_nonce = bufToB64u(iv);
  }
  const before = await deviceIds();
  const j = await post("/v1/pairing", payload);
  $("cmd").textContent = enrollCommand(j.code || code);
  // 이 코드가 쓰이면 목록을 바로 갱신한다(만료되면 멈춘다).
  watchForEnrollment(before, (j.expires_in || 600) * 1000);
  const btn = $("btn-copy");
  btn.textContent = "복사";
  btn.classList.remove("done");
  $("copy-msg").textContent = "";
  // 코드가 실제로 생긴 **뒤에만** 명령칸을 드러낸다(빈 칸을 미리 보여주지 않는다).
  $("pair-result").hidden = false;
  say(VAULT_KEY
      ? "이 명령은 10분 뒤 만료되고 한 번만 쓸 수 있습니다(키가 함께 전달됩니다)."
      : "이 명령은 10분 뒤 만료되고 한 번만 쓸 수 있습니다. " +
        "이 인증기는 키 자동 전달을 지원하지 않아, 첫 머신에서 invite 로 키를 옮겨야 합니다.");
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

// 코드가 쓰이는 순간을 **화면이 알아채게** 한다 — 머신에서 등록해 놓고 브라우저를
// 새로고침해야 확인되는 것은 불친절하고, 실제로 성공했는지도 알기 어렵다.
// 서버 푸시(SSE/WebSocket)를 들이기엔 과한 규모라 짧은 폴링으로 끝낸다.
let WATCH_TIMER = null;

function stopWatch() {
  if (WATCH_TIMER) { clearInterval(WATCH_TIMER); WATCH_TIMER = null; }
}

async function deviceIds() {
  const r = await fetch("/v1/devices", { credentials: "same-origin" });
  if (!r.ok) return null;
  const { devices } = await r.json();
  return devices;
}

function watchForEnrollment(before, expiresMs) {
  stopWatch();
  const known = new Set((before || []).map((d) => d.device_id));
  const deadline = Date.now() + expiresMs;
  WATCH_TIMER = setInterval(async () => {
    if (Date.now() > deadline) {            // 코드 만료 — 조용히 멈춘다
      stopWatch();
      return;
    }
    let now;
    try { now = await deviceIds(); } catch (e) { return; }
    if (!now) { stopWatch(); return; }      // 세션이 끊겼다
    const fresh = now.filter((d) => !known.has(d.device_id));
    if (!fresh.length) return;
    stopWatch();
    // 코드는 1회용이라 이미 소모됐다 — 화면에 남겨 두면 다시 쓸 수 있을 것처럼 보인다.
    $("pair-result").hidden = true;
    await loadDevices();
    say("머신이 등록되었습니다: " + (fresh[0].label || "(이름 없음)"));
  }, 3000);
}

async function loadDevices() {
  const r = await fetch("/v1/devices", { credentials: "same-origin" });
  if (!r.ok) return;
  const { devices } = await r.json();
  const tb = $("devices").querySelector("tbody");
  tb.textContent = "";
  for (const d of devices) {
    const tr = document.createElement("tr");
    // 서버가 살아 있는 기기만 준다(폐기 = 삭제) — 목록에 잔해가 남지 않는다.
    const name = document.createElement("td");
    name.textContent = d.label || "(이름 없음)";
    const act = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = "폐기";
    btn.addEventListener("click", async () => {
      await fetch("/v1/devices/" + encodeURIComponent(d.device_id),
                  { method: "DELETE", credentials: "same-origin" });
      await loadDevices();
    });
    act.appendChild(btn);
    tr.append(name, act);
    tb.appendChild(tr);
  }
  $("sec-devices").hidden = devices.length === 0;
}

function setLoggedIn(on) {
  // 로그인 상태에서 '패스키로 로그인' 은 할 일이 없다 — 눌러 봐야 같은 자리에
  // 머문다. 지금 할 수 있는 것(패스키 추가·로그아웃)만 남긴다.
  $("btn-login").hidden = on;
  $("btn-logout").hidden = !on;
  $("passkey-hint").textContent = on
    ? "다른 기기의 브라우저에서도 같은 vault 에 패스키를 추가할 수 있습니다 — "
      + "'새 패스키 만들기' 를 누르세요."
    : "처음이면 '새 패스키 만들기' 로 vault 가 생깁니다. 다른 기기의 브라우저에서도 "
      + "같은 vault 에 패스키를 추가할 수 있습니다(로그인 후 다시 누르세요).";
}

async function afterLogin() {
  showRecoverUI(false);
  $("sec-pair").hidden = false;
  $("pair-result").hidden = true;      // 로그인 직후엔 지난 코드의 흔적을 남기지 않는다
  setLoggedIn(true);
  await loadDevices();
}

// 세션을 서버에서 지운다 — 쿠키 만료만 기다리면 훔친 쿠키가 TTL 동안 살아 있다.
async function logout() {
  stopWatch();
  await post("/v1/logout");
  $("sec-pair").hidden = true;
  $("sec-devices").hidden = true;
  $("pair-result").hidden = true;
  setLoggedIn(false);
  say("로그아웃했습니다.");
}

// 화면 문구는 **전부 한국어**로 낸다. 두 출처를 모두 옮긴다:
//  ① 브라우저 예외(WebAuthn DOMException 등) — 영문 원문이 그대로 나가면 사용자가
//     무엇이 잘못됐는지 알 수 없다(제보: "The request is not allowed by the user
//     agent…" 가 그대로 노출).
//  ② 서버 오류 코드 — 서버는 사유를 일반화해 돌려주므로(정보 누출 방지) 맥락은
//     화면이 붙인다.
const BROWSER_ERRORS = {
  NotAllowedError: "인증이 취소됐거나 시간이 초과됐습니다 — 다시 시도하세요.",
  AbortError: "인증이 중단됐습니다 — 다시 시도하세요.",
  InvalidStateError: "이 인증기에는 이미 이 서버의 패스키가 있습니다 — "
                   + "'패스키로 로그인' 을 쓰세요.",
  NotSupportedError: "이 브라우저·인증기가 지원하지 않는 방식입니다.",
  ConstraintError: "인증기가 요구 조건을 만족하지 못했습니다"
                 + "(지문·PIN 등 사용자 확인이 필요합니다).",
  SecurityError: "보안 컨텍스트 오류 — 이 주소(HTTPS)에서 다시 시도하세요.",
  NetworkError: "네트워크 오류 — 연결을 확인하세요.",
  UnknownError: "인증기에서 알 수 없는 오류가 났습니다 — 다시 시도하세요.",
};

const SERVER_ERRORS = {
  slow_down: "요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.",
  limit: "상한을 넘었습니다 — 쓰지 않는 기기를 폐기한 뒤 다시 시도하세요.",
  quota: "서버 저장 용량이 가득 찼습니다.",
  too_large: "요청이 너무 큽니다.",
  bad_request: "요청 형식이 올바르지 않습니다.",
  not_found: "없는 항목입니다.",
  internal: "서버 오류가 났습니다 — 잠시 후 다시 시도하세요.",
  no_key: "이 vault 에는 아직 열쇠가 없습니다.",
};

function explain(err, what) {
  const name = err && err.name;
  if (name && BROWSER_ERRORS[name]) return BROWSER_ERRORS[name];
  const msg = String((err && err.message) || err || "");
  if (msg === "unauthorized") {
    if (what === "register") {
      return "새 vault 생성은 잠겨 있습니다 — 먼저 '패스키로 로그인' 한 뒤 다시 누르면 "
           + "같은 vault 에 이 패스키가 추가됩니다.";
    }
    if (what === "recover") {
      return "복구 코드가 맞지 않습니다 — 이미 쓴 코드이거나 오타입니다.";
    }
    if (what === "login") {
      return "로그인 실패 — 서버가 모르는 패스키일 수 있습니다(로그아웃 상태에서 만든 것). "
           + "인증기 목록에서 다른 패스키를 고르거나, 그 패스키를 지우세요.";
    }
    return "권한이 없습니다 — 로그인 상태를 확인하세요.";
  }
  if (SERVER_ERRORS[msg]) return SERVER_ERRORS[msg];
  return msg || "알 수 없는 오류가 났습니다.";
}

function wrap(fn, what) {
  return () => fn().catch((e) => say(explain(e, what), true));
}

// 새로고침 후에도 로그인 상태를 되찾는다(제보). 쿠키는 살아 있었는데 페이지가
// **묻지 않아** 로그아웃처럼 보였다. 키(VAULT_KEY)는 메모리에만 두므로 새로고침하면
// 잠긴 상태가 되고, 실제로 키가 필요한 순간(코드 발급)에만 지문을 한 번 더 받는다
// — 키를 브라우저 저장소에 남기면 XSS 한 방에 통째로 새는 것과 바꾸는 셈이라 안 한다.
async function restoreSession() {
  let j;
  try {
    const r = await fetch("/v1/session", { credentials: "same-origin" });
    if (!r.ok) return;
    j = await r.json();
  } catch (e) {
    return;
  }
  HAS_VAULT = !!j.vault_exists;
  if (!j.authenticated) return;
  await afterLogin();
  say(j.has_key
      ? "로그인 상태입니다(키는 잠겨 있음 — 코드 만들 때 한 번 확인합니다)."
      : "로그인 상태입니다.");
}

$("btn-register").addEventListener("click", wrap(register, "register"));
$("btn-login").addEventListener("click", wrap(login, "login"));
$("btn-pair").addEventListener("click", wrap(pair));
$("btn-copy").addEventListener("click", wrap(copyCommand));
$("btn-logout").addEventListener("click", wrap(logout));
$("btn-recover").addEventListener("click", () => showRecoverUI(true));
$("btn-recover-cancel").addEventListener("click", () => {
  showRecoverUI(false);
  say("");
});
$("btn-recover-go").addEventListener("click", wrap(submitRecover, "recover"));
$("recover-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") wrap(submitRecover, "recover")();
  if (e.key === "Escape") showRecoverUI(false);
});
$("btn-recovery-copy").addEventListener("click", wrap(async () => {
  const t = $("recovery-code").textContent.trim();
  if (!t) return;
  try { await navigator.clipboard.writeText(t); $("btn-recovery-copy").textContent = "복사됨"; }
  catch (e) { say("복사가 막혔습니다 — 직접 선택해 복사하세요.", true); }
}));

restoreSession().catch(() => {});
