"""OJHC 测试套件（只用标准库 unittest，无第三方依赖）。

运行（在 engine/ 目录）：
    python -m unittest discover -s tests -v

覆盖：压力引擎数值回归、判题归一化、档位映射、闸门、VJudge 榜单解析、
URL 识别、SQLite 往返、浏览器桥接、设备链路（租约/急停）、离线回放。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from ojhc_lib import bridge as bridge_mod  # noqa: E402
from ojhc_lib import device as device_mod  # noqa: E402
from ojhc_lib import feedback as feedback_mod  # noqa: E402
from ojhc_lib import platforms as platforms_mod  # noqa: E402
from ojhc_lib import pressure as pressure_mod  # noqa: E402
from ojhc_lib import profile as profile_mod  # noqa: E402
from ojhc_lib import runner as runner_mod  # noqa: E402
from ojhc_lib import store as store_mod  # noqa: E402

FIXTURES = ENGINE_DIR / "fixtures"


class TestPressure(unittest.TestCase):
    def test_doc_example_values(self) -> None:
        """DESIGN.md §5.6 的示例数值（别人改公式会先在这里挂掉）。"""
        inp = pressure_mod.PressureInput(
            now=3960.0, tau=0.55, duration_s=7200.0, n_official=8000, rank=3200,
            solved=1, t_since_ac_s=1320.0, e_total=3.2, e_seed=2600.0,
            target_percentile=0.25, recent=[(-1.0, 300.0), (-1.0, 540.0)])
        st = pressure_mod.compute(inp, pressure_mod.PressureState())
        self.assertAlmostEqual(st.components["rank"], 22.7, delta=0.5)
        self.assertAlmostEqual(st.components["progress"], 46.9, delta=0.5)
        self.assertAlmostEqual(st.components["stagnation"], 42.3, delta=0.5)
        self.assertAlmostEqual(st.components["recent"], 78.2, delta=0.5)
        self.assertAlmostEqual(st.p, 34.0, delta=0.6)

    def test_recent_neutral_prior(self) -> None:
        """单条 WA 必须随时间衰减：0/10/25 分钟应递减。"""
        scores = []
        for age in (0.0, 600.0, 1500.0):
            inp = pressure_mod.PressureInput(
                now=1000.0, tau=0.5, duration_s=7200, n_official=1000, rank=100,
                solved=0, t_since_ac_s=600, e_total=3.0,
                recent=[(-1.0, age)])
            scores.append(pressure_mod.recent_score(inp.recent))
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[1], scores[2])
        self.assertLessEqual(scores[0], 100.0)
        self.assertGreaterEqual(scores[2], 0.0)

    def test_missing_rank_lowers_confidence(self) -> None:
        inp = pressure_mod.PressureInput(
            now=1000.0, tau=0.5, duration_s=7200, n_official=None, rank=None,
            solved=0, t_since_ac_s=600, e_total=3.0)
        st = pressure_mod.compute(inp, pressure_mod.PressureState())
        # 只剩 progress/stagnation/recent(0) → 原始权重和 0.65，仍可高置信
        self.assertIn("rank", st.components)
        self.assertIsNone(st.components["rank"])


class TestVerdict(unittest.TestCase):
    def test_normalization(self) -> None:
        cases = {
            "Accepted": "OK", "AC": "OK", "答案正确": "OK",
            "Wrong Answer": "WRONG_ANSWER", "WA": "WRONG_ANSWER",
            "WRONG_ANSWER": "WRONG_ANSWER", "答案错误": "WRONG_ANSWER",
            "TIME_LIMIT_EXCEEDED": "TIME_LIMIT_EXCEEDED", "TLE": "TIME_LIMIT_EXCEEDED",
            "运行超时": "TIME_LIMIT_EXCEEDED",
            "Runtime Error": "RUNTIME_ERROR", "RE": "RUNTIME_ERROR",
            "Compile Error": "COMPILATION_ERROR",
            "Judging": "TESTING", "waiting": "TESTING", "testing": "TESTING",
            "???": "UNKNOWN",
        }
        for raw, want in cases.items():
            self.assertEqual(platforms_mod.normalize_verdict(raw), want, raw)


class TestProfile(unittest.TestCase):
    def test_mapping_monotonic_and_bounded(self) -> None:
        prof = profile_mod.new_profile(20, 45, "v3", "both", "")
        seq = [prof.intensity_for(f) for f in (0, 0.2, 0.35, 0.5, 0.65, 0.85, 1.0)]
        self.assertEqual(seq, sorted(seq))
        self.assertGreaterEqual(min(seq), prof.perceptible_pct)
        self.assertLessEqual(max(seq), prof.user_max_pct)
        self.assertLessEqual(max(seq), profile_mod.HARD_CAP_PCT)
        self.assertGreaterEqual(prof.usable_span, 10)

    def test_invalid_profile_rejected(self) -> None:
        self.assertFalse(profile_mod.DeviceProfile().is_valid())
        self.assertFalse(profile_mod.DeviceProfile(perceptible_pct=40, comfort_pct=30,
                                                   user_max_pct=15).is_valid())


class TestGate(unittest.TestCase):
    def setUp(self) -> None:
        self.prof = profile_mod.new_profile(20, 45, "v3", "both", "")

    def _wa(self, s: int = 2) -> feedback_mod.Event:
        return feedback_mod.Event("verdict.wa", 100.0, "题 B", s=s)

    def test_l0_never_fires(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="pressure")
        dec = gate.evaluate(self._wa(), pressure_mod.PressureState(
            p=10, level=0, confidence="high"), 100.0)
        self.assertFalse(dec.would_fire)

    def test_l1_fires(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="pressure")
        dec = gate.evaluate(self._wa(), pressure_mod.PressureState(
            p=30, level=1, confidence="high"), 100.0)
        self.assertTrue(dec.would_fire)
        self.assertGreater(dec.intensity, 0)
        self.assertEqual(dec.duration_s, feedback_mod.DURATION_S[2])

    def test_cooldown_blocks_second(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="pressure")
        state = pressure_mod.PressureState(p=60, level=2, confidence="high")
        first = gate.evaluate(self._wa(), state, 100.0)
        second = gate.evaluate(feedback_mod.Event("verdict.tle", 109.0, "题 C", s=3),
                               state, 109.0)
        third = gate.evaluate(feedback_mod.Event("verdict.tle", 110.0, "题 C", s=3),
                              state, 110.0)
        self.assertTrue(first.would_fire)
        self.assertFalse(second.would_fire)
        self.assertIn("全局冷却中（10s）", second.deny)
        self.assertTrue(third.would_fire)

    def test_casual_same_kind_cooldown(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="simple")
        state = pressure_mod.PressureState(p=60, level=2, confidence="high")
        first = gate.evaluate(self._wa(), state, 100.0)
        early = gate.evaluate(self._wa(), state, 129.0)
        ready = gate.evaluate(self._wa(), state, 130.0)
        self.assertTrue(first.would_fire)
        self.assertIn("同类事件冷却中（verdict.wa）", early.deny)
        self.assertTrue(ready.would_fire)

    def test_escalation_ban_caps_s(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="pressure")
        state = pressure_mod.PressureState(p=90, level=3, confidence="high")
        decisions = [gate.evaluate(feedback_mod.Event("verdict.tle", t, "B", s=4),
                                   state, float(t)) for t in (0, 200, 400, 600)]
        self.assertTrue(decisions[0].would_fire)
        self.assertEqual(decisions[0].s_eff, 4)
        # 连续 3 次失败且期间无 AC → 升压禁止：下一次强度封顶 s=2
        self.assertTrue(decisions[-1].would_fire)
        self.assertLessEqual(decisions[-1].s_eff, 2)

    def test_uncalibrated_never_fires(self) -> None:
        dec = feedback_mod.Gate(None, "casual", mode="pressure").evaluate(
            self._wa(), pressure_mod.PressureState(p=90, level=3, confidence="high"), 0.0)
        self.assertFalse(dec.would_fire)


class TestPlatforms(unittest.TestCase):
    def test_url_detection(self) -> None:
        urls = {
            "https://codeforces.com/contest/1847/problem/A": ("codeforces", "1847"),
            "https://codeforces.com/gym/104114": ("codeforces", "104114"),
            "https://vjudge.net/contest/654321": ("vjudge", "654321"),
            "https://atcoder.jp/contests/abc300": ("atcoder", "abc300"),
            "https://www.luogu.com.cn/contest/123456": ("luogu", "123456"),
            "https://ac.nowcoder.com/acm/contest/56789": ("nowcoder", "56789"),
            "https://example.com/foo": None,
        }
        for url, want in urls.items():
            self.assertEqual(platforms_mod.detect_from_url(url), want, url)

    def test_vjudge_rank_single_parse(self) -> None:
        payload = json.loads((FIXTURES / "vjudge_rank_single_mock.json")
                             .read_text(encoding="utf-8"))
        parsed = platforms_mod.parse_rank_single(payload, "tester")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["rank"], 3)
        self.assertEqual(parsed["solved"], 1)
        self.assertEqual(parsed["n_official"], 3)
        self.assertAlmostEqual(parsed["duration_s"], 7200)

    def test_vjudge_scored_format(self) -> None:
        payload = {
            "title": "scored", "length": 120,
            "participants": {"1": {"name": "me"}, "2": {"name": "other"}},
            "submissions": [
                [1, 0, 0, 1000, 61.0, 110.0],
                [2, 0, 1, 2000, 110.0, 110.0],
                [2, 1, 0, 3000, 20.0, 100.0],
            ],
        }
        parsed = platforms_mod.parse_rank_single(payload, "me")
        assert parsed is not None
        self.assertEqual(parsed["rank"], 2)     # 61 < 130
        self.assertEqual(parsed["solved"], 1)   # score>0 的题数

    def test_vjudge_duration_units(self) -> None:
        from ojhc_lib.platforms import vjudge as vj
        self.assertEqual(vj._duration_seconds(14100000), 14100.0)   # 毫秒（实测格式）
        self.assertEqual(vj._duration_seconds(7200), 7200.0)        # 秒
        self.assertEqual(vj._duration_seconds(120), 7200.0)         # 分钟（旧格式）

    def test_vjudge_acm_six_tuple_not_scored(self) -> None:
        """ICPC 等 ACM 赛制的 rank/single 也是 6 元组（0/100），不能当计分赛。"""
        payload = {
            "title": "icpc", "length": 14100000,
            "participants": {"1": {"name": "alice"}, "2": {"name": "bob"}},
            "submissions": [
                [1, 0, 1, 1000, 100.0, 100.0],   # AC
                [1, 0, 0, 500, 0.0, 100.0],      # WA
                [1, 1, 0, 2000, 0.0, 100.0],     # WA
                [1, 1, 1, 3000, 100.0, 100.0],   # AC
                [2, 0, 1, 400, 100.0, 100.0],    # bob AC
            ],
        }
        parsed = platforms_mod.parse_rank_single(payload, "alice")
        assert parsed is not None
        self.assertEqual(parsed["solved"], 2)
        self.assertEqual(parsed["rank"], 1)          # alice 2 题 > bob 1 题
        self.assertAlmostEqual(parsed["duration_s"], 14100.0)

    def test_expected_solves(self) -> None:
        problems = [platforms_mod.Problem("A", 800), platforms_mod.Problem("B", 1600)]
        self.assertGreater(platforms_mod.expected_solves(problems, 1400), 1.0)
        self.assertEqual(platforms_mod.expected_solves([], 1400), 3.0)


class TestCFFastPath(unittest.IsolatedAsyncioTestCase):
    def test_page_baseline_and_final_verdicts(self) -> None:
        hub = bridge_mod.BridgeHub("codeforces", "2256", "tester")
        historical = {"sid": "100", "verdict": "WRONG_ANSWER"}
        pending = {"sid": "101", "verdict": "TESTING"}
        final = {**pending, "verdict": "WRONG_ANSWER"}
        later = {"sid": "102", "verdict": "OK"}

        self.assertEqual(hub.ingest_submissions([historical], cf_page=True), 0)
        self.assertEqual(hub.handshakes, 1)
        self.assertEqual(hub.ingest_submissions([historical, pending, later], cf_page=True), 1)
        self.assertEqual([sub.sid for sub in hub.drain()], ["102"])
        self.assertEqual(hub.ingest_submissions([historical, final, later], cf_page=True), 1)
        self.assertEqual([sub.sid for sub in hub.drain()], ["101"])
        self.assertEqual(hub.ingest_submissions([final, later], cf_page=True), 0)

    async def test_fast_path_does_not_wait_for_api(self) -> None:
        from unittest import mock
        from ojhc_lib.platforms import codeforces as cf

        adapter = cf.CodeforcesAdapter("2256", "tester")
        hub = bridge_mod.BridgeHub("codeforces", "2256", "tester")
        adapter.attach_fast(hub)
        hub.ingest_submissions([], cf_page=True)
        api_started = asyncio.Event()
        api_ready = asyncio.Event()
        api_verdict = cf.Submission("101", 10.0, "TESTING", "A")

        async def fetch():
            api_started.set()
            await api_ready.wait()
            return [api_verdict]

        with mock.patch.object(adapter, "_fetch_submissions", side_effect=fetch), \
                mock.patch.object(adapter, "_standings_at", cf.time.monotonic()):
            try:
                self.assertEqual(await adapter.poll_live(None), [])
                await asyncio.wait_for(api_started.wait(), timeout=1)
                hub.ingest_submissions([{"sid": "101", "verdict": "WRONG_ANSWER"}], cf_page=True)
                batch = await asyncio.wait_for(adapter.poll_live(None), timeout=1)
                self.assertEqual([(sub.sid, sub.verdict) for sub in batch], [("101", "WRONG_ANSWER")])
                await asyncio.wait_for(adapter.standings(10.0), timeout=1)
                api_ready.set()
                await adapter._native_task
                api_verdict.verdict = "WRONG_ANSWER"
                self.assertEqual(await adapter.poll_live("101"), [])
                await adapter._native_task
                self.assertEqual(await adapter.poll_live("101"), [])
            finally:
                await adapter.close_live()


class TestStore(unittest.TestCase):
    def test_roundtrip_and_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            st = store_mod.Store(db)
            meta = asyncio.run(self._meta())
            sid = st.create_session("codeforces", "1847", "tester", "casual", meta)
            st.add_submission(sid, platforms_mod.Submission("1", 10, "OK", "A"))
            st.add_event(sid, 10.0, "verdict.wa", 2, 26, 0.8, True, [], allowed=True)
            st.commit()
            rep = st.report(sid)
            st.close()
            self.assertEqual(len(rep["submissions"]), 1)
            self.assertEqual(rep["events"][0]["allowed"], 1)

    async def _meta(self):
        return await platforms_mod.load_mock(FIXTURES / "cf_1847_mock.json").load_meta()


class TestBridge(unittest.TestCase):
    def test_second_bridge_cannot_share_listening_port(self) -> None:
        first = bridge_mod.BridgeHub("nowcoder", "142560", "tester", port=0)
        first.start()
        second = bridge_mod.BridgeHub("nowcoder", "143854", "tester", port=first.port)
        try:
            with self.assertRaises(OSError):
                second.start()
        finally:
            second.stop()
            first.stop()

    def test_roundtrip_and_token(self) -> None:
        import urllib.request
        hub = bridge_mod.BridgeHub("atcoder", "abc300", "tester", duration_s=7200, port=0)
        base = hub.start()
        try:
            session = json.load(urllib.request.urlopen(base + "/session", timeout=5))
            hub.ingest_submissions([])
            start_ms = session["started_at"] * 1000
            new = {"sid": "2", "verdict": "答案错误", "problem": "A",
                   "handle": "tester", "time": start_ms + 1000}
            req = urllib.request.Request(
                base + "/ingest/submissions",
                data=json.dumps([
                    {**new, "sid": "1", "time": start_ms - 1000},
                    {**new, "handle": "other-user"},
                    new,
                ]).encode(),
                method="POST",
                headers={"Content-Type": "application/json",
                         bridge_mod.BRIDGE_TOKEN_HEADER: session["token"]})
            urllib.request.urlopen(req, timeout=5)
            subs = hub.drain(None)
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0].sid, "2")
            self.assertEqual(subs[0].verdict, "WRONG_ANSWER")
            # 错误 token 必须 401
            bad = urllib.request.Request(
                base + "/ingest/submissions", data=b"[]", method="POST",
                headers={"Content-Type": "application/json",
                         bridge_mod.BRIDGE_TOKEN_HEADER: "bad"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(ctx.exception.code, 401)
        finally:
            hub.stop()


class TestNowcoderBridge(unittest.IsolatedAsyncioTestCase):
    async def test_empty_baseline_and_out_of_order_verdicts(self) -> None:
        hub = bridge_mod.BridgeHub("nowcoder", "142560", "tester")
        adapter = bridge_mod.BridgeAdapter(hub)
        self.assertEqual(hub.ingest_submissions([]), 0)
        self.assertEqual(hub.handshakes, 1)

        pending = {"sid": "101", "verdict": "等待判题"}
        accepted = {"sid": "102", "verdict": "答案正确"}
        wrong = {**pending, "verdict": "答案错误"}
        self.assertEqual(hub.ingest_submissions([pending, accepted]), 1)
        self.assertEqual([sub.sid for sub in await adapter.poll(None)], ["102"])
        self.assertEqual(hub.ingest_submissions([wrong, accepted]), 1)
        self.assertEqual([(sub.sid, sub.verdict) for sub in await adapter.poll("102")],
                         [("101", "WRONG_ANSWER")])
        self.assertEqual(hub.ingest_submissions([wrong]), 0)


class TestDeviceLink(unittest.IsolatedAsyncioTestCase):
    async def test_lease_nonce_ack_estop(self) -> None:
        link = device_mod.DeviceLink(port=0)
        await link.start()
        seen: dict = {}

        async def fake_plugin():
            reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
            writer.write((json.dumps({
                "op": "hello", "token": link.token, "plugin": "fake", "version": "0",
                "device": {"connected": True, "type": "v3", "max_a": 200, "max_b": 200},
            }) + "\n").encode())
            await writer.drain()
            seen["ack_hello"] = json.loads(await reader.readline())
            seen["lease"] = json.loads(await reader.readline())
            seen["output"] = json.loads(await reader.readline())
            msg = seen["output"]
            writer.write((json.dumps({"op": "ack", "event_id": msg["event_id"],
                                      "ok": msg["lease_id"] == seen["lease"]["lease_id"],
                                      "applied_pct": msg["intensity_pct"]}) + "\n").encode())
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                m = json.loads(line)
                if m.get("op") == "estop":
                    seen["estop"] = True
                    break
            writer.close()

        task = asyncio.create_task(fake_plugin())
        self.assertTrue(await link.wait_client(3.0))
        fired = await link.fire("e1", 26, 0.8, preset="", channel="both")
        self.assertTrue(fired.get("ok"), fired)
        self.assertIn("nonce", seen["output"])
        await link.estop("test")
        self.assertFalse((await link.fire("e2", 20, 0.8)).get("ok"))
        await link.close()
        await task
        self.assertTrue(seen.get("estop"))


class TestReplay(unittest.IsolatedAsyncioTestCase):
    async def test_mock_replay_events(self) -> None:
        timeline = platforms_mod.load_mock(FIXTURES / "cf_1847_mock.json")
        prof = profile_mod.new_profile(20, 45, "v3", "both", "")
        result = await runner_mod.run_adapter(
            timeline, prof, runner_mod.RunOptions(rating=1400, quiet=True, mode="pressure"),
            store=None, replay=True)
        self.assertGreaterEqual(len(result.events), 8)
        self.assertGreaterEqual(result.summary["would_fire"], 1)
        self.assertEqual(result.summary["platform"], "codeforces")


if __name__ == "__main__":
    unittest.main()


class TestModes(unittest.TestCase):
    """简单模式（默认）：只有「长时间没 AC」才电；压力模式才按算法。"""

    def setUp(self) -> None:
        self.prof = profile_mod.new_profile(20, 75, "v3", "both", "", device_max=40)
        self.high = pressure_mod.PressureState(p=95, level=3, confidence="high")
        self.wa = feedback_mod.Event("verdict.wa", 100.0, "B", s=2)
        self.no_ac = feedback_mod.Event("timeout_no_ac", 100.0, "no ac", s=4)

    def test_simple_allows_single_failures(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="simple")
        self.assertTrue(gate.evaluate(self.wa, self.high, 100.0).would_fire)

    def test_simple_fires_no_ac(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="simple")
        dec = gate.evaluate(self.no_ac, self.high, 100.0)
        self.assertTrue(dec.would_fire)
        self.assertGreater(dec.intensity, 0)

    def test_pressure_fires_l1_failure(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="pressure")
        state = pressure_mod.PressureState(p=30, level=1, confidence="high")
        self.assertTrue(gate.evaluate(self.wa, state, 100.0).would_fire)

    def test_plugin_tuning_overrides_mode(self) -> None:
        gate = feedback_mod.Gate(self.prof, "casual", mode="simple")
        gate.set_tuning({"mode": "pressure", "base_intensity": 80})
        self.assertEqual(gate.mode, "pressure")
        state = pressure_mod.PressureState(p=30, level=1, confidence="high")
        self.assertTrue(gate.evaluate(self.wa, state, 100.0).would_fire)
