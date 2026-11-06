#!/usr/bin/env python3
"""ClashVerge-rules 构建工具（仅依赖 Python 3.10+ 标准库）。

用法：
  python tools/build.py                      离线生成 rules/、examples/、reports/
  python tools/build.py --check              离线重建并与已提交文件比对，含域名模型比对
  python tools/build.py --import-from DIR [--ref REV]  从上游仓库指定提交导入快照并更新锁文件
  python tools/build.py --verify [--mihomo EXE]        启动 Mihomo 内核做运行时校验

转换语义（Quantumult X → Mihomo）：
  每份来源拆为 domain（host + host-suffix）、keyword、ip 三个规则集。
  QX 匹配模型由 manifest 的 qx_match_model 决定（均未实测确认）：
    order：按资源顺序先到先得，keyword 规则集紧跟所属来源；
    type ：host > host-suffix > host-keyword 类型优先，keyword 规则集统一后置，
           被更早 host-suffix 抢先命中的 host 条目提升为 *_priority 修正规则。
  两种模型下，本地 IP 规则、GeoIP 与规则集 IP 条件都放在全部域名规则之后，最后是 MATCH。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources" / "manifest.json"
LOCK = ROOT / "sources" / "upstream.lock.json"
SNAP = ROOT / "sources" / "qx"

DOMAIN_RE = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)*$")
KEYWORD_RE = re.compile(r"^[a-z0-9_.-]+$")
TYPE_MAP = {
    "host": "exact",
    "host-suffix": "suffix",
    "host-keyword": "keyword",
    "ip-cidr": "ip",
    "ip6-cidr": "ip",
}
GENERATED_NOTE = "由 tools/build.py 生成，请勿手改"


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- 基础工具

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def q(s: str) -> str:
    """YAML 单引号字符串。"""
    return "'" + str(s).replace("'", "''") + "'"


def parents(domain: str):
    """'a.b.c' -> 'a.b.c', 'b.c', 'c'"""
    labels = domain.split(".")
    for i in range(len(labels)):
        yield ".".join(labels[i:])


# ---------------------------------------------------------------- 解析

@dataclass
class Parsed:
    exact: list = field(default_factory=list)
    suffix: list = field(default_factory=list)
    keyword: list = field(default_factory=list)
    ip: list = field(default_factory=list)


def parse_qx(rel: str, text: str, skipped: list) -> Parsed:
    out = Parsed()
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip().lstrip("\ufeff")
        if not s or s.startswith(("#", ";", "//")):
            continue
        parts = [p.strip() for p in s.split(",")]
        kind = parts[0].lower()
        if len(parts) < 2 or not parts[1]:
            raise BuildError(f"{rel}:{n}: 缺少匹配值：{s}")
        value = parts[1]
        t = TYPE_MAP.get(kind)
        if t is None:
            skipped.append((rel, n, kind, s))
            continue
        if t in ("exact", "suffix"):
            v = value.lower().rstrip(".")
            if not DOMAIN_RE.match(v):
                raise BuildError(f"{rel}:{n}: 非法域名：{value}")
            (out.exact if t == "exact" else out.suffix).append(v)
        elif t == "keyword":
            v = value.lower()
            if not KEYWORD_RE.match(v):
                raise BuildError(f"{rel}:{n}: 非法关键词：{value}")
            out.keyword.append(v)
        else:
            try:
                net = ipaddress.ip_network(value, strict=False)
            except ValueError as e:
                raise BuildError(f"{rel}:{n}: 非法 IP 段：{value}（{e}）") from None
            if (kind == "ip6-cidr") != (net.version == 6):
                raise BuildError(f"{rel}:{n}: {kind} 与地址族不符：{value}")
            out.ip.append(net)
    return out


def normalize(p: Parsed) -> Parsed:
    """单份规则内无损去重：去掉被同文件 host-suffix 覆盖的条目。"""
    sset = set(p.suffix)
    suffix = sorted(s for s in sset if not any(x in sset for x in list(parents(s))[1:]))
    keep = set(suffix)
    exact = sorted(e for e in set(p.exact) if not any(x in keep for x in parents(e)))
    keyword = sorted(set(p.keyword))
    nets = sorted(set(p.ip), key=lambda n: (n.version, n.network_address, n.prefixlen))
    ips = [n for n in nets if not any(o != n and o.version == n.version and n.subnet_of(o) for o in nets)]
    return Parsed(exact, suffix, keyword, ips)


# ---------------------------------------------------------------- 规则单元与 QX 语义模拟

@dataclass
class Unit:
    name: str            # 规则集名；本地规则为 "local:<类型>,<值>"
    policy: str
    raw: Parsed
    norm: Parsed
    rs: dict | None = None  # manifest 中的 ruleset 条目；本地规则为 None


class QXModel:
    """QX 匹配模型（只覆盖域名层）。

    mode="order"：按资源顺序，第一个含匹配条件（host / host-suffix / host-keyword）的资源命中；
    mode="type"：先比类型 host > host-suffix > host-keyword，同类型内再按资源顺序。
    两种模型都尚未在 QX 中实测确认，见 README「匹配模型」。
    """

    def __init__(self, units: list[Unit], mode: str = "order"):
        if mode not in ("order", "type"):
            raise BuildError(f"未知匹配模型：{mode}")
        self.mode = mode
        self.units = units
        self.exact_first: dict[str, int] = {}
        self.suffix_first: dict[str, int] = {}
        self.keywords: list[tuple[str, int]] = []
        for i, u in enumerate(units):
            for e in u.raw.exact:
                self.exact_first.setdefault(e, i)
            for s in u.raw.suffix:
                self.suffix_first.setdefault(s, i)
            for k in u.raw.keyword:
                self.keywords.append((k, i))

    def suffix_hit(self, d: str):
        hits = [self.suffix_first[p] for p in parents(d) if p in self.suffix_first]
        return min(hits) if hits else None

    def keyword_hit(self, d: str):
        for k, i in self.keywords:
            if k in d:
                return i
        return None

    def match(self, d: str):
        if self.mode == "order":
            hits = [i for i in (self.exact_first.get(d), self.suffix_hit(d), self.keyword_hit(d)) if i is not None]
            return min(hits) if hits else None
        i = self.exact_first.get(d)
        if i is None:
            i = self.suffix_hit(d)
        if i is None:
            i = self.keyword_hit(d)
        return i

    def overrides(self) -> dict[int, list[str]]:
        """仅 type 模型：Mihomo 顺序下会被更早 host-suffix 抢先命中、且策略不同的 host 条目。"""
        if self.mode != "type":
            return {}
        out: dict[int, list[str]] = {}
        for e, qi in self.exact_first.items():
            si = self.suffix_hit(e)
            if si is not None and si < qi and self.units[si].policy != self.units[qi].policy:
                out.setdefault(qi, []).append(e)
        return {k: sorted(v) for k, v in out.items()}

    def dead_entries(self):
        """各单元中因更早单元而永远不会命中的条目：{(j, owner): [entries]}"""
        dead: dict[tuple[int, int], list[str]] = {}
        for j, u in enumerate(self.units):
            for e in u.norm.exact:
                i = self.match(e)
                if i is not None and i != j:
                    dead.setdefault((j, i), []).append(e)
            for s in u.norm.suffix:
                i = self.suffix_hit(s)
                if i is not None and i < j:
                    dead.setdefault((j, i), []).append("+." + s)
        return dead


# ---------------------------------------------------------------- 快照与锁文件

def published(manifest):
    return [rs for rs in manifest["rulesets"] if rs.get("publish", True)]


def _git(src_dir: Path, *args, text=True):
    r = subprocess.run(["git", "-C", str(src_dir), *args], capture_output=True, text=text)
    if r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise BuildError(f"git {' '.join(args)} 失败：{err.strip()}")
    return r.stdout


def _snapshot_matches_lock(snap_dir: Path, lock_path: Path) -> bool:
    """核对快照目录是否与锁文件中的哈希清单逐一完整匹配（且无多余文件）。"""
    if not snap_dir.is_dir() or not lock_path.is_file():
        return False
    try:
        lock = load_json(lock_path)
    except Exception:
        return False
    files = lock.get("files") if isinstance(lock, dict) else None
    if not isinstance(files, dict) or not files:
        return False
    for rel, info in files.items():
        if not isinstance(info, dict) or "sha256" not in info:
            return False
        p = snap_dir / rel
        if not p.is_file():
            return False
        try:
            if sha256_bytes(p.read_bytes()) != info["sha256"]:
                return False
        except OSError:
            return False
    actual = {p.relative_to(snap_dir).as_posix() for p in snap_dir.rglob("*") if p.is_file()}
    return actual == set(files.keys())


def recover_interrupted_import():
    """若上次导入中断并留下 .bak，先按锁文件哈希校验：
    - 正式快照与正式锁文件完整匹配 → 保留正式版本，清理遗留 .bak；
    - 正式版本不完整、但备份组合（含只移动了一半的情况）完整匹配 → 回滚到备份版本；
    - 两套都无法验证 → 保留现场并报错。
    """
    bak = SNAP.with_name(SNAP.name + ".bak")
    bak_lock = LOCK.with_name(LOCK.name + ".bak")
    if not bak.exists() and not bak_lock.exists():
        return
    if _snapshot_matches_lock(SNAP, LOCK):
        if bak.exists():
            shutil.rmtree(bak)
        if bak_lock.exists():
            bak_lock.unlink()
        return
    # 备份可能完整在 (bak, bak_lock)，也可能上次只来得及改名其中一个（如 (bak, LOCK) 或 (SNAP, bak_lock)）
    cand_snap = bak if bak.exists() else SNAP
    cand_lock = bak_lock if bak_lock.exists() else LOCK
    if _snapshot_matches_lock(cand_snap, cand_lock):
        if bak.exists():
            if SNAP.exists():
                shutil.rmtree(SNAP)
            bak.rename(SNAP)
        if bak_lock.exists():
            os.replace(bak_lock, LOCK)
        return
    raise BuildError(f"发现遗留备份（{bak.name} / {bak_lock.name}），且正式快照与备份均无法通过锁文件哈希校验；"
                     "为避免损坏数据已停止，请人工检查")


def import_upstream(manifest, src_dir: Path, ref: str = "HEAD"):
    """从上游仓库的指定提交读取文件（不读工作区），全部成功后才替换快照。"""
    recover_interrupted_import()
    src_dir = src_dir.resolve()
    commit = _git(src_dir, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    date = _git(src_dir, "log", "-1", "--format=%cI", commit).strip()
    paths = [rs["src"] for rs in published(manifest)] + manifest["upstream"]["extra_files"]
    blobs, missing = {}, []
    for rel in paths:
        r = subprocess.run(["git", "-C", str(src_dir), "show", f"{commit}:{rel}"], capture_output=True)
        if r.returncode != 0:
            missing.append(rel)
        else:
            blobs[rel] = r.stdout
    if missing:
        raise BuildError(f"上游提交 {commit[:12]} 缺少文件：{', '.join(missing)}；快照未改动")
    tmp = SNAP.with_name(SNAP.name + ".tmp")
    tmp_lock = LOCK.with_name(LOCK.name + ".tmp")
    bak = SNAP.with_name(SNAP.name + ".bak")
    bak_lock = LOCK.with_name(LOCK.name + ".bak")
    if tmp.exists():
        shutil.rmtree(tmp)
    if tmp_lock.exists():
        tmp_lock.unlink()
    # 1. 新快照与新锁文件全部写入临时位置
    for rel, data in blobs.items():
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    lock = {
        "_comment": "上游快照锁文件，由 tools/build.py --import-from 生成；文件内容取自 commit 对应的 Git 对象。",
        "repo": manifest["upstream"]["repo"],
        "commit": commit,
        "commit_date": date,
        "files": {rel: {"sha256": sha256_bytes(blobs[rel])} for rel in sorted(blobs)},
    }
    tmp_lock.write_text(dump_json(lock), encoding="utf-8", newline="\n")
    # 2. 旧快照、旧锁文件改名为备份；3. 新文件就位；任何一步失败都回滚
    moved = []
    try:
        if SNAP.exists():
            SNAP.rename(bak)
            moved.append("snap")
        if LOCK.exists():
            os.replace(LOCK, bak_lock)
            moved.append("lock")
        tmp.rename(SNAP)
        moved.append("new_snap")
        os.replace(tmp_lock, LOCK)
    except OSError as e:
        if "new_snap" in moved and SNAP.exists():
            shutil.rmtree(SNAP)
        if "snap" in moved and bak.exists():
            bak.rename(SNAP)
        if "lock" in moved and bak_lock.exists():
            os.replace(bak_lock, LOCK)
        raise BuildError(f"替换快照失败，已回滚到原快照与锁文件：{e}") from None
    # 4. 两者都更新成功后才清理备份
    if bak.exists():
        shutil.rmtree(bak)
    if bak_lock.exists():
        bak_lock.unlink()
    print(f"已从提交 {commit[:12]}（{date}）导入 {len(blobs)} 个文件")


def load_units(manifest, lock, skipped):
    units: list[Unit] = []
    for typ, value, policy in manifest["local_rules"]["domain"]:
        p = Parsed(exact=[value] if typ == "DOMAIN" else [], suffix=[value] if typ == "DOMAIN-SUFFIX" else [])
        units.append(Unit(f"local:{typ},{value}", policy, p, p))
    for rs in published(manifest):
        rel = rs["src"]
        entry = lock["files"].get(rel)
        if entry is None:
            raise BuildError(f"锁文件中没有 {rel}，请先运行 --import-from")
        data = (SNAP / rel).read_bytes()
        if sha256_bytes(data) != entry["sha256"]:
            raise BuildError(f"快照哈希不符：{rel}")
        raw = parse_qx(rel, data.decode("utf-8"), skipped)
        units.append(Unit(rs["name"], rs["policy"], raw, normalize(raw), rs))
    names = [u.name for u in units]
    if len(names) != len(set(names)):
        raise BuildError("规则集名称重复")
    return units


# ---------------------------------------------------------------- 渲染规则文件

@dataclass
class Provider:
    name: str
    behavior: str      # domain / classical / ipcidr
    path: str          # 仓库内相对路径
    policy: str
    kind: str          # priority / domain / keyword / ip
    payload: list


def rule_file(lock, manifest, u: Unit, prov: Provider, extra: str = "") -> str:
    rs = u.rs
    lic = rs.get("license", manifest["default_license"])
    head = [
        f"# 名称：{prov.name}（behavior: {prov.behavior}）",
        f"# 来源：{lock['repo'].removeprefix('https://github.com/')}@{lock['commit'][:12]} {rs['src']}",
        f"# 来源 SHA256：{lock['files'][rs['src']]['sha256']}",
        f"# 许可：{lic}",
        f"# 条目：{len(prov.payload)}",
    ]
    if extra:
        head.append(f"# 说明：{extra}")
    head.append(f"# {GENERATED_NOTE}")
    body = ["payload:"] + [f"  - {q(x)}" for x in prov.payload]
    return "\n".join(head + body) + "\n"


def build_providers(units: list[Unit], overrides: dict[int, list[str]], mode: str):
    """返回 (修正规则集, 域名层规则集序列, IP 规则集, 本地内联修正规则)。

    order 模型：每份来源的 keyword 规则集紧跟它的 domain 规则集；
    type 模型：全部 keyword 规则集放在全部 domain 规则集之后。
    """
    pri, dom, kw, ip = [], [], [], []
    inline_pri = []
    for i, u in enumerate(units):
        if u.rs is None:
            if i in overrides:
                inline_pri += [["DOMAIN", e, u.policy] for e in overrides[i]]
            continue
        base, d = u.rs["name"], u.rs["dir"]
        if i in overrides:
            pri.append(Provider(f"{base}_priority", "domain", f"rules/overrides/{base}_priority.yaml",
                                u.policy, "priority", overrides[i]))
        dp = u.norm.exact + ["+." + s for s in u.norm.suffix]
        if dp:
            dom.append(Provider(base, "domain", f"rules/{d}/{base}.yaml", u.policy, "domain", sorted(dp, key=lambda x: x.removeprefix("+."))))
        if u.norm.keyword:
            k = Provider(f"{base}_keyword", "classical", f"rules/{d}/{base}_keyword.yaml",
                         u.policy, "keyword", [f"DOMAIN-KEYWORD,{k}" for k in u.norm.keyword])
            (dom if mode == "order" else kw).append(k)
        if u.norm.ip:
            ip.append(Provider(f"{base}_ip", "ipcidr", f"rules/{d}/{base}_ip.yaml",
                               u.policy, "ip", [str(n) for n in u.norm.ip]))
    return pri, dom + kw, ip, inline_pri


# ---------------------------------------------------------------- 渲染主配置

def unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def render_config(manifest, lock, groups_prov, inline_pri, mode="http", file_root: Path | None = None,
                  hosts: dict | None = None) -> str:
    pri, dom, ip = groups_prov
    model = manifest.get("qx_match_model", "order")
    regions = [r["name"] for r in manifest["regions"]]
    pub = manifest["publish"]
    L = []
    a = L.append
    a("# ClashVerge-rules 完整配置模板（Mihomo / Clash Verge Rev）")
    a(f"# 规则来源：{lock['repo']} @ {lock['commit'][:12]}；{GENERATED_NOTE}")
    a("# 使用前把 proxy-providers.airport.url 替换为你自己的订阅地址（不要提交真实地址）。")
    a(f"# 匹配模型：{model}（见 README「匹配模型」）。地区组没有匹配节点时走 REJECT，不会静默直连。")
    if mode != "http":
        a(f"mixed-port: {RUNTIME_MIXED_PORT}")
        a(f"external-controller: 127.0.0.1:{RUNTIME_CONTROLLER_PORT}")
    a("mode: rule")
    a("log-level: info")
    a("ipv6: true")
    a("unified-delay: true")
    a("tcp-concurrent: true")
    a("")
    a("dns:")
    a("  enable: true")
    a("  ipv6: true")
    a("  enhanced-mode: fake-ip")
    a("  fake-ip-range: 198.18.0.1/16")
    a("  fake-ip-filter: ['*.lan', '*.local', '*.arpa', 'time.*.com', 'ntp.*.com', '+.market.xiaomi.com', 'localhost.ptlogin2.qq.com', '*.msftncsi.com', 'www.msftconnecttest.com']")
    if mode == "http":
        a("  default-nameserver: [223.5.5.5, 119.29.29.29]")
        a("  nameserver: ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query']")
        a("  proxy-server-nameserver: ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query']")
    else:
        a("  # 运行时校验：DNS 指向不可达地址，让直连快速失败并写出匹配日志，不产生真实外网流量")
        a("  default-nameserver: ['127.0.0.1:1']")
        a("  nameserver: ['127.0.0.1:1']")
    if hosts:
        a("")
        a("hosts:")
        for k, v in hosts.items():
            a(f"  {q(k)}: {q(v)}")
    a("")
    a("proxy-providers:")
    a("  airport:")
    if mode == "http":
        a("    type: http")
        a("    url: 'https://example.com/your-subscription-url'  # ← 替换为你的订阅地址")
        a("    path: ./proxy_providers/airport.yaml")
        a("    interval: 86400")
    else:
        a("    type: file")
        a(f"    path: {q((file_root / 'dummy-proxies.yaml').as_posix())}")
    a("    health-check: {enable: true, url: 'https://www.gstatic.com/generate_204', interval: 600}")
    a("")
    a("proxy-groups:")
    a(f"  - {{name: Global, type: select, proxies: [{', '.join(q(x) for x in regions + ['DIRECT'])}]}}")
    for r in manifest["regions"]:
        a(f"  - {{name: {q(r['name'])}, type: url-test, use: [airport], filter: {q(r['filter'])}, "
          f"empty-fallback: REJECT, "
          f"url: 'https://www.gstatic.com/generate_204', interval: 600, tolerance: 20, lazy: true}}")
    for g in manifest["groups"]:
        choices = g.get("choices") or unique([g["default"], "Global"] + regions + ["DIRECT"])
        choices = unique([g["default"]] + choices)
        a(f"  - {{name: {q(g['name'])}, type: select, proxies: [{', '.join(q(x) for x in choices)}]}}")
    a("")
    a("rule-providers:")
    for p in pri + dom + ip:
        a(f"  {p.name}:")
        if mode == "http":
            a("    type: http")
            a(f"    url: {q(pub['base_url'] + '/' + p.path)}")
            a(f"    path: {pub['provider_path_prefix']}/{p.path.removeprefix('rules/')}")
            a("    interval: 86400")
            a("    proxy: Global")
        else:
            a("    type: file")
            a(f"    path: {q((file_root / p.path).as_posix())}")
        a(f"    behavior: {p.behavior}")
        a("    format: yaml")
    a("")
    a("rules:")
    loc = manifest["local_rules"]
    if inline_pri or pri:
        a("  # ① type 模型修正：host 精确条目优先于更早资源的 host-suffix")
    for t, v, pol in inline_pri:
        a(f"  - {q(f'{t},{v},{pol}')}")
    for p in pri:
        a(f"  - {q(f'RULE-SET,{p.name},{p.policy}')}")
    a("  # ② 本地域名规则（QX [filter_local]）")
    for t, v, pol in loc["domain"]:
        a(f"  - {q(f'{t},{v},{pol}')}")
    if model == "order":
        a("  # ③ 域名规则集，按 QX [filter_remote] 顺序（keyword 紧跟所属来源）")
    else:
        a("  # ③ 域名规则集，按 QX [filter_remote] 顺序；keyword 规则集统一放在最后")
    for p in dom:
        a(f"  - {q(f'RULE-SET,{p.name},{p.policy}')}")
    a("  # ④ 本地 IP 规则与 GeoIP（放在全部域名规则之后；这一层会触发 DNS 解析）")
    for t, v, pol in loc["ip"]:
        a(f"  - {q(f'{t},{v},{pol}')}")
    final = loc["final"]
    for t, v, pol in final[:-1]:
        a(f"  - {q(f'{t},{v},{pol}')}")
    if ip:
        a("  # ⑤ 规则集中的 IP 条件")
    for p in ip:
        a(f"  - {q(f'RULE-SET,{p.name},{p.policy}')}")
    t, v, pol = final[-1]
    a("  # ⑥ 兜底")
    a(f"  - {q(f'{t},{pol}')}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 按生成产物模拟 Mihomo

NON_DOMAIN = "（非域名层：IP / GeoIP / MATCH）"
MIHOMO_RULE_LABEL = {"DOMAIN": "Domain", "DOMAIN-SUFFIX": "DomainSuffix", "DOMAIN-KEYWORD": "DomainKeyword"}


def read_payload(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if line.startswith("  - "):
            out.append(line[4:].strip().strip("'").replace("''", "'"))
    return out


class MihomoSim:
    """读取生成的 rules 段与规则文件，按 Mihomo 顺序匹配域名（只模拟域名层）。"""

    def __init__(self, config_text: str, outputs: dict[str, str], providers: list[Provider]):
        by_name = {p.name: p for p in providers}
        self.rules = []
        in_rules = False
        for line in config_text.splitlines():
            if line.startswith("rules:"):
                in_rules = True
                continue
            if in_rules and line.startswith("  - "):
                parts = line[4:].strip().strip("'").split(",")
                if parts[0] == "RULE-SET":
                    p = by_name[parts[1]]
                    label = f"RuleSet/{parts[1]}"
                    payload = read_payload(outputs[p.path])
                    if p.behavior == "domain":
                        ex = {x for x in payload if not x.startswith("+.")}
                        sx = {x[2:] for x in payload if x.startswith("+.")}
                        self.rules.append(("SET", (ex, sx), parts[2], label))
                    elif p.behavior == "classical":
                        kws = [x.split(",", 1)[1] for x in payload if x.startswith("DOMAIN-KEYWORD,")]
                        self.rules.append(("KW", kws, parts[2], label))
                    else:
                        self.rules.append(("STOP", None, None, None))
                elif parts[0] in MIHOMO_RULE_LABEL:
                    self.rules.append((parts[0], parts[1], parts[2], f"{MIHOMO_RULE_LABEL[parts[0]]}/{parts[1]}"))
                else:
                    self.rules.append(("STOP", None, None, None))

    def match(self, d: str) -> str:
        return self.match_rule(d)[0]

    def match_rule(self, d: str) -> tuple[str, str | None]:
        """返回 (策略组, Mihomo 日志中的规则标识)，例如 ("YouTube", "RuleSet/google-youtube")。"""
        for t, v, pol, label in self.rules:
            if t == "STOP":
                return NON_DOMAIN, None
            if t == "DOMAIN" and d == v:
                return pol, label
            if t == "DOMAIN-SUFFIX" and (d == v or d.endswith("." + v)):
                return pol, label
            if t == "DOMAIN-KEYWORD" and v in d:
                return pol, label
            if t == "SET":
                ex, sx = v
                if d in ex or any(p in sx for p in parents(d)):
                    return pol, label
            if t == "KW" and any(k in d for k in v):
                return pol, label
        return NON_DOMAIN, None


# ---------------------------------------------------------------- 报告

def render_report(manifest, lock, units, model, overrides, dead, skipped, equiv, accept) -> str:
    L = []
    a = L.append
    a("# 构建报告")
    a("")
    a(f"> {GENERATED_NOTE}。上游：{lock['repo']} @ `{lock['commit'][:12]}`（{lock['commit_date']}）")
    a("")
    a("## 规则集")
    a("")
    a("| 序 | 规则集 | 策略组 | host | host-suffix | keyword | IP | 去重后 domain |")
    a("|---:|---|---|---:|---:|---:|---:|---:|")
    n = 0
    for u in units:
        if u.rs is None:
            continue
        n += 1
        a(f"| {n} | `{u.name}` | {u.policy} | {len(u.raw.exact)} | {len(u.raw.suffix)} | "
          f"{len(u.raw.keyword)} | {len(u.raw.ip)} | {len(u.norm.exact) + len(u.norm.suffix)} |")
    a("")
    a("## 未发布的规则集")
    a("")
    unpub = [rs for rs in manifest["rulesets"] if not rs.get("publish", True)]
    for rs in unpub:
        a(f"- `{rs['name']}`（`{rs['src']}`）：{rs.get('note', '')}；许可：{rs.get('license', '')}")
    if not unpub:
        a("无。")
    a("")
    a("## 跳过的条件（Mihomo 无对应规则类型）")
    a("")
    for rel, ln, kind, s in skipped:
        a(f"- `{rel}:{ln}` `{kind}`：`{s}`")
    if not skipped:
        a("无。")
    a("")
    a("## 匹配模型")
    a("")
    a(f"当前模型：`{model.mode}`。order = 按资源顺序先到先得；type = host > host-suffix > host-keyword 类型优先。"
      "两种模型都**尚未在 QX 中实测确认**，见 README「匹配模型」。")
    a("")
    if model.mode == "type":
        a("type 模型修正：以下 host 精确条目会被更早资源的 host-suffix 抢先命中，因此提升到 `rules/overrides/*_priority.yaml`。")
        a("")
        if not overrides:
            a("无。")
        for i, es in sorted(overrides.items()):
            u = units[i]
            a(f"- `{u.name}` → {u.policy}：{len(es)} 条")
            for e in es[:30]:
                a(f"  - `{e}`（按顺序会命中 `{units[model.suffix_hit(e)].name}`）")
            if len(es) > 30:
                a(f"  - …… 其余 {len(es) - 30} 条见规则文件")
    else:
        alt = QXModel(units, "type").overrides()
        a(f"如果 QX 实际采用 type 模型，有 {sum(len(v) for v in alt.values())} 个 host 条目的结果会与当前模板不同，例如：")
        a("")
        shown = 0
        for i, es in sorted(alt.items()):
            for e in es[:3]:
                a(f"- `{e}`：order → `{units[model.match(e)].name}`（{units[model.match(e)].policy}），"
                  f"type → `{units[i].name}`（{units[i].policy}）")
                shown += 1
        if not alt:
            a("无。")
    a("")
    a("## 遮蔽情况（仅供审查）")
    a("")
    a("后序规则集中被前序规则集覆盖、永远不会命中的条目。")
    a("")
    a("| 被遮蔽规则集 | 先命中的规则集 | 条目数 | 示例 |")
    a("|---|---|---:|---|")
    for (j, i), es in sorted(dead.items()):
        a(f"| `{units[j].name}` | `{units[i].name}` | {len(es)} | {', '.join('`' + e + '`' for e in es[:4])} |")
    a("")
    a("## 域名模型比对")
    a("")
    a("只说明生成配置的**域名层**与本脚本采用的 QX 模型一致；不覆盖 IP / GeoIP / MATCH，也不能证明 QX 的实际行为。"
      "IP 层与策略组的运行时检查见 `python tools/build.py --verify`。")
    a("")
    a(f"- 比对域名数：{equiv['total']}（全部 host、host-suffix 根域及一个虚拟子域）")
    a(f"- 结果不一致：{len(equiv['diff'])}")
    a("")
    a("## 域名验收用例（离线模拟）")
    a("")
    a("| 域名 | 期望策略组 | 生成配置命中 | 结果 |")
    a("|---|---|---|---|")
    for d, exp, got in accept:
        a(f"| `{d}` | {exp} | {got} | {'✅' if exp == got else '❌'} |")
    return "\n".join(L) + "\n"



# ---------------------------------------------------------------- 主流程

def expected_for(exp, mode: str) -> str:
    """验收期望值可以是字符串，也可以是 {"order": ..., "type": ...}。"""
    return exp[mode] if isinstance(exp, dict) else exp


def build_outputs(manifest, lock):
    skipped: list = []
    units = load_units(manifest, lock, skipped)
    mode = manifest.get("qx_match_model", "order")
    model = QXModel(units, mode)
    overrides = model.overrides()
    pri, dom, ip, inline_pri = build_providers(units, overrides, mode)
    providers = pri + dom + ip
    unit_by_name = {u.name: u for u in units}

    outputs: dict[str, str] = {}
    for p in providers:
        base = p.name.removesuffix("_priority").removesuffix("_keyword").removesuffix("_ip") \
            if p.kind != "domain" else p.name
        extra = "type 模型修正条目（host 精确匹配优先于更早资源的 host-suffix）" if p.kind == "priority" else ""
        outputs[p.path] = rule_file(lock, manifest, unit_by_name[base], p, extra)
    config = render_config(manifest, lock, (pri, dom, ip), inline_pri)
    outputs["examples/mihomo.yaml"] = config

    # 域名层比对：QX 模型 vs 生成配置
    sim = MihomoSim(config, outputs, providers)
    probes = set(model.exact_first) | set(model.suffix_first) | {"cvr-probe." + s for s in model.suffix_first}
    diff = []
    for d in sorted(probes):
        i = model.match(d)
        exp = units[i].policy if i is not None else NON_DOMAIN
        got = sim.match(d)
        if exp != got:
            diff.append((d, exp, got))
    equiv = {"total": len(probes), "diff": diff}
    accept = [(d, expected_for(exp, mode), sim.match(d)) for d, exp in manifest.get("acceptance", [])]

    dead = model.dead_entries()
    outputs["reports/build-report.md"] = render_report(
        manifest, lock, units, model, overrides, dead, skipped, equiv, accept)
    return outputs, equiv, accept, skipped, (pri, dom, ip, inline_pri), sim


def managed_existing() -> set[str]:
    found = set()
    for base in ("rules",):
        d = ROOT / base
        if d.exists():
            for f in d.rglob("*.yaml"):
                found.add(f.relative_to(ROOT).as_posix())
    return found


def report_problems(equiv, accept, skipped) -> bool:
    ok = True
    for rel, ln, kind, s in skipped:
        print(f"[跳过] {rel}:{ln} 不支持的条件类型 {kind}：{s}")
    if equiv["diff"]:
        ok = False
        print(f"[错误] QX 与生成配置结果不一致 {len(equiv['diff'])} 处：")
        for d, e, g in equiv["diff"][:20]:
            print(f"    {d}: QX={e} Mihomo={g}")
    for d, e, g in accept:
        if e != g:
            ok = False
            print(f"[错误] 验收用例 {d}: 期望 {e}，实际 {g}")
    return ok


def cmd_build(check: bool) -> int:
    manifest, lock = load_json(MANIFEST), load_json(LOCK)
    outputs, equiv, accept, skipped, _, _ = build_outputs(manifest, lock)
    ok = report_problems(equiv, accept, skipped)
    stale = managed_existing() - set(outputs)
    if check:
        changed = []
        for rel, content in outputs.items():
            f = ROOT / rel
            if not f.exists() or f.read_bytes() != content.encode("utf-8"):
                changed.append(rel)
        for rel in changed:
            print(f"[不一致] {rel}")
        for rel in sorted(stale):
            print(f"[多余] {rel}")
        if changed or stale:
            ok = False
            print("产物与仓库不一致，请运行 python tools/build.py 后提交。")
        print("检查通过。" if ok else "检查失败。")
        return 0 if ok else 1
    for rel, content in outputs.items():
        f = ROOT / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content.encode("utf-8"))
    for rel in stale:
        (ROOT / rel).unlink()
    print(f"已生成 {len(outputs)} 个文件；域名模型（{manifest.get('qx_match_model', 'order')}）比对 {equiv['total']} 个域名，"
          f"不一致 {len(equiv['diff'])}；域名验收用例 {sum(e == g for _, e, g in accept)}/{len(accept)} 通过。")
    return 0 if ok else 1


def find_mihomo(arg: str | None) -> str:
    cands = [arg, os.environ.get("MIHOMO"), shutil.which("mihomo"),
             r"C:\Program Files\Clash Verge\verge-mihomo.exe"]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise BuildError("找不到 Mihomo 内核，请用 --mihomo 指定或设置环境变量 MIHOMO")


RUNTIME_MIXED_PORT = 17897
RUNTIME_CONTROLLER_PORT = 19097
LOG_OK = re.compile(r"--> (?P<host>\[?[^\s\]]+\]?):\d+ match (?P<rule>.+?) using (?P<chain>.+?)\"?$")
LOG_ERR = re.compile(r"dial (?P<chain>.+?) \(match (?P<rule>[^)]*)\) .*?--> (?P<host>\[?[^\s\]]+\]?):\d+ error")
REGION_LABELS = {"Hong Kong": "🇭🇰 HongKong", "Taiwan": "🇹🇼 Taiwan", "Japan": "🇯🇵 Japan",
                 "Singapore": "🇸🇬 Singapore", "United States": "🇺🇸 UnitedStates"}


def _http_json(path: str):
    import urllib.parse
    import urllib.request
    url = f"http://127.0.0.1:{RUNTIME_CONTROLLER_PORT}{urllib.parse.quote(path)}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不走系统代理
    with opener.open(url, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def _connect(target: str, timeout: float):
    import socket
    host = f"[{target}]" if ":" in target else target
    try:
        with socket.create_connection(("127.0.0.1", RUNTIME_MIXED_PORT), timeout=timeout) as s:
            s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
            s.recv(256)
    except OSError:
        pass


def _resolve_chain(name: str, depth=0) -> list[str]:
    """沿 /proxies 的 now 字段展开策略组链路。"""
    if depth > 8:
        return [name]
    import urllib.error
    try:
        info = _http_json(f"/proxies/{name}")
    except urllib.error.HTTPError:
        return [name]  # 来自 proxy-provider 的节点不在 /proxies 中，视为链路终点
    now = info.get("now")
    if not now:
        return [name]
    return [name] + _resolve_chain(now, depth + 1)


def run_scenario(exe, manifest, lock, prov, tdp: Path, drop_regions: list[str], cases):
    import time
    pri, dom, ip, inline_pri = prov
    dummy = ["proxies:"]
    for i, r in enumerate(manifest["regions"]):
        if r["name"] in drop_regions:
            continue
        label = REGION_LABELS.get(r["name"], r["name"])
        dummy.append(f"  - {{name: {q(label + ' 01')}, type: socks5, server: 127.0.0.1, port: {10801 + i}}}")
    (tdp / "dummy-proxies.yaml").write_text("\n".join(dummy) + "\n", encoding="utf-8")
    # 域名用例默认映射到 127.0.0.1，让直连立即被拒绝并写出日志；用例可用 hosts 字段覆盖（用于测试 IP 层）
    hosts = {c["target"]: c.get("hosts", "127.0.0.1") for c in cases if not IP_LITERAL.match(c["target"])}
    cfg = render_config(manifest, lock, (pri, dom, ip), inline_pri, mode="file", file_root=tdp, hosts=hosts)
    (tdp / "config.yaml").write_text(cfg, encoding="utf-8")
    env = dict(os.environ)
    env["SAFE_PATHS"] = os.pathsep.join(filter(None, [str(ROOT), env.get("SAFE_PATHS")]))
    log_path = tdp / "mihomo.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen([exe, "-d", str(tdp), "-f", str(tdp / "config.yaml")],
                                stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            for _ in range(50):
                try:
                    _http_json("/version")
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                key = [l for l in log_text.splitlines() if "level=info msg=\"Start initial provider" not in l]
                raise BuildError("Mihomo 控制接口未就绪：\n" + "\n".join(key[-40:]))
            chains = {g: _resolve_chain(g) for g in
                      ["Global"] + [r["name"] for r in manifest["regions"]] + [g["name"] for g in manifest["groups"]]}
            import threading
            threads = [threading.Thread(target=_connect, args=(c["target"], 10)) for c in cases]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            time.sleep(2)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    keep = ROOT / ".cache" / f"verify-{'-'.join(drop_regions) or 'full'}.log".replace(" ", "_")
    keep.parent.mkdir(exist_ok=True)
    keep.write_text(text, encoding="utf-8")
    hits = {}
    for line in text.splitlines():
        m = LOG_OK.search(line) or LOG_ERR.search(line)
        if m:
            hits.setdefault(m["host"].strip("[]"), (normalize_rule(m["rule"]), m["chain"].strip()))
    return chains, hits, text


IP_LITERAL = re.compile(r"^[\d.:a-fA-F]+$")


def normalize_rule(s: str) -> str:
    """统一 Mihomo 两种日志写法：RuleSet(x) / RuleSet/x -> RuleSet/x；Match() / Match/ -> Match/"""
    s = s.strip()
    m = re.match(r"^(\w+)(?:\((.*)\)|/(.*))?$", s)
    if not m:
        return s
    return f"{m.group(1)}/{m.group(2) if m.group(2) is not None else (m.group(3) or '')}"


def region_names(manifest) -> list[str]:
    return [r["name"] for r in manifest["regions"]]


def expected_chain(manifest, name: str, present: set[str], depth=0) -> list[str]:
    """按模板默认选择推算策略组链路：服务组 → 默认项 → … → 地区组 → 假节点 / REJECT。"""
    if depth > 8 or name in ("DIRECT", "REJECT"):
        return [name]
    if name == "Global":
        return ["Global"] + expected_chain(manifest, region_names(manifest)[0], present, depth + 1)
    if name in region_names(manifest):
        leaf = REGION_LABELS.get(name, name) + " 01" if name in present else "REJECT"
        return [name, leaf]
    for g in manifest["groups"]:
        if g["name"] == name:
            return [name] + expected_chain(manifest, g["default"], present, depth + 1)
    raise BuildError(f"未知策略组：{name}")


def all_group_names(manifest) -> list[str]:
    return ["Global"] + region_names(manifest) + [g["name"] for g in manifest["groups"]]


def runtime_cases(manifest, mode: str, sim: MihomoSim) -> list[dict]:
    """组装运行时用例：域名用例的期望规则由生成配置推算，IP 用例的期望规则写在 manifest 中。"""
    cases = []
    for d, e in manifest.get("acceptance", []):
        pol = expected_for(e, mode)
        sim_pol, rule = sim.match_rule(d)
        if sim_pol != pol or rule is None:
            raise BuildError(f"域名用例 {d} 在生成配置中命中 {sim_pol}，与期望 {pol} 不符；先运行 --check")
        cases.append({"target": d, "policy": pol, "rule": rule})
    for c in manifest.get("runtime_cases", []):
        cases.append(dict(c))
    return cases


def evaluate_scenario(manifest, cases, chains, hits, present: set[str]) -> list[tuple[bool, str]]:
    """逐项断言，返回 [(是否通过, 说明)]。纯函数，便于单元测试。

    1. 每个策略组的实际链路 == 按默认选择推算的链路（覆盖地区组末端节点）；
    2. 每个用例：命中规则标识、首个策略组、链路末端都与期望一致。
    """
    out = []
    for g in all_group_names(manifest):
        exp = expected_chain(manifest, g, present)
        got = chains.get(g)
        out.append((got == exp, f"策略组 {g}: {' → '.join(got or ['（无）'])}（期望 {' → '.join(exp)}）"))
    for c in cases:
        t, pol, rule = c["target"], c["policy"], c["rule"]
        if t not in hits:
            out.append((False, f"{t}: 日志中没有匹配记录"))
            continue
        got_rule, chain = hits[t]
        first = chain.split("[")[0].strip()
        exp_leaf = expected_chain(manifest, pol, present)[-1]
        got_leaf = (chains.get(first) or [first])[-1]
        log_leaf = chain[chain.find("[") + 1:].rstrip("]") if "[" in chain else None
        problems = []
        if got_rule != rule:
            problems.append(f"规则 {got_rule} ≠ {rule}")
        if first != pol:
            problems.append(f"策略组 {first} ≠ {pol}")
        if got_leaf != exp_leaf:
            problems.append(f"末端 {got_leaf} ≠ {exp_leaf}")
        if log_leaf is not None and log_leaf != exp_leaf:
            problems.append(f"日志末端 {log_leaf} ≠ {exp_leaf}")
        out.append((not problems, f"{t}: {got_rule} → {first} → {got_leaf}"
                    + (f"（{'；'.join(problems)}）" if problems else "")))
    return out


def cmd_verify(mihomo_arg: str | None, model_override: str | None = None) -> int:
    manifest, lock = load_json(MANIFEST), load_json(LOCK)
    if model_override:
        manifest["qx_match_model"] = model_override
    mode = manifest.get("qx_match_model", "order")
    outputs, _, _, _, prov, sim = build_outputs(manifest, lock)
    exe = find_mihomo(mihomo_arg)
    print(f"内核：{exe}；匹配模型：{mode}")
    cases = runtime_cases(manifest, mode, sim)
    ok = True
    scenarios = [("完整订阅", []), ("缺少 United States 节点", ["United States"])]
    for title, drop in scenarios:
        with tempfile.TemporaryDirectory(prefix="cvr-verify-") as td:
            tdp = Path(td)
            # 使用本次构建的内存产物，而不是磁盘上可能过期的 rules/
            for rel, content in outputs.items():
                if rel.startswith("rules/"):
                    f = tdp / rel
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(content.encode("utf-8"))
            appdata = os.environ.get("APPDATA")
            if appdata:
                for name in ("Country.mmdb", "geoip.metadb", "geoip.dat", "geosite.dat", "ASN.mmdb"):
                    src = Path(appdata) / "io.github.clash-verge-rev.clash-verge-rev" / name
                    if src.exists():
                        shutil.copy(src, tdp / name)
            env = dict(os.environ)
            env["SAFE_PATHS"] = os.pathsep.join(filter(None, [str(ROOT), env.get("SAFE_PATHS")]))
            pri, dom, ip, inline_pri = prov
            (tdp / "dummy-proxies.yaml").write_text("proxies: []\n", encoding="utf-8")
            (tdp / "config.yaml").write_text(
                render_config(manifest, lock, (pri, dom, ip), inline_pri, mode="file", file_root=tdp), encoding="utf-8")
            t = subprocess.run([exe, "-t", "-d", str(tdp), "-f", str(tdp / "config.yaml")], env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if t.returncode != 0:
                print((t.stdout + t.stderr).strip())
                return t.returncode
            chains, hits, log = run_scenario(exe, manifest, lock, prov, tdp, drop, cases)
        present = set(region_names(manifest)) - set(drop)
        results = evaluate_scenario(manifest, cases, chains, hits, present)
        passed = sum(r for r, _ in results)
        print(f"\n== 场景：{title}（{passed}/{len(results)}）==")
        for good, msg in results:
            print(f"  {'✅' if good else '❌'} {msg}")
        ok &= passed == len(results)
        if not hits:
            print(log[-3000:])
    print("\n运行时校验通过。" if ok else "\n运行时校验失败。")
    print("说明：本校验使用本地规则文件与假节点，不覆盖远程规则下载和真实出口；这两项需推送后在 Clash Verge Rev 中实测。")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="ClashVerge-rules 构建工具")
    ap.add_argument("--check", action="store_true", help="离线重建并与仓库比对")
    ap.add_argument("--import-from", metavar="DIR", help="从上游仓库目录导入快照（读取 Git 对象，不读工作区）")
    ap.add_argument("--ref", default="HEAD", help="配合 --import-from 指定上游提交 / 标签，默认 HEAD")
    ap.add_argument("--verify", action="store_true", help="启动 Mihomo 内核做运行时校验")
    ap.add_argument("--mihomo", metavar="EXE", help="Mihomo 可执行文件路径")
    ap.add_argument("--model", choices=["order", "type"], help="配合 --verify 临时指定匹配模型（不改 manifest 与产物）")
    args = ap.parse_args()
    try:
        if args.import_from:
            import_upstream(load_json(MANIFEST), Path(args.import_from), args.ref)
            return 0
        if args.verify:
            return cmd_verify(args.mihomo, args.model)
        return cmd_build(args.check)
    except BuildError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
