# Gateway

## 用途

`Gateway` 由 `network.routing` 使用，用来描述可复用的路由下一跳。
`RouteTable.routes[].gateway` 引用它后，DROS 会把它渲染到 `ip route`
命令中的 `via/dev/nexthop` 片段。

这是多例配置。常见名称包括 `wan`、`vpc`、`office`。

## 内置配置

DROS 当前没有内置 `Gateway` YAML。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
  via: 10.0.0.1
  onlink: true
  metric: 100
```

ECMP 示例：

```yaml
apiVersion: dros/v1alpha1
kind: Gateway
metadata:
  name: dual-wan
spec:
  nexthops:
    - dev: pppoe-a
      weight: 1
    - dev: pppoe-b
      weight: 1
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。loader 现阶段会忽略该字段。

默认值：无。

### `kind`

必须为 `Gateway`。

默认值：无。

### `metadata.name`

Gateway 名称，供 `RouteTable` 引用。

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.dev`

单下一跳模式的出口 interface。

默认值：无。`dev` 和 `nexthops` 必须二选一。

### `spec.via`

单下一跳模式的下一跳地址。

默认值：无。

### `spec.onlink`

是否在路由中追加 `onlink`。

默认值：`false`。

### `spec.metric`

默认 metric。`RouteTable.routes[].metric` 未设置时会继承该值。

默认值：无。

### `spec.nexthops`

ECMP 下一跳列表。

默认值：`[]`。`dev` 和 `nexthops` 必须二选一。

每个 nexthop 字段：

- `dev`：必填，出口 interface
- `via`：可选，下一跳地址
- `weight`：可选，必须为正整数
- `onlink`：可选，默认 `false`
