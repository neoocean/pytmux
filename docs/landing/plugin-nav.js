// pytmux 플러그인 페이지 공용 크롬 — guide-nav.js 와 같은 패턴의 파셜 주입기.
// 개별 플러그인 페이지(guide/plugins/<name>.html)의 <body data-plugin> 으로 현재
// 플러그인을 알아내 목차 강조·이전/다음 페이저를 만든다. 챕터 페이저(guide-nav.js
// 의 CH)와 분리한 이유: 플러그인 13개를 가이드 챕터 목차에 섞으면 목차·페이저가
// 배로 길어진다 — 플러그인끼리만 도는 별도 트랙으로 둔다.
//
// 여기서 innerHTML 에 넣는 모든 문자열은 1st-party 정적 마크업(아래 PL 카탈로그와
// 내비/푸터)뿐이다 — 사용자·네트워크 입력이 닿지 않는다.
(function () {
  var body = document.body;
  var slug = body.getAttribute('data-plugin') || '';

  // 플러그인 순서 = 목차 순서 = 이전/다음 순서(카테고리 묶음). 한 곳에서만 관리.
  var PL = [
    { t: 'claude-code',             n: 'claude-code',             c: 'Claude' },
    { t: 'claude-resume',           n: 'claude-resume',           c: 'Claude' },
    { t: 'claude-prompt-history',   n: 'claude-prompt-history',   c: 'Claude' },
    { t: 'claude-name-sync',        n: 'claude-name-sync',        c: 'Claude' },
    { t: 'claude-token-usage-view', n: 'claude-token-usage-view', c: 'Claude' },
    { t: 'claude-disable-feedback', n: 'claude-disable-feedback', c: 'Claude' },
    { t: 'ncd',                     n: 'ncd',                     c: '탐색' },
    { t: 'mdir',                    n: 'mdir',                    c: '탐색' },
    { t: 'clock',                   n: 'clock',                   c: '오버레이' },
    { t: 'calendar',                n: 'calendar',                c: '오버레이' },
    { t: 'ime-indicator',           n: 'ime-indicator',           c: '입력' },
    { t: 'p4-show-submitted-changelists', n: 'p4changes',         c: 'Perforce' },
    { t: 'rec',                     n: 'rec',                     c: '모니터' }
  ];
  var idx = -1;
  for (var i = 0; i < PL.length; i++) { if (PL[i].t === slug) { idx = i; break; } }

  // ── 상단 내비 (사이트 공통 — guide-nav.js 와 동일) ──
  var nav = document.createElement('nav');
  nav.className = 'nav';
  nav.innerHTML =
    '<div class="nav-inner">' +
    '<a class="brand" href="/" style="text-decoration:none"><span class="prompt">❯</span> pytmux</a>' +
    '<div class="nav-links">' +
    '<a href="/">홈</a>' +
    '<a href="/guide">가이드 개요</a>' +
    '<a href="/guide/plugins">플러그인</a>' +
    '<a href="/changes">최근 수정사항</a>' +
    '<a class="gh" href="https://github.com/neoocean/pytmux" target="_blank" rel="noopener">GitHub ↗</a>' +
    '</div></div>';
  body.insertBefore(nav, body.firstChild);

  // ── 목차 사이드바 (플러그인 전체 나열, 카테고리 헤더 + 현재 강조) ──
  var toc = document.querySelector('.toc');
  if (toc) {
    var html = '<h4>플러그인</h4><a href="/guide/plugins"><span class="num">☰</span>플러그인 개요</a>';
    var lastCat = '';
    for (var j = 0; j < PL.length; j++) {
      var p = PL[j];
      if (p.c !== lastCat) {
        html += '<h4 style="margin-top:14px;">' + p.c + '</h4>';
        lastCat = p.c;
      }
      var cls = p.t === slug ? ' class="active"' : '';
      html += '<a' + cls + ' href="/guide/plugin/' + p.t + '">' + p.n + '</a>';
    }
    toc.innerHTML = html;
  }

  // ── 이전 / 다음 페이저 (본문 하단) ──
  var doc = document.querySelector('.doc');
  if (doc && idx >= 0) {
    var prev = idx > 0 ? PL[idx - 1] : null;
    var next = idx < PL.length - 1 ? PL[idx + 1] : null;
    var pager = document.createElement('nav');
    pager.className = 'pager';
    pager.setAttribute('aria-label', '플러그인 페이지 이동');
    var ph = prev
      ? '<a class="pager-prev" href="/guide/plugin/' + prev.t + '">' +
        '<span class="pager-dir">← 이전</span>' +
        '<span class="pager-title">' + prev.n + '</span></a>'
      : '<a class="pager-prev" href="/guide/plugins">' +
        '<span class="pager-dir">← 이전</span>' +
        '<span class="pager-title">플러그인 개요</span></a>';
    if (next) {
      ph += '<a class="pager-next" href="/guide/plugin/' + next.t + '">' +
        '<span class="pager-dir">다음 →</span>' +
        '<span class="pager-title">' + next.n + '</span></a>';
    } else {
      ph += '<a class="pager-next" href="/guide/plugins">' +
        '<span class="pager-dir">다음 →</span>' +
        '<span class="pager-title">플러그인 개요로</span></a>';
    }
    pager.innerHTML = ph;
    doc.appendChild(pager);
  }

  // ── 푸터 (사이트 공통) ──
  var foot = document.createElement('footer');
  foot.className = 'foot';
  foot.innerHTML =
    '<div class="wrap">' +
    '<div><a class="brand" href="/" style="text-decoration:none"><span class="prompt">❯</span> pytmux</a> &nbsp; — 플러그인 가이드</div>' +
    '<div class="foot-links">' +
    '<a href="/">홈</a> · ' +
    '<a href="/guide">가이드 개요</a> · ' +
    '<a href="/guide/plugins">플러그인</a> · ' +
    '<a href="/changes">최근 수정사항</a> · ' +
    '<a href="https://github.com/neoocean/pytmux" target="_blank" rel="noopener">GitHub</a> · ' +
    '<a href="mailto:me@woojinkim.org?subject=pytmux">me@woojinkim.org</a>' +
    '</div></div>';
  body.appendChild(foot);
})();
