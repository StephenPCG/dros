# DnsmasqChinaNames

## 用途

`DnsmasqChinaNames` 由 `network.dnsmasq` 插件使用，用来管理
`felixonmars/dnsmasq-china-list` 的下载配置、运行时缓存、生成文件和定时任务。

这是单例式配置，推荐只写 `DnsmasqChinaNames/system`。DROS 会管理：

```text
/etc/dnsmasq.d/dros-30-china-names.conf
/etc/dnsmasq.d/dros-31-china-names-manual.conf
/etc/cron.d/dros-dnsmasq-china-names
```

`gw update dnsmasq-china-names/system` 不主动联网下载。它会根据配置立即生成
`dros-31-china-names-manual.conf`；同时从运行时缓存目录读取已经下载过的上游文件，生成
`dros-30-china-names.conf`。如果缓存目录中还没有对应文件，则 `dros-30-china-names.conf`
会是空的安全配置。

运行时缓存目录固定为 `{paths.run}/dnsmasq-china-names`。这是 DROS 内部实现细节，不通过
`DnsmasqChinaNames` 配置对象暴露。

`gw dnsmasq china-names update` 只负责下载上游数据到运行时缓存目录。下载完成后，它会立刻
用同一套生成逻辑刷新 `dros-30-china-names.conf` 和
`dros-31-china-names-manual.conf`。

## 内置配置

DROS 没有内置 `DnsmasqChinaNames` YAML。没有该对象时，不会创建占位文件或 cron。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DnsmasqChinaNames
metadata:
  name: system
spec:
  servers:
    - 114.114.114.114
    - 223.5.5.5
  files:
    - accelerated-domains.china.conf
    - bogus-nxdomain.china.conf
  manualNames:
    - internal.example.cn
  schedule: "27 4 * * *"
```

手动刷新：

```sh
gw dnsmasq china-names update
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。

### `kind`

必须为 `DnsmasqChinaNames`。

默认值：无。

### `metadata.name`

对象名称。推荐使用 `system`。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。注意这不会删除已有 cron；如果需要删除，
应保留对象并设置 `spec.enabled: false` 后执行 `gw update dnsmasq-china-names/system`。

默认值：`false`。

### `spec.enabled`

是否启用该配置。为 `false` 时写入 disabled marker，并删除
`/etc/cron.d/dros-dnsmasq-china-names`。

默认值：`true`。

### `spec.servers`

用于替换上游 `server=/domain/DNS` 中 DNS 服务器的列表。

默认值：`[]`。启用对象时不能为空。

### `spec.files`

要下载的上游文件。

默认值：`[]`，表示下载所有支持的文件。

当前支持：

- `accelerated-domains.china.conf`
- `apple.china.conf`
- `bogus-nxdomain.china.conf`
- `google.china.conf`

### `spec.manualNames`

手工追加的域名列表。每个域名会为每个 `servers` 生成一行
`server=/domain/server`。

默认值：`[]`。

### `spec.manualNameFiles`

额外手工域名文件。相对路径按配置文件所在目录解析。该字段由 `gw update dnsmasq` 直接生成，
不需要先执行 `gw dnsmasq china-names update`。

默认值：`[]`。

### `spec.cronEnabled`

是否创建 cron 任务。

默认值：`true`。

### `spec.schedule`

cron 时间。默认值：`27 4 * * *`。

DROS 支持两种写法：

- 三段写法：`minute hour day-of-week`
- 五段标准 cron 写法

### `spec.command`

cron 中执行的命令。

默认值：`/usr/local/bin/gw dnsmasq china-names update --verbose 1`。
