# DnsmasqDHCP

## 用途

`DnsmasqDHCP` 由 `network.dnsmasq` 插件使用，用来管理 dnsmasq DHCP
配置。DROS 会渲染到：

```text
/etc/dnsmasq.d/dros-20-dhcp.conf
```

这是单例式配置，推荐只写 `DnsmasqDHCP/system`。

## 内置配置

DROS 没有内置 `DnsmasqDHCP` YAML。没有该对象时，不会生成 DHCP 片段。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DnsmasqDHCP
metadata:
  name: system
spec:
  authoritative: true
  domain: lan
  dnsServers:
    - 10.0.0.1
  ranges:
    - tag: lan
      start: 10.0.0.100
      end: 10.0.0.200
      lease: 24h
  hosts:
    - 00:11:22:33:44:55,10.0.0.10,nas
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。

### `kind`

必须为 `DnsmasqDHCP`。

默认值：无。

### `metadata.name`

对象名称。推荐使用 `system`。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.enabled`

是否启用该配置片段。为 `false` 时写入 disabled marker。

默认值：`true`。

### DHCP 全局选项

- `authoritative`：渲染为 `dhcp-authoritative`，默认 `false`
- `domain`：渲染为 `domain=...`，默认 `null`
- `dnsServers`：渲染为 `dhcp-option=option:dns-server,...`，默认 `[]`
- `v6DnsServers`：渲染为 `dhcp-option=option6:dns-server,...`，默认 `[]`
- `options`：每项渲染为 `dhcp-option=...`，默认 `[]`
- `hosts`：每项渲染为 `dhcp-host=...`，默认 `[]`
- `rawOptions`：逐行原样写入，默认 `[]`
- `raw`：逐行原样写入，默认 `[]`

### `spec.ranges`

DHCP 地址池列表。默认值：`[]`。

每个 range 字段：

- `tag`：必填，dnsmasq DHCP tag
- `start`：必填，起始地址
- `end`：可选，结束地址
- `netmask`：可选，IPv4 netmask
- `broadcast`：可选，broadcast 地址；仅在设置 `netmask` 时写入
- `router`：可选，网关地址；未设置时，IPv4 range 会推断为同 `/24` 的 `.1`
- `mode`：可选，用于 `static`、`ra-stateless` 等 dnsmasq range mode
- `lease`：租约时间，默认 `24h`
