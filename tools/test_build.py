"""tools/build.py 的单元测试（仅标准库）：python -m unittest discover -s tools -p "test_*.py" """
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as b  # noqa: E402

MANIFEST = b.load_json(b.MANIFEST)
ALL = set(b.region_names(MANIFEST))


def fake_runtime(manifest, cases, present):
    """按期望构造一份「正确」的控制接口链路与日志命中结果。"""
    chains = {g: b.expected_chain(manifest, g, present) for g in b.all_group_names(manifest)}
    hits = {}
    for c in cases:
        leaf = b.expected_chain(manifest, c["policy"], present)[-1]
        chain = f"{c['policy']}[{leaf}]" if leaf == "REJECT" else c["policy"]
        hits[c["target"]] = (c["rule"], chain)
    return chains, hits


CASES = [
    {"target": "chatgpt.com", "policy": "OpenAI", "rule": "RuleSet/openai"},
    {"target": "www.youtube.com", "policy": "YouTube", "rule": "RuleSet/google-youtube"},
    {"target": "8.8.8.8", "policy": "Global", "rule": "Match/"},
    {"target": "223.5.5.5", "policy": "DIRECT", "rule": "GeoIP/cn"},
]


def all_ok(results):
    return all(r for r, _ in results)


class EvaluateScenarioTest(unittest.TestCase):
    def test_correct_results_pass(self):
        for present in (ALL, ALL - {"United States"}):
            chains, hits = fake_runtime(MANIFEST, CASES, present)
            self.assertTrue(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, present)))

    def test_wrong_rule_fails(self):
        chains, hits = fake_runtime(MANIFEST, CASES, ALL)
        hits["chatgpt.com"] = ("RuleSet/global", "OpenAI")
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, ALL)))

    def test_wrong_first_group_fails(self):
        chains, hits = fake_runtime(MANIFEST, CASES, ALL)
        hits["www.youtube.com"] = ("RuleSet/google-youtube", "Google")
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, ALL)))

    def test_openai_via_japan_reject_fails(self):
        """GPT 复现的场景：OpenAI 走 Japan → REJECT 必须判失败。"""
        chains, hits = fake_runtime(MANIFEST, CASES, ALL)
        chains["OpenAI"] = ["OpenAI", "Japan", "REJECT"]
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, ALL)))

    def test_all_regions_reject_in_full_scenario_fails(self):
        chains, hits = fake_runtime(MANIFEST, CASES, ALL)
        for r in ALL:
            chains[r] = [r, "REJECT"]
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, ALL)))

    def test_missing_region_must_reject_not_direct(self):
        present = ALL - {"United States"}
        chains, hits = fake_runtime(MANIFEST, CASES, present)
        chains["United States"] = ["United States", "COMPATIBLE"]
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, present)))

    def test_log_leaf_mismatch_fails(self):
        present = ALL - {"United States"}
        chains, hits = fake_runtime(MANIFEST, CASES, present)
        hits["chatgpt.com"] = ("RuleSet/openai", "OpenAI[DIRECT]")
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, present)))

    def test_missing_log_fails(self):
        chains, hits = fake_runtime(MANIFEST, CASES, ALL)
        del hits["8.8.8.8"]
        self.assertFalse(all_ok(b.evaluate_scenario(MANIFEST, CASES, chains, hits, ALL)))


class NormalizeRuleTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(b.normalize_rule("RuleSet(openai)"), "RuleSet/openai")
        self.assertEqual(b.normalize_rule("RuleSet/openai"), "RuleSet/openai")
        self.assertEqual(b.normalize_rule("IPCIDR(192.168.0.0/16)"), "IPCIDR/192.168.0.0/16")
        self.assertEqual(b.normalize_rule("Match()"), "Match/")
        self.assertEqual(b.normalize_rule("Match/"), "Match/")
        self.assertEqual(b.normalize_rule("GeoIP(cn)"), "GeoIP/cn")


class MatchModelTest(unittest.TestCase):
    def test_both_models_consistent(self):
        lock = b.load_json(b.LOCK)
        for mode in ("order", "type"):
            m = json.loads(json.dumps(MANIFEST))
            m["qx_match_model"] = mode
            _, equiv, accept, _, _, _ = b.build_outputs(m, lock)
            self.assertEqual(equiv["diff"], [], mode)
            self.assertTrue(all(e == g for _, e, g in accept), mode)


class ImportRollbackTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="cvr-test-"))
        self.repo = self.td / "upstream"
        self.repo.mkdir()
        (self.repo / "a.list").write_text("host, new.example, proxy\n", encoding="utf-8")
        git = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
        self.snap = self.td / "sources" / "qx"
        self.lock = self.td / "sources" / "upstream.lock.json"
        (self.snap).mkdir(parents=True)
        (self.snap / "a.list").write_bytes(b"old\n")
        old_sha = b.sha256_bytes(b"old\n")
        self.lock.write_text(json.dumps({"commit": "old", "files": {"a.list": {"sha256": old_sha}}}) + "\n", encoding="utf-8")
        self.manifest = {"rulesets": [{"src": "a.list"}], "upstream": {"repo": "x", "extra_files": []}}

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_success_replaces_both(self):
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            b.import_upstream(self.manifest, self.repo)
        self.assertIn("new.example", (self.snap / "a.list").read_text(encoding="utf-8"))
        self.assertNotEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], "old")
        self.assertFalse((self.td / "sources" / "qx.bak").exists())

    def test_failure_rolls_back(self):
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if Path(src).name.endswith(".tmp"):  # 新锁文件就位这一步失败
                raise OSError("模拟写锁失败")
            return real_replace(src, dst)

        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock), \
                mock.patch.object(b.os, "replace", flaky):
            with self.assertRaises(b.BuildError):
                b.import_upstream(self.manifest, self.repo)
        self.assertEqual((self.snap / "a.list").read_text(encoding="utf-8"), "old\n")
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], "old")

    def test_missing_file_keeps_old(self):
        self.manifest["rulesets"].append({"src": "missing.list"})
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            with self.assertRaises(b.BuildError):
                b.import_upstream(self.manifest, self.repo)
        self.assertEqual((self.snap / "a.list").read_text(encoding="utf-8"), "old\n")

    def test_interrupted_bak_recovered_before_failed_retry(self):
        """模拟上次导入在改名为 .bak 后中断，且本次重试在写入新快照时失败：旧快照与旧锁文件应已恢复。"""
        bak = self.td / "sources" / "qx.bak"
        bak_lock = self.td / "sources" / "upstream.lock.json.bak"
        self.snap.rename(bak)
        self.lock.rename(bak_lock)
        self.manifest["rulesets"].append({"src": "missing.list"})
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            with self.assertRaises(b.BuildError):
                b.import_upstream(self.manifest, self.repo)
        self.assertTrue(self.snap.exists())
        self.assertTrue(self.lock.exists())
        self.assertEqual((self.snap / "a.list").read_text(encoding="utf-8"), "old\n")
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], "old")

    def test_interrupted_partial_swap_recovered_then_updated(self):
        """模拟上次在「新快照已就位、新锁文件未就位」时中断：先恢复旧版本，再完成本次更新。"""
        bak = self.td / "sources" / "qx.bak"
        bak_lock = self.td / "sources" / "upstream.lock.json.bak"
        self.snap.rename(bak)
        self.lock.rename(bak_lock)
        self.snap.mkdir()
        (self.snap / "a.list").write_text("half\n", encoding="utf-8")
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            b.import_upstream(self.manifest, self.repo)
        self.assertIn("new.example", (self.snap / "a.list").read_text(encoding="utf-8"))
        self.assertNotEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], "old")
        self.assertFalse(bak.exists())
        self.assertFalse(bak_lock.exists())

    def test_ambiguous_single_bak_refuses_and_keeps_files(self):
        """正式版本与备份均无法通过哈希校验时，拒绝覆盖并保留现场。"""
        bak_lock = self.td / "sources" / "upstream.lock.json.bak"
        bak_lock.write_text('{"commit": "bak"}\n', encoding="utf-8")
        (self.snap / "a.list").write_text("corrupted\n", encoding="utf-8")
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            with self.assertRaises(b.BuildError):
                b.import_upstream(self.manifest, self.repo)
        self.assertEqual((self.snap / "a.list").read_text(encoding="utf-8"), "corrupted\n")
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], "old")
        self.assertTrue(bak_lock.exists())

    def test_interrupted_during_bak_cleanup_keeps_valid_live_snapshot(self):
        """复现场景：新快照与新锁文件均已完整就位，但在递归删除 qx.bak 时中断（qx.bak 残缺）。
        下次导入（即便随后因其他原因失败）应保留完整的正式新版本，清理残缺备份，绝不能用残缺备份覆盖正式版本。
        """
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            b.import_upstream(self.manifest, self.repo)
        new_commit = json.loads(self.lock.read_text(encoding="utf-8"))["commit"]
        # 模拟上次清理阶段中断：留下已经被删掉 a.list 的残缺 qx.bak 和旧 bak_lock
        bak = self.td / "sources" / "qx.bak"
        bak_lock = self.td / "sources" / "upstream.lock.json.bak"
        bak.mkdir()
        (bak / "leftover.tmp").write_text("partial\n", encoding="utf-8")
        bak_lock.write_text('{"commit": "old", "files": {"a.list": {"sha256": "deadbeef"}}}\n', encoding="utf-8")
        # 让本次重试在读取上游文件时失败，专门检验恢复阶段的行为
        self.manifest["rulesets"].append({"src": "missing.list"})
        with mock.patch.object(b, "SNAP", self.snap), mock.patch.object(b, "LOCK", self.lock):
            with self.assertRaises(b.BuildError):
                b.import_upstream(self.manifest, self.repo)
        self.assertTrue((self.snap / "a.list").is_file())
        self.assertIn("new.example", (self.snap / "a.list").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["commit"], new_commit)
        self.assertFalse(bak.exists())
        self.assertFalse(bak_lock.exists())


if __name__ == "__main__":
    unittest.main()
