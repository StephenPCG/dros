# IpListUpdater

## 用途

`IpListUpdater` 由 `ip_lists` 插件使用，用来管理自动下载 IP list 的 cron 任务。

这是单例式配置，推荐只写一个 `IpListUpdater/system`。它不定义 IP list 内容本身；
IP list 是普通文本文件，由 `gw ip-list update` 下载到 `paths.run/ip-lists`。

## 内置配置

DROS 没有内置 `IpListUpdater` YAML。没有该对象时，不会创建 cron 任务。

源码中会内置一份 IP list 文件，部署在：

```text
{paths.source}/ip-lists
```

因此首次部署后，即使没有联网下载，常用 list 也可以被 `RouteTable` 引用。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: IpListUpdater
metadata:
  name: system
spec:
  enabled: true
  cron: "0 1 *"
```

该配置会生成 `/etc/cron.d/dros-ip-list-updater`，每天 1 点执行：

```text
/usr/local/bin/gw ip-list update --verbose 1
```

禁用自动更新：

```yaml
apiVersion: dros/v1alpha1
kind: IpListUpdater
metadata:
  name: system
spec:
  enabled: false
```

设置为 `enabled: false` 后，DROS 会删除 `/etc/cron.d/dros-ip-list-updater`。

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。loader 现阶段会忽略该字段。

默认值：无。

### `kind`

必须为 `IpListUpdater`。

默认值：无。

### `metadata.name`

对象名称。推荐使用 `system`。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。注意这不会删除已有 cron 文件；如果需要删除，
应保留对象并设置 `spec.enabled: false` 后执行 `gw update ip-list-updater/system`。

默认值：`false`。

### `spec.enabled`

是否启用自动更新。

默认值：`true`。

设置为 `false` 时，删除 `/etc/cron.d/dros-ip-list-updater`。

### `spec.cron`

更新时间。

默认值：`0 1 *`。

DROS 支持两种写法：

- 三段写法：`minute hour day-of-week`，例如 `0 1 *` 会渲染为 `0 1 * * *`
- 五段标准 cron 写法：例如 `17 4 * * *`

## CLI

列出当前检测到的 IP list：

```sh
gw ip-list list
```

手动下载更新：

```sh
gw ip-list update
```

## 加载优先级

Route 解析 IP list 时按以下顺序加载，先找到的同名同 family 文件优先：

1. `{paths.configs}/ip-lists`
2. `{paths.run}/ip-lists`
3. `{paths.source}/ip-lists`

如果 `paths.configs` 是多个目录，DROS 按配置 overlay 的语义处理，后面的配置目录优先。
