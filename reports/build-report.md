# 构建报告

> 由 tools/build.py 生成，请勿手改。上游：https://github.com/Steeefanie/QuantumultX-resources @ `33b22e9fc24f`（2026-11-06T11:25:59+08:00）

## 规则集

| 序 | 规则集 | 策略组 | host | host-suffix | keyword | IP | 去重后 domain |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `advertising` | 广告拦截 | 159 | 679 | 0 | 0 | 838 |
| 2 | `openai` | OpenAI | 6 | 14 | 0 | 0 | 20 |
| 3 | `anthropic` | Anthropic | 1 | 18 | 0 | 0 | 19 |
| 4 | `google-gemini` | Gemini | 30 | 33 | 0 | 0 | 63 |
| 5 | `google-youtube` | YouTube | 1 | 175 | 0 | 0 | 176 |
| 6 | `google-without-youtube` | Google | 246 | 642 | 0 | 0 | 663 |
| 7 | `microsoft-onedrive-web` | OneDrive-Web | 10 | 1 | 0 | 0 | 11 |
| 8 | `microsoft-onedrive-sync` | OneDrive-Sync | 12 | 13 | 0 | 0 | 24 |
| 9 | `microsoft-github` | GitHub | 32 | 33 | 0 | 0 | 59 |
| 10 | `microsoft` | Microsoft | 116 | 629 | 0 | 0 | 628 |
| 11 | `spotify` | Spotify | 14 | 24 | 1 | 2 | 35 |
| 12 | `bytedance-tiktok` | TikTok | 6 | 33 | 0 | 0 | 39 |
| 13 | `netflix` | Netflix | 0 | 24 | 0 | 0 | 24 |
| 14 | `disney` | Disney | 5 | 222 | 0 | 0 | 221 |
| 15 | `steam` | Steam | 19 | 42 | 0 | 0 | 60 |
| 16 | `telegram` | Telegram | 0 | 21 | 0 | 0 | 21 |
| 17 | `twitter` | X | 0 | 23 | 0 | 0 | 23 |
| 18 | `meta` | Meta | 2 | 549 | 0 | 0 | 550 |
| 19 | `wikimedia` | Wiki | 0 | 18 | 0 | 0 | 18 |
| 20 | `linkedin` | LinkedIn | 3 | 9 | 0 | 0 | 12 |
| 21 | `bilibili` | 国内服务 | 1 | 52 | 0 | 0 | 53 |
| 22 | `xiaohongshu` | 国内服务 | 0 | 12 | 0 | 0 | 12 |
| 23 | `deepseek` | 国内服务 | 0 | 2 | 0 | 0 | 2 |
| 24 | `tencent` | 国内服务 | 1 | 630 | 0 | 0 | 553 |
| 25 | `apple` | Apple | 251 | 1569 | 0 | 0 | 1583 |
| 26 | `domestic` | DIRECT | 51 | 7858 | 0 | 0 | 7827 |
| 27 | `global` | Global | 1313 | 24293 | 0 | 0 | 24308 |

## 未发布的规则集

- `adblock-rewrite`（`filter/adblock/combined.list`）：许可混合，待单独评估后发布；许可：MIT (app2smile/rules) + GPL-3.0 (fmz200/wool_scripts)

## 跳过的条件（Mihomo 无对应规则类型）

- `filter/services/spotify.list:58` `user-agent`：`user-agent, Spotify*, proxy`

## 匹配模型

当前模型：`order`。order = 按资源顺序先到先得；type = host > host-suffix > host-keyword 类型优先。两种模型都**尚未在 QX 中实测确认**，见 README「匹配模型」。

如果 QX 实际采用 type 模型，有 68 个 host 条目的结果会与当前模板不同，例如：

- `2mdn-cn.net`：order → `advertising`（广告拦截），type → `google-without-youtube`（Google）
- `2mdn.net`：order → `advertising`（广告拦截），type → `google-without-youtube`（Google）
- `admob-cn.com`：order → `advertising`（广告拦截），type → `google-without-youtube`（Google）
- `copilot-proxy.githubusercontent.com`：order → `local:DOMAIN-SUFFIX,githubusercontent.com`（Global），type → `microsoft-github`（GitHub）
- `copilot-telemetry-service.githubusercontent.com`：order → `local:DOMAIN-SUFFIX,githubusercontent.com`（Global），type → `microsoft-github`（GitHub）
- `copilot-telemetry.githubusercontent.com`：order → `local:DOMAIN-SUFFIX,githubusercontent.com`（Global），type → `microsoft-github`（GitHub）
- `roovza-launches.appsflyersdk.com`：order → `advertising`（广告拦截），type → `bytedance-tiktok`（TikTok）
- `roovza.inapps.appsflyersdk.com`：order → `advertising`（广告拦截），type → `bytedance-tiktok`（TikTok）
- `roovza.launches.appsflyersdk.com`：order → `advertising`（广告拦截），type → `bytedance-tiktok`（TikTok）
- `steamcloudsweden.blob.core.windows.net`：order → `microsoft`（Microsoft），type → `steam`（Steam）
- `steamugcquincy.blob.core.windows.net`：order → `microsoft`（Microsoft），type → `steam`（Steam）
- `ntp.tencent.com`：order → `tencent`（国内服务），type → `domestic`（DIRECT）
- `ntp1.tencent.com`：order → `tencent`（国内服务），type → `domestic`（DIRECT）
- `ntp2.tencent.com`：order → `tencent`（国内服务），type → `domestic`（DIRECT）
- `bybit-exchange.github.io`：order → `microsoft-github`（GitHub），type → `global`（Global）
- `client-teamviewer-com.trafficmanager.net`：order → `microsoft`（Microsoft），type → `global`（Global）
- `discord-attachments-uploads-prd.storage.googleapis.com`：order → `google-without-youtube`（Google），type → `global`（Global）

## 遮蔽情况（仅供审查）

后序规则集中被前序规则集覆盖、永远不会命中的条目。

| 被遮蔽规则集 | 先命中的规则集 | 条目数 | 示例 |
|---|---|---:|---|
| `google-without-youtube` | `advertising` | 23 | `2mdn-cn.net`, `2mdn.net`, `admob-cn.com`, `app-analytics-services.com` |
| `google-without-youtube` | `google-gemini` | 13 | `+.ai.studio`, `+.antigravity-unleash.goog`, `+.antigravity.google`, `+.deepmind.com` |
| `microsoft-github` | `local:DOMAIN-SUFFIX,githubusercontent.com` | 1 | `+.githubusercontent.com` |
| `microsoft-github` | `local:DOMAIN-SUFFIX,github.com` | 1 | `+.github.com` |
| `microsoft` | `local:DOMAIN-SUFFIX,githubusercontent.com` | 1 | `+.githubusercontent.com` |
| `microsoft` | `local:DOMAIN-SUFFIX,github.com` | 1 | `+.github.com` |
| `microsoft` | `microsoft-onedrive-sync` | 4 | `+.livefilestore.com`, `+.onedrive.com`, `+.sharepoint.com`, `+.sharepointonline.com` |
| `microsoft` | `microsoft-github` | 36 | `github-api.arkoselabs.com`, `github-cloud.s3.amazonaws.com`, `github-production-release-asset-2e65be.s3.amazonaws.com`, `github-production-repository-file-5c1aeb.s3.amazonaws.com` |
| `bytedance-tiktok` | `advertising` | 4 | `roovza-launches.appsflyersdk.com`, `roovza.inapps.appsflyersdk.com`, `roovza.launches.appsflyersdk.com`, `roovza.skadsdk.appsflyersdk.com` |
| `steam` | `microsoft` | 2 | `steamcloudsweden.blob.core.windows.net`, `steamugcquincy.blob.core.windows.net` |
| `linkedin` | `microsoft` | 1 | `+.l-0005.l-msedge.net` |
| `domestic` | `bilibili` | 49 | `+.acg.tv`, `+.acgvideo.com`, `+.animetamashi.cn`, `+.animetamashi.com` |
| `domestic` | `xiaohongshu` | 9 | `+.askdiandian.com`, `+.diandianlife.top`, `+.redelight.cn`, `+.rnote.com` |
| `domestic` | `deepseek` | 2 | `+.deepseek.com`, `+.deepseeksvc.com` |
| `domestic` | `tencent` | 545 | `+.17roco.com`, `+.97ting.com`, `+.acctdns.com`, `+.acctdns.net` |
| `global` | `local:DOMAIN-SUFFIX,githubusercontent.com` | 1 | `+.githubusercontent.com` |
| `global` | `local:DOMAIN-SUFFIX,github.com` | 1 | `+.github.com` |
| `global` | `advertising` | 30 | `2mdn-cn.net`, `2mdn.net`, `admob-cn.com`, `app-analytics-services.com` |
| `global` | `openai` | 13 | `+.chat.com`, `+.chatgpt.com`, `+.chatgpt.livekit.cloud`, `+.chatgpt.site` |
| `global` | `anthropic` | 18 | `+.ant.dev`, `+.anthropic.com`, `+.anthropic.team`, `+.antspace.dev` |
| `global` | `google-gemini` | 13 | `+.ai.studio`, `+.antigravity-unleash.goog`, `+.antigravity.google`, `+.deepmind.com` |
| `global` | `google-youtube` | 170 | `+.ggpht.cn`, `+.ggpht.com`, `+.googlevideo.com`, `+.withyoutube.com` |
| `global` | `google-without-youtube` | 626 | `apps5.oingo.com`, `avail.googleflights.net`, `clickserve.cc-dt.com`, `dartsearch-cn.net` |
| `global` | `microsoft-onedrive-sync` | 4 | `+.livefilestore.com`, `+.onedrive.com`, `+.sharepoint.com`, `+.sharepointonline.com` |
| `global` | `microsoft-github` | 30 | `github-api.arkoselabs.com`, `+.atom.io`, `+.dependabot.com`, `+.gh.io` |
| `global` | `microsoft` | 584 | `img-prod-cms-rt-microsoft-com.akamaized.net`, `img-s-msn-com.akamaized.net`, `mwf-service.akamaized.net`, `officecdn-microsoft-com.akamaized.net` |
| `global` | `spotify` | 33 | `audio-akp-bbr-spotify-com.akamaized.net`, `audio-akp-quic-spotify-com.akamaized.net`, `audio-akp-spotify-com.akamaized.net`, `audio4-ak-spotify-com.akamaized.net` |
| `global` | `bytedance-tiktok` | 31 | `p16-tiktok-dm-sticker-sign-sg.ibyteimg.com`, `p16-tiktokcdn-com.akamaized.net`, `+.bytedapm.com`, `+.muscdn.com` |
| `global` | `netflix` | 23 | `+.fast.com`, `+.netflix.ca`, `+.netflix.com`, `+.netflix.net` |
| `global` | `disney` | 217 | `+.112263.com`, `+.20thcenturystudios.com`, `+.20thcenturystudios.com.au`, `+.20thcenturystudios.com.br` |
| `global` | `steam` | 57 | `a4e8s8k3.map2.ssl.hwcdn.net`, `f3b7q2p3.ssl.hwcdn.net`, `lv.queniujq.cn`, `steambroadcast.akamaized.net` |
| `global` | `telegram` | 21 | `+.cdn-telegram.org`, `+.comments.app`, `+.contest.com`, `+.fragment.com` |
| `global` | `twitter` | 23 | `+.cms-twdigitalassets.com`, `+.periscope.tv`, `+.pscp.tv`, `+.t.co` |
| `global` | `meta` | 550 | `fbcdn-a.akamaihd.net`, `+.aboutfacebook.com`, `+.accessfacebookfromschool.com`, `+.accountkit.com` |
| `global` | `wikimedia` | 18 | `+.mediawiki.org`, `+.toolforge.org`, `+.w.wiki`, `+.wikibooks.org` |
| `global` | `linkedin` | 9 | `licdn.cn.cdn20.com`, `+.bizographics.com`, `+.l-0005.dc-msedge.net`, `+.licdn.cn` |
| `global` | `tencent` | 7 | `ntp.tencent.com`, `ntp1.tencent.com`, `ntp2.tencent.com`, `ntp3.tencent.com` |
| `global` | `apple` | 1534 | `p-events-delivery.akamaized.net`, `+.100beatscheap.com`, `+.12diasdepresentesdeitunes.com`, `+.12diasderegalosdeitunes.cl` |
| `global` | `domestic` | 70 | `ntp.aliyun.com`, `ntp1.aliyun.com`, `ntp2.aliyun.com`, `ntp3.aliyun.com` |

## 域名模型比对

只说明生成配置的**域名层**与本脚本采用的 QX 模型一致；不覆盖 IP / GeoIP / MATCH，也不能证明 QX 的实际行为。IP 层与策略组的运行时检查见 `python tools/build.py --verify`。

- 比对域名数：67181（全部 host、host-suffix 根域及一个虚拟子域）
- 结果不一致：0

## 域名验收用例（离线模拟）

| 域名 | 期望策略组 | 生成配置命中 | 结果 |
|---|---|---|---|
| `www.youtube.com` | YouTube | YouTube | ✅ |
| `rr1---sn-abc.googlevideo.com` | YouTube | YouTube | ✅ |
| `www.google.com` | Google | Google | ✅ |
| `mail.google.com` | Google | Google | ✅ |
| `gemini.google.com` | Gemini | Gemini | ✅ |
| `onedrive.com` | OneDrive-Web | OneDrive-Web | ✅ |
| `onedrive.live.com` | OneDrive-Web | OneDrive-Web | ✅ |
| `login.live.com` | OneDrive-Web | OneDrive-Web | ✅ |
| `api.onedrive.com` | OneDrive-Sync | OneDrive-Sync | ✅ |
| `contoso-my.sharepoint.com` | OneDrive-Sync | OneDrive-Sync | ✅ |
| `www.microsoft.com` | Microsoft | Microsoft | ✅ |
| `chatgpt.com` | OpenAI | OpenAI | ✅ |
| `api.openai.com` | OpenAI | OpenAI | ✅ |
| `claude.ai` | Anthropic | Anthropic | ✅ |
| `www.xiaohongshu.com` | 国内服务 | 国内服务 | ✅ |
| `www.bilibili.com` | 国内服务 | 国内服务 | ✅ |
| `www.baidu.com` | DIRECT | DIRECT | ✅ |
| `github.com` | Global | Global | ✅ |
| `copilot-proxy.githubusercontent.com` | Global | Global | ✅ |
| `adservice.google.com` | 广告拦截 | 广告拦截 | ✅ |
| `doubleclick.com` | 广告拦截 | 广告拦截 | ✅ |
| `www.wikipedia.org` | Wiki | Wiki | ✅ |
| `www.reddit.com` | Global | Global | ✅ |
