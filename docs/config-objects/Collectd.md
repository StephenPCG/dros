# Collectd

## 用途

`Collectd` 由 `monitoring.collectd` 插件使用，用来管理网关本机的
collectd 配置和服务。

这是单例配置，名称固定为 `system`。`gw bootstrap` 会安装 collectd 相关包并写入
`/etc/collectd/collectd.conf`；后续修改配置后，可以再次执行 `gw bootstrap`，也可以执行
`gw update collectd` 更新配置并在有变更时重启 `collectd.service`。

## 内置配置

DROS 没有内置 YAML 文件，但代码中有默认配置。没有用户配置时，`gw bootstrap` 仍会按
`Collectd/system` 的默认值生成 collectd 配置。

默认等价于：

```yaml
apiVersion: dros/v1alpha1
kind: Collectd
metadata:
  name: system
spec:
  enabled: true
  interval: 10
  rrdDir: /var/lib/collectd/rrd
  unixSock: true
  unixSockPath: /run/collectd-unixsock
  plugins:
    interface:
      enabled: true
      ignore:
        - /^veth/
    ping:
      enabled: false
      hosts: []
    sensors:
      enabled: false
  raw: []
```

## 常见配置

开启 ping 监控：

```yaml
apiVersion: dros/v1alpha1
kind: Collectd
metadata:
  name: system
spec:
  plugins:
    ping:
      enabled: true
      hosts:
        - 1.1.1.1
        - 8.8.8.8
        - vps-hk
```

开启 sensors 并调整采集周期：

```yaml
apiVersion: dros/v1alpha1
kind: Collectd
metadata:
  name: system
spec:
  interval: 5
  plugins:
    sensors:
      enabled: true
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

### `kind`

必须为 `Collectd`。

### `metadata.name`

必须为 `system`。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。此时 `gw bootstrap` 会回到代码默认配置；
如果要停止 collectd，应设置 `spec.enabled: false` 并运行 `gw update collectd`。

### `spec.enabled`

是否启用 collectd。

默认值：`true`。

为 `false` 时，DROS 会写入禁用占位配置，并执行
`systemctl disable --now collectd.service`。

### `spec.interval`

采集周期，单位秒。

默认值：`10`。

### `spec.rrdDir`

RRD 数据目录。启用 collectd 时 DROS 会创建该目录。

默认值：`/var/lib/collectd/rrd`。

### `spec.unixSock`

是否启用 collectd `unixsock` 插件。

默认值：`true`。

### `spec.unixSockPath`

collectd unix socket 路径。

默认值：`/run/collectd-unixsock`。

### `spec.plugins.interface.enabled`

是否启用 `interface` 插件。

默认值：`true`。

### `spec.plugins.interface.ignore`

interface 插件忽略列表。collectd 支持接口名，也支持 `/.../` 格式的正则。
DROS 会生成 `IgnoreSelected true`，因此这里表示“不采集这些接口”。

默认值：`["/^veth/"]`。

### `spec.plugins.ping.enabled`

是否启用 `ping` 插件。

默认值：`false`。

### `spec.plugins.ping.hosts`

ping 目标列表。可以写 IP、域名或本机可解析的主机名。

默认值：`[]`。

### `spec.plugins.sensors.enabled`

是否启用 `sensors` 插件。

默认值：`false`。

### `spec.raw`

追加到 `collectd.conf` 末尾的原始单行配置列表，用于临时扩展。

默认值：`[]`。
