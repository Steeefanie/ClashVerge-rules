# ClashVerge-rules

适用于 **Mihomo / Clash Verge Rev** 的分流规则集与完整配置模板，由
[Steeefanie/QuantumultX-resources](https://github.com/Steeefanie/QuantumultX-resources) 的分流部分转换而来。

- 规则集只保存匹配条件，流量去向统一写在主配置中。
- 上游文件按固定提交（从 Git 对象读取）快照到 `sources/qx/`，可**离线重建**，哈希记录在 `sources/upstream.lock.json`。
- 首版范围 = 原 QX 主配置 `[filter_remote]` 实际启用的 28 份规则，减去暂不发布的重写配套广告合集，共 27 份。

> [!IMPORTANT]
> 当前状态：**v0.1.0 发布前验收中**。离线构建、域名模型比对、本地内核运行时校验已通过；
> QX 匹配模型实测、远程规则下载、Clash Verge Rev 真实出口尚待验证，见「验收状态」。

## 目录结构

```text
ClashVerge-rules/
├── rules/
│   ├── services/        # 按服务划分（openai、google-youtube、microsoft …）
│   ├── collections/     # 综合合集（domestic、global、advertising、google-without-youtube）
│   ├── custom/          # 个人维护（OneDrive 网页端 / 同步端）
│   └── overrides/       # 仅 type 匹配模型下生成（见「匹配模型」）
├── examples/mihomo.yaml # 完整配置模板（策略组 + 规则顺序）
├── sources/
│   ├── manifest.json        # 规则集清单、顺序、策略组、匹配模型、验收用例
│   ├── upstream.lock.json   # 上游仓库、提交、文件哈希
│   └── qx/                  # 上游原始文件快照
├── tools/build.py       # 转换 / 去重 / 模型比对 / 运行时校验
└── reports/build-report.md
```

## 快速开始（Clash Verge Rev）

1. 下载 [`examples/mihomo.yaml`](examples/mihomo.yaml)，把 `proxy-providers.airport.url` 换成你的订阅地址。
   **带真实订阅地址的文件只保存在本机，不要提交。**
2. Clash Verge Rev →「订阅」→「新建」→ 类型选「Local」，导入上一步的文件并启用。
3. 首次启动时，规则集会通过 `Global` 组从 GitHub 下载到 `./ruleset/clashverge-rules/`；之后每 24 小时更新一次。
   仓库推送到 GitHub 之前，这些地址无法访问。

### 策略组

| 策略组 | 默认 | 说明 |
|---|---|---|
| Global | Hong Kong | 总出口，可切到其他地区或 DIRECT |
| Hong Kong / Taiwan / Japan / Singapore / United States | 自动测速 | 按节点名正则筛选（沿用原 QX 配置），排除流量、到期、beta 等信息节点 |
| 广告拦截 | REJECT | 可临时改为 DIRECT |
| OpenAI / Anthropic / Gemini / Spotify | United States | |
| TikTok / Netflix / Disney | Singapore | |
| OneDrive-Web / Steam | Hong Kong | |
| OneDrive-Sync / 国内服务 / Apple | DIRECT | 国内服务含哔哩哔哩、小红书、DeepSeek、腾讯 |
| YouTube / Google / GitHub / Microsoft / Telegram / X / Meta / Wiki / LinkedIn | Global | |

> [!WARNING]
> **地区组没有匹配节点时会拒绝连接，而不是直连。** 地区组设置了 `empty-fallback: REJECT`：
> 如果订阅里没有美国节点，或节点名称没被正则识别，默认走 United States 的 OpenAI、Anthropic、Gemini、Spotify 会**连接失败**，
> 不会悄悄变成直连。遇到这种情况，请把对应服务组切到其他地区或 Global。
> 不设置这一项时，Mihomo 默认回退到 `COMPATIBLE`，效果等同于直连。

> [!NOTE]
> **GitHub 组的作用范围**：原 QX 配置在 `[filter_local]` 中把 `github.com`、`githubusercontent.com` 固定走 Global，模板保留了这一行为。具体效果取决于匹配模型：
> - **`order`（默认）**：切换 GitHub 组**不影响** `github.com` 及其全部子域，也不影响 `*.githubusercontent.com`；
>   只影响 `microsoft-github` 规则集中的其他域名（如 `githubassets.com`、`github.io`）。
> - **`type`**：`microsoft-github` 中的 3 个精确条目（`copilot-proxy.githubusercontent.com`、`copilot-telemetry.githubusercontent.com`、
>   `copilot-telemetry-service.githubusercontent.com`）会被提到本地后缀规则之前，改走 GitHub 组；其余与 order 相同。
>
> 如果想让 GitHub 组完全接管，删掉模板中这两条 `DOMAIN-SUFFIX` 规则即可。

## 匹配模型

Mihomo 严格按 `rules:` 自上而下匹配。Quantumult X 的匹配方式有两种说法，**目前都没有官方文档或实测确认**：

| 模型 | 含义 | 模板做法 |
|---|---|---|
| `order`（默认） | 按资源加载顺序，先命中先得 | 规则集按原 `[filter_remote]` 顺序排列 |
| `type` | 先比类型：`host` > `host-suffix` > `host-keyword`，同类型内再按顺序 | 额外生成 `rules/overrides/*_priority.yaml`，放在最前面 |

两种模型只在少数域名上结果不同（type 模型下需修正 68 个 host 条目）。典型例子是 `adservice.google.com`：
它在广告合集中是 `host-suffix`，在 Google 合集中是 `host`。按 order 模型会被拦截，按 type 模型会走 Google。

**在 QX 中确认的方法**：用原主配置访问 `adservice.google.com`，在「工具 → 请求记录」中看命中的规则：
命中「广告与跟踪」（reject）对应 order，命中「Google」对应 type。
这个测试只能确定**这一类冲突**（host 精确条目与更早资源的 host-suffix）在原配置里怎么处理，不能证明 QX 其他方面的匹配行为。
确认后把 `sources/manifest.json` 中的 `qx_match_model` 设为对应值，再运行 `python tools/build.py`。

两种模型共同的处理：

1. 本地域名规则（原 `[filter_local]`）放在所有规则集之前。
2. 本地 IP 规则、`GEOIP,CN` 和规则集中的 IP 条件放在**全部域名规则之后**。这一层会触发 DNS 解析；
   原配置中的 IP 规则都针对 IP 直连（局域网、HTTPDNS），按目前的判断，放在后面对结果影响很小。
3. 最后是 `MATCH,Global`。

**OneDrive**：Web 规则在 Sync 之前，两者都在 Microsoft 之前。`onedrive.com`、`onedrive.live.com` 是 Web 的精确条目，
它们的子域（如 `api.onedrive.com`、`*.sharepoint.com`）命中 Sync。规则按目标域名划分，
网页和同步客户端访问同一个域名时会命中同一条规则。

## 规则集地址

```text
https://raw.githubusercontent.com/Steeefanie/ClashVerge-rules/main/rules/<目录>/<名称>.yaml
https://cdn.jsdelivr.net/gh/Steeefanie/ClashVerge-rules@main/rules/<目录>/<名称>.yaml
```

把 `main` 换成标签（如 `v0.1.0`）即可固定版本。每份来源最多拆成三个文件：

| 文件 | behavior | 内容 |
|---|---|---|
| `<名称>.yaml` | `domain` | `host`（精确）与 `host-suffix`（`+.` 前缀） |
| `<名称>_keyword.yaml` | `classical` | `DOMAIN-KEYWORD` |
| `<名称>_ip.yaml` | `ipcidr` | IPv4 / IPv6 网段 |

## 转换规则

| Quantumult X | Mihomo |
|---|---|
| `host` | `domain` 规则集中的精确条目 |
| `host-suffix` | `domain` 规则集中的 `+.` 条目 |
| `host-keyword` | `DOMAIN-KEYWORD` |
| `ip-cidr` / `ip6-cidr` | `ipcidr` 规则集 |
| `user-agent` | 无对应条件，构建时报告文件和行号并跳过（全库仅 `spotify.list:58` 一条） |

与原配置的规模对比：28 份规则共 40,359 条条件；减去未发布的广告合集（458 条）和 1 条 user-agent，转换了 39,900 条，
单文件内去掉冗余后为 37,845 条。Clash Verge Rev 的「规则」页把每个 `RULE-SET` 算作一条，所以只显示几十条，这不代表条件少了。

## 验收状态

| 项目 | 方式 | 状态 |
|---|---|---|
| 离线重建、快照哈希、产物一致 | `build.py --check` | ✅ |
| 域名层与所选 QX 模型一致 | `build.py --check`（67,181 个域名，只覆盖域名层） | ✅ |
| 模板可被 Mihomo 加载；规则、IP、GeoIP、MATCH 实际命中；策略组链路与末端节点；空地区组拒绝连接 | `build.py --verify`（本地规则文件 + 假节点，order / type 两种模型） | ✅ Mihomo v1.19.32 |
| QX 实际匹配模型 | 在 QX 中访问 `adservice.google.com` | ⏳ |
| 远程规则下载 | 推送后在 Clash Verge Rev 中加载模板 | ⏳ |
| 真实出口与订阅接入 | Clash Verge Rev 连接日志 | ⏳ |

`--verify` 会启动本机的 Mihomo 内核（默认查找 Clash Verge 自带的 `verge-mihomo.exe`），分别用「完整订阅」和「缺少美国节点」
两套假节点运行：经 mixed 端口发起连接，从内核日志读取命中的规则，再通过控制接口展开各策略组的实际链路。每个场景逐项断言：

- **每个策略组**的实际链路与按默认选择推算的链路完全一致，包括地区组的末端节点（完整订阅时必须落到对应地区节点，缺少节点时必须是 `REJECT`）；
- **每个用例**的规则标识（如 `RuleSet/google-youtube`、`GeoIP/cn`、`Match/`）、首个策略组和链路末端都与期望一致。

断言逻辑的反例（规则填错、OpenAI 走 `Japan → REJECT`、完整订阅下地区组全部 `REJECT` 等）写在 `tools/test_build.py` 里。
DNS 指向不可达地址，域名用例经 `hosts` 映射到本机，所以不会产生外网代理流量；但规则文件和节点都是本地替身，
**不能代替**远程下载和真实出口的验收。

## 维护

需要 Python 3.10+，仅依赖标准库。

```bash
# 离线重建（修改 sources/manifest.json 后执行）
python tools/build.py

# 校验：快照哈希、产物一致性、域名模型比对、域名验收用例
python tools/build.py --check

# 单元测试（断言逻辑反例、快照替换回滚、两种匹配模型）
python -m unittest discover -s tools -p "test_*.py"

# 运行时校验（需要能监听本机端口；可用 --mihomo 或环境变量 MIHOMO 指定内核；--model 临时切换匹配模型）
python tools/build.py --verify
python tools/build.py --verify --model type

# 更新上游：拉取上游仓库后，从指定提交导入（只读 Git 对象；新快照和新锁文件都就位后才删除备份，失败自动回滚）
git clone https://github.com/Steeefanie/QuantumultX-resources .cache/QuantumultX-resources
python tools/build.py --import-from .cache/QuantumultX-resources --ref <提交或标签>
python tools/build.py
```

## 许可与来源

- 构建工具与配置模板：[MIT](LICENSE)，署名为 ClashVerge-rules contributors。规则数据的版权归各自原作者，本仓库只负责转换和整理。
- 规则数据：转换自 [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)（MIT，
  许可原文见 `sources/qx/sources/v2fly/LICENSE`），经 QuantumultX-resources 整理；
  OneDrive 两份规则由上游仓库自行维护。每个规则文件头部都记录了来源路径、哈希和许可。
- 上游的 `filter/adblock/combined.list` 同时包含 MIT（app2smile）和 GPL-3.0（fmz200）内容，**本仓库暂不发布**，待单独评估。
  详见 [NOTICE](NOTICE)。
