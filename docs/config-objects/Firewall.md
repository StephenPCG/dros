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
          - gre
      forward:
        policy: accept
  natRules:
    - type: portmap
      daddr: 10.20.255.8
      proto: tcp
      dport: 6443
      to: 10.20.3.83
      toPort: 6443
      hairpin:
        sourceNet: 10.20.0.0/16
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

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
- `input.services`：服务列表。端口服务格式为 `tcp/<port>` 或 `udp/<port>`；
  也支持无端口的 IP protocol：`gre`、`esp`、`ah`
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
- `localOutput`：可选，默认 `false`。仅适用于 `portmap`、`ipmap` 和
  `raw`。设置为 `true` 后，DNAT 规则除了写入 `dnat_prerouting`，还会写入
  `dnat_output`，让网关自身发出的包也能命中该 DNAT 规则。该字段不能和
  `iif` 同用，因为本机发出的包没有入接口。
- `hairpin`：可选。为 `portmap` 或 `ipmap` 生成 hairpin SNAT 规则
- `rawRule`：raw NAT rule，设置后跳过结构化渲染
- `forwardAllowRule`：配合 `rawRule` 写入 `portmap_forward`

`portmap` 会生成 DNAT 规则、对应的 `portmap_forward` 放行规则，并在配置
`hairpin` 时生成额外的 postrouting SNAT 规则。`ipmap` 也支持同样的
`hairpin` 配置，但因为它是不按端口区分的 IP 映射，生成的 hairpin SNAT
规则只匹配源网段和 DNAT 后的目标地址，不匹配协议/端口。

默认情况下，`portmap`、`ipmap` 和 `raw` 只写入 `dnat_prerouting`，因此
只覆盖进入网关后的 DNAT 流量。网关本机进程主动发出的包会走 NAT
`output` hook，需要在对应规则上设置 `localOutput: true` 才会额外生成
`dnat_output` 规则。`snat` 和 `masquerade` 写入 `postrouting`，只要匹配条件
成立，本身可以覆盖本机发出的包。

`hairpin` 字段：

- `sourceNet`：必填。需要做 hairpin SNAT 的源网段，例如 `10.20.0.0/16`。
  DROS 不会从 DNAT 地址自动推导这个网段。
- `snat`：可选，默认 `preserve-low24`。支持 `preserve-low24` 和 `to-address`。
- `snatTo`：仅 `snat: to-address` 时必填，表示固定 SNAT 到哪个地址。

`preserve-low24` 只支持 IPv4，会生成类似下面的规则，把源 IP 第一段改成
`255`，保留后三段：

```nft
add rule inet dros_nat snat_postrouting ip saddr 10.20.0.0/16 ip daddr 10.20.3.83 tcp dport 6443 snat to ip saddr & 0.255.255.255 | 255.0.0.0
```

如果不配置 `hairpin`，DROS 不会生成对应 SNAT 规则。

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
- `match.icmp`：设为 `true` 时匹配 IPv4 ICMP；可与 `iif` / `iifGroup` 组合
