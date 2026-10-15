// 일회용 복구 링크 확인 페이지(pytmux-34 #2).
// 토큰은 URL 조각(#t=…, 서버·프록시 로그에 안 남는다) 또는 옛 형식 ?t=… 에서 읽고,
// 읽자마자 주소창에서 지운다. 서버에는 **버튼을 눌렀을 때만** POST 본문으로 보낸다 —
// GET 으로 소모하던 예전 방식은 링크 미리보기 봇이 토큰을 먼저 써 버렸다.
(function () {
  "use strict";
  function readToken() {
    var h = (location.hash || "").replace(/^#/, "");
    var q = (location.search || "").replace(/^\?/, "");
    var pick = function (s) {
      var parts = s.split("&");
      for (var i = 0; i < parts.length; i++) {
        var kv = parts[i].split("=");
        if (kv[0] === "t" && kv.length > 1) {
          try { return decodeURIComponent(kv.slice(1).join("=")); } catch (e) { return ""; }
        }
      }
      return "";
    };
    return pick(h) || pick(q);
  }
  var token = readToken();
  try { history.replaceState(null, "", "/v1/recover"); } catch (e) { /* 무시 */ }
  var btn = document.getElementById("btn-recover-link");
  var msg = document.getElementById("recover-link-msg");
  if (!token) {
    btn.disabled = true;
    msg.textContent = "링크에 토큰이 없습니다. 운영자에게 받은 링크 전체를 여세요.";
    return;
  }
  btn.addEventListener("click", function () {
    btn.disabled = true;
    msg.textContent = "확인 중…";
    fetch("/v1/recover-link", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({t: token})
    }).then(function (r) {
      if (r.ok) { location.replace("/"); return; }
      msg.textContent = r.status === 429
        ? "요청이 너무 많습니다. 잠시 뒤 다시 누르세요."
        : "링크가 맞지 않거나 이미 쓰였습니다. 운영자에게 새 링크를 받으세요.";
      btn.disabled = r.status !== 429;
    }, function () {
      msg.textContent = "서버에 닿지 못했습니다.";
      btn.disabled = false;
    });
  });
})();
