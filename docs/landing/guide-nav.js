// pytmux 가이드 공용 크롬 — 상단 내비 / 목차 사이드바 / 이전·다음 페이저 / 푸터를
// 챕터별 페이지(guide/<topic>.html)에 주입한다. space 프로젝트 guide-nav.js 의 패턴을
// 따른 것으로, 빌드 단계 없이 이 파일 하나가 공유 파셜 역할을 한다. 각 페이지 <body> 의
// data-topic 속성으로 현재 챕터를 알아내 목차 강조·페이저 링크를 만든다.
//
// 여기서 innerHTML 에 넣는 모든 문자열은 1st-party 정적 마크업(내가 만든 내비/목차/푸터와
// 아래 CH 카탈로그)뿐이다 — 사용자·네트워크 입력이 닿지 않는다.
(function () {
  var body = document.body;
  var topic = body.getAttribute('data-topic') || '';

  // 챕터 순서 = 목차 순서 = 이전/다음 순서. 한 곳에서만 관리한다.
  var CH = [
    { t: 'start',      n: '시작 · 핵심 개념' },
    { t: 'install',    n: '설치' },
    { t: 'panes',      n: '패널 다루기' },
    { t: 'tabs',       n: '탭 · 윈도우' },
    { t: 'scrollback', n: '스크롤백 · 복사' },
    { t: 'mouse',      n: '마우스로 제어' },
    { t: 'command',    n: '메뉴 · 명령 프롬프트' },
    { t: 'claude',     n: 'Claude Code 연동' },
    { t: 'tokens',     n: '토큰 사용량' },
    { t: 'sync',       n: '여러 머신 토큰 동기화' },
    { t: 'remote',     n: '원격 페더레이션' },
    { t: 'restart',    n: '재시작 · 회복' },
    { t: 'tools',      n: '도구 · 위젯' },
    { t: 'config',     n: '설정 파일' },
    { t: 'plugins',    n: '플러그인' }
  ];
  var idx = -1;
  for (var i = 0; i < CH.length; i++) { if (CH[i].t === topic) { idx = i; break; } }

  // ── 상단 내비 (사이트 공통) ──
  var nav = document.createElement('nav');
  nav.className = 'nav';
  nav.innerHTML =
    '<div class="nav-inner">' +
    '<a class="brand" href="/" style="text-decoration:none"><span class="prompt">❯</span> pytmux</a>' +
    '<div class="nav-links">' +
    '<a href="/">홈</a>' +
    '<a href="/guide">가이드 개요</a>' +
    '<a href="/changes">최근 수정사항</a>' +
    '<a class="gh" href="https://github.com/neoocean/pytmux" target="_blank" rel="noopener">GitHub ↗</a>' +
    '</div></div>';
  body.insertBefore(nav, body.firstChild);

  // ── 목차 사이드바 (모든 챕터 나열, 현재 강조) ──
  var toc = document.querySelector('.toc');
  if (toc) {
    var html = '<h4>가이드 목차</h4><a href="/guide"><span class="num">☰</span>가이드 개요</a>';
    for (var j = 0; j < CH.length; j++) {
      var c = CH[j];
      var cls = c.t === topic ? ' class="active"' : '';
      html += '<a' + cls + ' href="/guide/' + c.t + '"><span class="num">' + (j + 1) + '</span>' + c.n + '</a>';
    }
    toc.innerHTML = html;
  }

  // ── 이전 / 다음 페이저 (본문 하단) ──
  var doc = document.querySelector('.doc');
  if (doc && idx >= 0) {
    var prev = idx > 0 ? CH[idx - 1] : { t: '', n: '가이드 개요', href: '/guide' };
    var next = idx < CH.length - 1 ? CH[idx + 1] : null;
    var prevHref = prev.href || ('/guide/' + prev.t);
    var pager = document.createElement('nav');
    pager.className = 'pager';
    pager.setAttribute('aria-label', '가이드 페이지 이동');
    var ph = '<a class="pager-prev" href="' + prevHref + '">' +
      '<span class="pager-dir">← 이전</span>' +
      '<span class="pager-title">' + prev.n + '</span></a>';
    if (next) {
      ph += '<a class="pager-next" href="/guide/' + next.t + '">' +
        '<span class="pager-dir">다음 →</span>' +
        '<span class="pager-title">' + next.n + '</span></a>';
    } else {
      ph += '<a class="pager-next" href="/#download">' +
        '<span class="pager-dir">다음 →</span>' +
        '<span class="pager-title">내려받아 시작하기</span></a>';
    }
    pager.innerHTML = ph;
    doc.appendChild(pager);
  }

  // ── 푸터 (사이트 공통) ──
  var foot = document.createElement('footer');
  foot.className = 'foot';
  foot.innerHTML =
    '<div class="wrap">' +
    '<div><a class="brand" href="/" style="text-decoration:none"><span class="prompt">❯</span> pytmux</a> &nbsp; — 상세 가이드</div>' +
    '<div class="foot-links">' +
    '<a href="/">홈</a> · ' +
    '<a href="/guide">가이드 개요</a> · ' +
    '<a href="/changes">최근 수정사항</a> · ' +
    '<a href="https://github.com/neoocean/pytmux" target="_blank" rel="noopener">GitHub</a> · ' +
    '<a href="mailto:me@woojinkim.org?subject=pytmux">me@woojinkim.org</a>' +
    '</div></div>';
  body.appendChild(foot);
})();
