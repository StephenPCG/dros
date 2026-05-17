# DnsmasqDNS

## 用途

`DnsmasqDNS` 由 `network.dnsmasq` 插件使用，用来管理 dnsmasq 的 DNS
服务配置，同时包含 gwtool 旧 `DnsmasqCore` 中的进程级选项。

这是单例式配置，推荐只写 `DnsmasqDNS/system`。DROS 会渲染到：

```text
/etc/dnsmasq.d/dros-10-dns.conf
```

如果配置了 `logFile`，DROS 还会生成 `/etc/logrotate.d/dros-dnsmasq`。

## 内置配置

DROS 没有内置 `DnsmasqDNS` YAML。没有该对象时，不会生成 DNS 片段。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DnsmasqDNS
metadata:
  name: system
spec:
  interfaces:
    - br-lan
  listenAddresses:
    - 10.0.0.1
    - 127.0.0.1
  noResolv: true
  bogusPriv: true
  domainNeeded: true
  servers:
    - 223.5.5.5
    - /corp.example.com/10.0.0.53
  locals:
    - lan
  addresses:
    - /gateway.lan/10.0.0.1
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。loader 现阶段会忽略该字段。

默认值：无。

### `kind`

必须为 `DnsmasqDNS`。

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

### 进程和监听选项

- `interfaces`：渲染为 `interface=...`，默认 `[]`
- `exceptInterfaces`：渲染为 `except-interface=...`，默认 `[]`
- `listenAddresses`：渲染为 `listen-address=...`，默认 `[]`
- `bindInterfaces`：渲染为 `bind-interfaces`，默认 `false`
- `noResolv`：渲染为 `no-resolv`，默认 `true`
- `noNegcache`：渲染为 `no-negcache`，默认 `true`
- `noHosts`：渲染为 `no-hosts`，默认 `true`
- `allServers`：渲染为 `all-servers`，默认 `false`
- `bogusPriv`：渲染为 `bogus-priv`，默认 `true`
- `domainNeeded`：渲染为 `domain-needed`，默认 `true`
- `expandHosts`：渲染为 `expand-hosts`，默认 `false`
- `localTtl`：渲染为 `local-ttl=...`，默认 `null`
- `cacheSize`：渲染为 `cache-size=...`，默认 `null`
- `port`：渲染为 `port=...`，默认 `null`

### 日志选项

- `logQueries`：渲染为 `log-queries`，默认 `false`
- `logAsync`：渲染为 `log-async=...`，默认 `null`
- `logFile`：渲染为 `log-facility=...`，默认 `null`；必须是绝对路径

### include 选项

- `confFiles`：渲染为 `conf-file=...`，默认 `[]`
- `confDirs`：渲染为 `conf-dir=...`，默认 `[]`
- `addnHosts`：渲染为 `addn-hosts=...`，默认 `[]`

### DNS 规则

- `servers`：渲染为 `server=...`，默认 `[]`
- `locals`：每项渲染为 `local=/domain/`，默认 `[]`
- `addresses`：渲染为 `address=...`，默认 `[]`
- `hostRecords`：渲染为 `host-record=...`，默认 `[]`
- `srvHosts`：渲染为 `srv-host=...`，默认 `[]`
- `cnameRecords`：渲染为 `cname=...`，默认 `[]`
- `rawOptions`：逐行原样写入，默认 `[]`
- `raw`：逐行原样写入，默认 `[]`
