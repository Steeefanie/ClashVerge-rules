# 更新日志

## v0.1.0（未发布，验收中）

- 从 QuantumultX-resources@33b22e9 转换 27 份规则集：广告、AI、YouTube / Google、OneDrive Web / Sync、
  GitHub / Microsoft、流媒体与社交服务、国内服务、Apple、国内 / 海外合集。
- 匹配模型可选：`order`（默认）/ `type`。两种模型都有对应的预期结果和校验；type 模型会生成 68 条前置修正。
- 地区组设置 `empty-fallback: REJECT`，没有匹配节点时拒绝连接，避免静默直连。
- 快照改为从指定 Git 提交读取；新快照和新锁文件都就位后才删除备份，任一步失败自动回滚。
- 构建工具：离线重建、`--check`（域名模型比对）、`--verify`（启动 Mihomo 内核，逐项断言规则标识、首个策略组、
  链路末端和全部策略组链路，覆盖 IP / GeoIP / MATCH 与空地区组）、单元测试（断言反例、回滚）。
- 跳过 1 条不支持的条件（`spotify.list:58 user-agent`）。
- 暂不发布 `adblock/combined.list`（MIT 与 GPL-3.0 混合）。
