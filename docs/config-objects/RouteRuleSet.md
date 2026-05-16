# RouteRuleSet

## 用途

`RouteRuleSet` 由 `network.routing` 使用，用来管理一段 policy routing
rule priority range。执行 update 时，DROS 会先删除该 range 内已有 rules，
再按配置重新添加。

这是多例配置。常见做法是按 family 或策略域拆分，例如 `ipv4-policy`、
`ipv6-policy`。

## 内置配置

DROS 当前没有内置 `RouteRuleSet` YAML。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
  mask: "0x0000ff00"
---
apiVersion: dros/v1alpha1
kind: RouteTable
metadata:
  name: lab
spec:
  table: 100
  routes: []
---
apiVersion: dros/v1alpha1
kind: RouteRuleSet
metadata:
  name: ipv4-policy
spec:
  family: ipv4
  managedPriority:
    start: 10000
    end: 10999
  rules:
    - priority: 10010
      fwMark: lab
      lookup: lab
    - priority: 10020
      from: 10.8.0.0/16
      lookup: 100
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。loader 现阶段会忽略该字段。

默认值：无。

### `kind`

必须为 `RouteRuleSet`。

默认值：无。

### `metadata.name`

RouteRuleSet 名称。

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.family`

规则族。可选值：`ipv4`、`ipv6`。

默认值：`ipv4`。

### `spec.managedPriority`

DROS 管理的 priority range。

默认值：无。必须显式写出。

字段：

- `start`：必填整数
- `end`：必填整数，必须大于等于 `start`

### `spec.rules`

规则列表。

默认值：`[]`。

每个 rule 字段：

- `priority`：必填，必须落在 `managedPriority` 范围内，且不能重复
- `lookup`：table ID、table name，或 `RouteTable` 名称；非 raw rule 必填
- `fwMark`：引用 `FwMark`
- `fwmark`：literal fwmark；如果同名 `FwMark` 存在，也会解析为对象引用
- `from`：可选源网段
- `to`：可选目标网段
- `iif`：可选入接口
- `oif`：可选出接口
- `uidrange`：可选 uid range
- `suppressPrefixlength`：可选 suppress prefix length
- `raw`：可选，直接拼入 `ip rule add priority <priority> ...`
