# DevGroup

## 用途

`DevGroup` 由 `network.interfaces` 插件使用，用来把一个稳定名称映射到 Linux interface group id。

它本身不写入系统文件。`Interface.spec.devGroup` 或 `Interface.spec.devgroup` 引用它时，
DROS 会在对应 ifupdown fragment 中追加：

```text
post-up ip link set dev <interface> group <id>
```

这是多例配置。一个系统中通常会有多个 DevGroup，例如 `wan`、`lan`、`guest`。

## 内置配置

DROS 当前没有内置 `DevGroup` YAML，也不会自动创建 `wan` 或 `lan`。

如果 Interface 引用了某个 DevGroup，但配置目录中没有定义对应对象，`gw update` 会报错，并且不会写入该 Interface 的系统文件。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。示例中建议显式写出。

### `kind`

必须为 `DevGroup`。

默认值：无。必须显式写出。

### `metadata.name`

DevGroup 名称，供其他 ConfigObject 引用。

例如 Interface 可以写：

```yaml
spec:
  devGroup: lan
```

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

是否禁用该对象。

设置为 `true` 后，这个对象会被忽略，效果等同于文件不存在。引用它的 Interface 会因为找不到 DevGroup 而无法更新。

默认值：`false`。

### `spec.id`

Linux interface group id。

`Interface` 引用该 DevGroup 时，会把这个 id 渲染到 `ip link set dev ... group <id>`。

默认值：无。必须显式写出。
