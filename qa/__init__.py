"""qa/ — pytmux 자율 QA 체계 (T0 착지분).

설계 SSOT 는 이슈 트래커의 `pytmux/qa-system` 문서다(이 저장소는 M2 라 산문의 원본이
트래커에 있다). 읽는 길:

    node ../issue/bin/issue.mjs doc-get pytmux/qa-system

쓰는 법은 `qa/README.md`. 이 패키지는 **제품을 import 하지 않는다**(블랙박스 원칙) —
예외는 `pytmuxlib.ipc`/`proc` 로, 그건 로직이 아니라 **엔드포인트 경로 규약**이다.
그 규약을 베껴 적으면 제품이 경로를 바꾼 날 QA 가 조용히 남의 서버를 잰다.
"""
