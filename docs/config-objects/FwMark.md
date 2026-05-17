# FwMark

## 用途

`FwMark` 由 `network.routing` 和 `network.firewall` 使用，用来给一组
Linux fwmark `mark/mask` 取一个稳定名称。

这是多例配置。`RouteRuleSet.rules[].fwMark` 可以引用它，`Firewall.markRules[].fwMark`
也可以引用它。

## 内置配置

DROS 当前没有内置 `FwMark` YAML。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
  mask: "0x0000ff00"
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。

### `kind`

必须为 `FwMark`。

默认值：无。

### `metadata.name`

FwMark 名称，供 `RouteRuleSet` 和 `Firewall` 引用。

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。

默认值：`false`。

### `spec.mark`

32-bit 整数或十六进制字符串。

默认值：无。必须显式写出。

### `spec.mask`

32-bit 整数或十六进制字符串。

默认值：`0xffffffff`。

### `spec.value`

`spec.mark` 的兼容别名。推荐新配置使用 `mark`。

默认值：无。
