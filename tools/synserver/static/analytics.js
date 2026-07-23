"use strict";
// Matomo(matomo.woojinkim.org, siteId 14) — 등록 페이지 이용 흐름 계측.
//
// **왜 인라인이 아니라 별도 파일인가**: 표준 Matomo 스니펫은 인라인 `<script>` 라
// CSP 에 `script-src 'unsafe-inline'` 을 열어야 한다. 이 페이지는 패스키·1회용
// 페어링 코드를 다루므로 인라인을 여는 대가가 너무 크다. 같은 코드를 `'self'` 파일로
// 옮기면 CSP 는 `script-src 'self' https://matomo.woojinkim.org` 로 끝난다.
//
// **무엇을 보내지 않는가**(이 서버의 요점을 계측이 깨면 안 된다):
//  · 페어링 코드·복구 코드·패스키 ID·vault ID·기기 ID·라벨 — **한 톨도 안 보낸다**.
//  · 오류도 원문이 아니라 **미리 정한 슬러그**만(서버 문구가 그대로 실리지 않게).
//  · 그래서 `track()` 은 경로를 화이트리스트 정규식으로 **거른다** — 실수로 값을
//    끼워 넣어도 나가지 않고 `/app/_rejected` 로 떨어진다(사고를 조용히 넘기지 않음).
var _paq = window._paq = window._paq || [];
/* tracker methods like "setCustomDimension" should be called before "trackPageView" */
_paq.push(['trackPageView']);
_paq.push(['enableLinkTracking']);
(function() {
  var u = "//matomo.woojinkim.org/";
  _paq.push(['setTrackerUrl', u + 'matomo.php']);
  _paq.push(['setSiteId', '14']);
  var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];
  g.async = true; g.src = u + 'matomo.js'; s.parentNode.insertBefore(g, s);
})();

(function () {
  // 가상 페이지 경로는 이 모양만 통과한다 — 소문자·숫자·`-`·`/` 뿐이라
  // 코드(대문자 HEX)·해시·라벨은 형태상 통과할 수 없다.
  var SAFE = /^\/app\/[a-z0-9][a-z0-9/-]{0,80}$/;

  // 계측이 페이지를 망가뜨리는 일은 없어야 한다(차단기·오프라인·Matomo 장애).
  // 그래서 전 구간 try/catch 이고, 실패해도 호출자는 아무것도 모른다.
  window.pxTrack = function (path, title) {
    try {
      if (!SAFE.test(path)) path = "/app/_rejected";
      window._paq.push(["setCustomUrl", location.origin + path]);
      window._paq.push(["setDocumentTitle", title || path]);
      window._paq.push(["trackPageView"]);
    } catch (e) { /* 계측 실패는 조용히 넘긴다 */ }
  };
})();
