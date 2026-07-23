# pytmux 토큰 동기화 서버 — 컨테이너 이미지.
#
# 설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.
# 의존성은 cryptography 하나뿐이라(웹 프레임워크·WebAuthn 라이브러리 없음) 이미지는
# 작고 공격면도 좁다. 빌드 컨텍스트는 **pytmux 저장소 루트**다:
#
#   docker build -f tools/synserver/Dockerfile -t pytmux-synserver .
#
# 앞단에 TLS 를 맡길 리버스 프록시(Caddy·Cloudflare Tunnel 등)를 두고, 이 컨테이너는
# 평문 HTTP 로만 듣는다 — 패스키는 HTTPS(또는 localhost)에서만 동작하므로 프록시가
# 반드시 있어야 한다.
FROM python:3.13-slim

# cryptography 는 wheel 로 온다(빌드 도구 불필요).
RUN pip install --no-cache-dir 'cryptography>=42'

WORKDIR /srv
COPY tools/synserver /srv/synserver

# 비루트로 돌린다. DB 는 /data 볼륨(0700) — 유출 시 남는 것이 암호문뿐이라도
# 파일 권한은 조인다(설계 §8.2 와 같은 관례).
RUN useradd --system --uid 10001 --home /srv synserver \
    && mkdir -p /data && chown -R synserver:synserver /data /srv \
    && chmod 700 /data
USER synserver
VOLUME ["/data"]
EXPOSE 8080

# --rp-id 는 **필수**(패스키가 묶이는 도메인)라 compose/run 에서 넘긴다.
ENTRYPOINT ["python3", "-u", "/srv/synserver/app.py"]
CMD ["--db", "/data/sync.db", "--host", "0.0.0.0", "--port", "8080"]
