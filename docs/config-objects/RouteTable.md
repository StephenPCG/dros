# RouteTable

## 用途

`RouteTable` 由 `network.routing` 使用，用来管理一张 Linux route table。
执行 `gw update route-table/<name>` 时，DROS 会先 flush 该 table，再按
当前配置重新写入 routes。

实际应用时，DROS 会先生成 `{paths.run}/tmp/update-route.sh`，再执行该脚本。
脚本内每张 table 使用一个 `ip -batch` 批量写入，避免大量逐条
`ip route replace` 调用。

这是多例配置。DROS 还会把所有数字 table 写入
`/etc/iproute2/rt_tables.d/dros.conf`，便于系统命令按名称显示。

## 内置配置

DROS 当前没有内置 `RouteTable` YAML。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
apiVersion: dros/v1alpha1
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: default
      gateway: wan
    - to: 192.0.2.0/24
      type: unreachable
```

引用 IP list：

```yaml
apiVersion: dros/v1alpha1
kind: RouteTable
metadata:
  name: china
spec:
  family: ipv4
  table: 101
  routes:
    - to: china
      gateway: wan
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。

### `kind`

必须为 `RouteTable`。

默认值：无。

### `metadata.name`

RouteTable 名称。`RouteRuleSet.rules[].lookup` 可以引用该名称。

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.family`

路由族。可选值：`ipv4`、`ipv6`。

默认值：`ipv4`。

### `spec.table`

Linux route table ID 或 table name。

默认值：无。必须显式写出。

### `spec.id`

`spec.table` 的兼容别名。推荐新配置使用 `table`。

默认值：无。

### `spec.routes`

路由列表。

默认值：`[]`。

每个 route 字段：

- `to`：目标 CIDR、`default` 或 `default6`
- `to` 也可以是 IP list 引用，例如 `china`、`china@v4`、`china@v6`、`china@all`
- `gateway`：`Gateway` 名称，`type: route` 时必填
- `type`：`route`、`unreachable`、`blackhole`、`prohibit`，默认 `route`
- `metric`：可选，覆盖 gateway 上的 metric
- `raw`：可选，直接拼入 `ip route` 命令；设置后跳过结构化字段

更新前，DROS 会检查结构化 route 引用的 `Gateway` 当前是否可用。若 gateway
引用的接口不存在或未 UP，该 route 会被跳过；CLI 下会打印 warning，hook
触发的静默更新默认不打印。

## IP List

IP list 是普通文本文件，不是 ConfigObject。文件名约定：

- `<name>.v4.txt`
- `<name>.v6.txt`
- `<name>.txt`，mixed family

DROS 按以下顺序加载，先找到的同名同 family 文件优先：

1. `{paths.configs}/ip-lists`
2. `{paths.run}/ip-lists`
3. `{paths.source}/ip-lists`

`gw ip-list update` 会下载到 `{paths.run}/ip-lists`。源码中也内置一份
`{paths.source}/ip-lists`，用于首次部署时的默认可用列表。

如果 route 引用的 IP list 文件不存在、为空，或文件内有无法解析的 CIDR，该
route 会被跳过。CLI 下会打印 warning；hook 触发的静默更新默认不打印。
