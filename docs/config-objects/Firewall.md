# Firewall

## 用途

`Firewall` 由 `network.firewall` 使用，用来生成 DROS 管理的 nftables
入口文件和基础规则文件：

```text
/etc/nftables.conf
/etc/dros/nftables.d/10-firewall.nft
```

这是多例配置，但通常只需要一个 `Firewall/main`。多个 Firewall 对象会合并到同一个
ruleset 文件中；基础默认策略优先使用 `main`，否则使用按名称排序后的第一个对象。

应用 `Firewall` 后，DROS 会接管 `/etc/nftables.conf`，让它 `flush ruleset`
并 include `/etc/dros/nftables.d/*.nft`。`Firewall` 负责提供
`dros_filter`、`dros_route`、`dros_nat` 的 table/chain 结构；其他插件可以在
`/etc/dros/nftables.d` 中注入依赖这些 chain 的 snippet。

WireGuard `listenPort` 和 OpenVPN `listen` 的入口放行规则由
`network.interfaces` 写入 `30-interface-*.nft`，不是写入 `10-firewall.nft`。

## 内置配置

DROS 当前没有内置 `Firewall` YAML。没有 Firewall 对象时，`gw update firewall`
不会接管 `/etc/nftables.conf`，也不会生成基础 ruleset。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
---
apiVersion: dros/v1alpha1
kind: Firewall
metadata:
  name: main
spec:
  defaults:
    inputPolicy: drop
    forwardPolicy: drop
    outputPolicy: accept
  interfaceRules:
    - subject: devgroup/lan
      input:
        ping: true
        services:
          - tcp/22
          - udp/53
      forward:
        policy: accept
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。loader 现阶段会忽略该字段。

默认值：无。

### `kind`

必须为 `Firewall`。

默认值：无。

### `metadata.name`

Firewall 对象名称。推荐主配置使用 `main`。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.defaults`

基础链默认策略和自动规则开关。

默认值：

```yaml
inputPolicy: drop
forwardPolicy: drop
outputPolicy: accept
allowLoopback: true
allowEstablished: true
clampMss: true
allowEssentialIcmp: true
allowIcmpv6: null
```

字段：

- `inputPolicy`：`accept` 或 `drop`
- `forwardPolicy`：`accept` 或 `drop`
- `outputPolicy`：`accept` 或 `drop`
- `allowLoopback`：是否允许 loopback input
- `allowEstablished`：是否允许 established/related，并 drop invalid
- `clampMss`：是否在 forward_pre 中设置 TCP MSS clamp
- `allowEssentialIcmp`：是否允许必要 IPv4 ICMP
- `allowIcmpv6`：是否允许必要 ICMPv6；为 `null` 时跟随 `allowEssentialIcmp`

### `spec.interfaceRules`

按 interface 或 devgroup 生成 input/output/forward 策略。

默认值：`[]`。

每个 item 字段：

- `subject`：必填，接口名、`interface/<name>` 或 `devgroup/<name>`
- `input.ping`：是否允许 echo request
- `input.services`：服务列表，格式为 `tcp/<port>` 或 `udp/<port>`
- `input.dhcpv6Client`：是否允许 DHCPv6 client 流量
- `input.policy`：追加到 input_policy 的 raw policy
- `forward.allowTo`：允许转发到哪些 interface/devgroup
- `forward.denyPrivateNetworks`：拒绝转发到 IPv4 private networks
- `forward.policy`：追加到 forward_policy 的 raw policy
- `output.policy`：追加到 output_policy 的 raw policy
- `output.masquerade`：为该出口生成 IPv4 masquerade

### `spec.firewallRules`

追加 raw nft filter rule。

默认值：`[]`。

每个 item 字段：

- `chain`：`input`、`output` 或 `forward`
- `rule`：追加到 `<chain>_user` 的 raw nft rule

raw rule 中的 `iifgroup devgroup/<name>` 和 `oifgroup devgroup/<name>` 会解析为
`DevGroup.spec.id`。

### `spec.natRules`

生成 NAT 规则。

默认值：`[]`。

支持的 `type`：`portmap`、`ipmap`、`snat`、`masquerade`、`raw`。

常用字段：

- `family`：`ip`、`ip6` 或 `inet`，默认 `ip`
- `iif` / `oif`：接口名、`interface/<name>` 或 `devgroup/<name>`
- `saddr` / `daddr` / `daddrNot`：地址匹配
- `proto`：`tcp` 或 `udp`
- `dport`：端口、端口集合或端口列表
- `to`：DNAT/SNAT 目标
- `toPort`：DNAT 目标端口
- `rawRule`：raw NAT rule，设置后跳过结构化渲染
- `forwardAllowRule`：配合 `rawRule` 写入 `portmap_forward`

### `spec.markRules`

生成 nft mark 规则，供 policy routing 使用。

默认值：`[]`。

每个 item 字段：

- `fwMark`：引用 `FwMark`
- `mark` / `mask`：literal mark/mask；没有 `fwMark` 时使用
- `chains`：`prerouting` 或 `output`，默认 `["prerouting", "output"]`
- `rules`：raw nft mark rules
- `match.iif`：入接口匹配
- `match.iifGroup`：入 devgroup 匹配
- `match.tcpDports`：TCP 目标端口
- `match.udpDports`：UDP 目标端口
