import json
import os
import tempfile

import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from pytmuxlib import transcript


def _evt(msg_id="m1", req="r1", inp=100, out=50, cc=0, cr=0,
         sidechain=False, uuid="u1", model="claude-opus-4-8",
         typ="assistant", ts="2026-06-22T10:00:00.000Z", sid="s1"):
    return {
        "type": typ, "uuid": uuid, "requestId": req, "timestamp": ts,
        "sessionId": sid, "isSidechain": sidechain,
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr}},
    }


async def test_parse_line_extracts_four_categories():
    out = transcript.parse_line(_evt(inp=7694, out=821, cc=38625, cr=120000))
    assert out is not None
    xkey, rec = out
    assert xkey == "m1:r1"
    assert (rec["input"], rec["output"], rec["cache_create"],
            rec["cache_read"]) == (7694, 821, 38625, 120000)
    assert rec["model"] == "claude-opus-4-8"
    assert rec["session_uuid"] == "s1"
    assert rec["is_sidechain"] == 0


async def test_parse_line_skips_non_assistant_and_no_usage():
    assert transcript.parse_line(_evt(typ="user")) is None
    assert transcript.parse_line({"type": "assistant", "message": {}}) is None
    assert transcript.parse_line({"type": "system"}) is None


async def test_parse_line_dedup_key_fallback_to_uuid():
    # message.id 없으면 이벤트 uuid 로 폴백(유일 키).
    e = _evt(msg_id=None, req=None, uuid="evt-xyz")
    e["message"].pop("id", None)
    xkey, _rec = transcript.parse_line(e)
    assert xkey == "evt-xyz"


async def test_sum_records_footer_vs_full_ratio():
    recs = [transcript.parse_line(_evt(inp=10, out=5, cc=0, cr=985,
                                       msg_id=f"m{i}", req=f"r{i}"))[1]
            for i in range(3)]
    b = transcript.sum_records(recs)
    assert b["footer"] == (10 + 5) * 3          # in+out (스크랩 근사)
    assert b["full"] == 1000 * 3                # 4항목 합
    assert b["turns"] == 3
    assert round(b["ratio"], 1) == round(3000 / 45, 1)


async def test_iter_records_skips_garbage_lines():
    lines = [json.dumps(_evt(msg_id="m1", req="r1")), "", "not json{",
             json.dumps(_evt(typ="user")),
             json.dumps(_evt(msg_id="m2", req="r2"))]
    keys = [k for k, _ in transcript.iter_records(lines)]
    assert keys == ["m1:r1", "m2:r2"]


async def test_tail_file_incremental_and_holds_partial_line():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.jsonl")
        line1 = json.dumps(_evt(msg_id="m1", req="r1", inp=100)) + "\n"
        with open(p, "w") as fh:
            fh.write(line1)
        recs, off = transcript.tail_file(p, 0)
        assert len(recs) == 1 and off == len(line1.encode())
        # append 완성줄 + 미완성줄(개행 없음) → 완성분만, offset 은 완성 끝까지.
        line2 = json.dumps(_evt(msg_id="m2", req="r2")) + "\n"
        partial = '{"type":"assistant","message"'  # 개행 없음
        with open(p, "a") as fh:
            fh.write(line2 + partial)
        recs2, off2 = transcript.tail_file(p, off)
        assert [r["xkey"] for r in recs2] == ["m2:r2"]
        assert off2 == len(line1.encode()) + len(line2.encode())
        # 같은 offset 재호출 → 미완성줄만 있어 새 레코드 없음(중복 없음).
        recs3, off3 = transcript.tail_file(p, off2)
        assert recs3 == [] and off3 == off2


async def test_tail_file_handles_truncation():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.jsonl")
        with open(p, "w") as fh:
            fh.write(json.dumps(_evt(msg_id="m1", req="r1")) + "\n")
        # offset 이 파일보다 큼(회전/절단) → 0 부터 다시.
        recs, off = transcript.tail_file(p, 999999)
        assert len(recs) == 1 and off > 0


async def test_claude_descendant_pid_bfs():
    # 셸(100) → 자식 node(200) → 손자 claude(300).
    rows = [(100, 1, "zsh"), (200, 100, "node"), (300, 200, "claude"),
            (400, 1, "claude")]  # 무관한 다른 claude
    assert transcript.claude_descendant_pid(100, lambda: rows) == 300
    assert transcript.claude_descendant_pid(999, lambda: rows) is None


async def test_find_transcript_pid_path_then_cwd_fallback():
    root = transcript.projects_dir()
    jp = os.path.join(root, "enc", "sess.jsonl")
    # 1차: pid→lsof 가 경로를 주면 그걸 쓴다.
    got = transcript.find_transcript(
        shell_pid=100, cwd="/x",
        ps_list=lambda: [(200, 100, "claude")],
        open_jsonl=lambda pid: [jp],
        list_dir=lambda d: [])
    assert got == jp
    # 폴백: claude pid 없음 → cwd 디렉터리 최신 mtime.
    got2 = transcript.find_transcript(
        shell_pid=100, cwd="/x",
        ps_list=lambda: [(200, 100, "zsh")],
        open_jsonl=lambda pid: [],
        list_dir=lambda d: ["/proj/a.jsonl", "/proj/b.jsonl"])
    assert got2 in ("/proj/a.jsonl", "/proj/b.jsonl")
