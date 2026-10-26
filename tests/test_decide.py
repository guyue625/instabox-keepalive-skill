#!/usr/bin/env python3
"""decide() / write_next_wake() 离线单测：不调真实 API、不需要凭证。

跑法（仓库根目录）：
    python3 -m unittest discover -s tests -v
或直接：
    python3 tests/test_decide.py

原理：scripts/ 不在默认 import 路径里，先加进 sys.path；
ib_api 在 dynamic_credentials 缺失时已改为延迟报错，所以 import 安全。
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import instabox_tick as tick  # noqa: E402

NOW = 1_800_000_000.0  # 固定时刻，测试不依赖真实时钟


def iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def running(run0, **kw):
    m = {"id": "m1", "name": "a", "state": "running", "runStartedAt": iso(run0)}
    m.update(kw)
    return m


class DecideRunningTests(unittest.TestCase):
    def test_exec_due_when_never_exec(self):
        st = {}
        action, due = tick.decide(running(NOW), st, NOW)
        self.assertEqual(action, "exec")
        self.assertLessEqual(due, NOW + tick.EXEC_EVERY)

    def test_no_action_before_exec_due(self):
        st = {"state": "running", "run0": NOW, "lastExec": NOW - 60}
        action, due = tick.decide(running(NOW), st, NOW)
        self.assertIsNone(action)
        self.assertEqual(due, NOW - 60 + tick.EXEC_EVERY)

    def test_rotate_at_470min(self):
        run0 = NOW - tick.ROTATE_AT
        st = {"state": "running", "run0": run0, "lastExec": NOW}
        action, due = tick.decide(running(run0), st, NOW)
        self.assertEqual(action, "rotate")
        self.assertEqual(due, NOW + 60)

    def test_no_rotate_before_470min(self):
        run0 = NOW - tick.ROTATE_AT + 60
        st = {"state": "running", "run0": run0, "lastExec": NOW}
        action, _ = tick.decide(running(run0), st, NOW)
        self.assertIsNone(action)

    def test_follows_platform_run0_when_externally_rotated(self):
        # 别处刚轮换过：平台 runStartedAt 更新到现在，本地旧 run0 不能触发误轮换
        st = {"state": "running", "run0": NOW - tick.ROTATE_AT - 10, "lastExec": NOW}
        action, _ = tick.decide(running(NOW), st, NOW)
        self.assertIsNone(action)
        self.assertEqual(st["run0"], NOW)

    def test_just_woken_resets_lastexec_for_watchdog_exec(self):
        st = {"state": "suspended", "run0": NOW - 1000, "lastExec": NOW - 10}
        action, _ = tick.decide(running(NOW - 1000), st, NOW)
        self.assertEqual(action, "exec")  # 刚唤醒先跑一次看门狗


class DecideSuspendedTests(unittest.TestCase):
    def test_waits_during_cooldown(self):
        cool = NOW + 300
        m = {"id": "m1", "state": "suspended", "cooldownUntil": iso(cool)}
        action, due = tick.decide(m, {}, NOW)
        self.assertIsNone(action)
        self.assertEqual(due, cool - tick.WAKE_EARLY)

    def test_resume_after_cooldown(self):
        m = {"id": "m1", "state": "suspended", "cooldownUntil": iso(NOW - 10)}
        action, _ = tick.decide(m, {}, NOW)
        self.assertEqual(action, "resume")

    def test_resume_without_cooldown(self):
        m = {"id": "m1", "state": "suspended"}
        action, due = tick.decide(m, {}, NOW)
        self.assertEqual(action, "resume")
        self.assertEqual(due, NOW + 60)

    def test_start_when_stopped(self):
        m = {"id": "m1", "state": "stopped"}
        action, _ = tick.decide(m, {}, NOW)
        self.assertEqual(action, "start")

    def test_transient_waits_60s(self):
        for state in tick.TRANSIENT:
            action, due = tick.decide({"id": "m1", "state": state}, {}, NOW)
            self.assertIsNone(action, state)
            self.assertEqual(due, NOW + 60, state)

    def test_unknown_state_is_error(self):
        action, due = tick.decide({"id": "m1", "state": "weird"}, {}, NOW)
        self.assertEqual(action, "error")
        self.assertEqual(due, NOW + tick.EXEC_EVERY)


class WriteNextWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = tick.ROOT
        tick.ROOT = self.tmp.name

    def tearDown(self):
        tick.ROOT = self.old_root
        self.tmp.cleanup()

    def read_wake(self):
        with open(os.path.join(self.tmp.name, "next_wake")) as f:
            return int(f.read())

    def test_empty_book_falls_back_not_empty(self):
        # 回归：原版无机器时写空字符串，loop 每 1 秒空转
        tick.write_next_wake({}, NOW)
        self.assertEqual(self.read_wake(), int(NOW + tick.FALLBACK_WAKE))

    def test_uses_earliest_future(self):
        book = {"a": {"m": {"next": NOW + 120}}, "b": {"n": {"next": NOW + 30}}}
        tick.write_next_wake(book, NOW)
        self.assertEqual(self.read_wake(), int(NOW + 30))

    def test_ignores_past_times(self):
        book = {"a": {"m": {"next": NOW - 5}}}
        tick.write_next_wake(book, NOW)
        self.assertEqual(self.read_wake(), int(NOW + tick.FALLBACK_WAKE))


class IdempotencyKeyTests(unittest.TestCase):
    """_lifecycle 同一逻辑操作重试复用同一个 key，成功后清除。"""

    def test_key_reused_until_success(self):
        seen = []
        real_call = tick.call

        def flaky(method, path, cred, body=None, timeout=60, idempotency_key=None):
            seen.append(idempotency_key)
            if len(seen) == 1:
                raise tick.ApiError("响应丢失（模拟）")
            return {}

        tick.call = flaky
        try:
            st = {}
            with self.assertRaises(tick.ApiError):
                tick._lifecycle("resume", "m1", "cred", st)
            tick._lifecycle("resume", "m1", "cred", st)
        finally:
            tick.call = real_call
        self.assertEqual(seen[0], seen[1])          # 重试同 key
        self.assertNotIn("pendingKey:resume", st)   # 成功后清除

    def test_new_operation_gets_new_key(self):
        keys = []
        real_call = tick.call

        def ok(method, path, cred, body=None, timeout=60, idempotency_key=None):
            keys.append(idempotency_key)
            return {}

        tick.call = ok
        try:
            st = {}
            tick._lifecycle("resume", "m1", "cred", st)
            tick._lifecycle("resume", "m1", "cred", st)
        finally:
            tick.call = real_call
        self.assertNotEqual(keys[0], keys[1])


class WaitStateTests(unittest.TestCase):
    def test_poll_failure_does_not_abort(self):
        real_call, real_sleep = tick.call, tick.time.sleep
        calls = {"n": 0}

        def flaky(method, path, cred, body=None, timeout=60, idempotency_key=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise tick.ApiError("抖动（模拟）")
            return {"state": "suspended"}

        tick.call = flaky
        tick.time.sleep = lambda s: None
        try:
            # limit 要大于 0；用小 limit + 无等待 sleep 快速跑完多次轮询
            orig_time = tick.time.time
            seq = iter([0, 0, 0, 0, 0, 0, 999])
            tick.time.time = lambda: next(seq, 999)
            try:
                self.assertTrue(tick.wait_state("m1", "cred", "suspended", limit=90))
            finally:
                tick.time.time = orig_time
        finally:
            tick.call = real_call
            tick.time.sleep = real_sleep


class MainFallbackTests(unittest.TestCase):
    """回归：空账号 / 凭证错误时 main() 也必须写出未来的 next_wake。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root, self.old_argv = tick.ROOT, sys.argv
        tick.ROOT = self.tmp.name
        sys.argv = ["instabox_tick.py", "cred-x", "--now"]
        self.real_call = tick.call

    def tearDown(self):
        tick.ROOT, sys.argv, tick.call = self.old_root, self.old_argv, self.real_call
        self.tmp.cleanup()

    def wake_is_future(self):
        with open(os.path.join(self.tmp.name, "next_wake")) as f:
            content = f.read().strip()
        self.assertTrue(content.isdigit(), f"next_wake 应为数字时间戳，实际: {content!r}")
        self.assertGreater(int(content), time.time())

    def test_empty_machine_list_writes_fallback_wake(self):
        tick.call = lambda *a, **k: {"machines": []}
        self.assertEqual(tick.main(), 0)
        self.wake_is_future()

    def test_credential_error_writes_fallback_wake(self):
        def bad(*a, **k):
            raise tick.DynamicCredentialError("bad cred（模拟）")
        tick.call = bad
        self.assertEqual(tick.main(), 3)
        self.wake_is_future()

    def test_list_failure_writes_fallback_wake(self):
        def boom(*a, **k):
            raise RuntimeError("network down（模拟）")
        tick.call = boom
        self.assertEqual(tick.main(), 1)
        self.wake_is_future()


if __name__ == "__main__":
    unittest.main()
