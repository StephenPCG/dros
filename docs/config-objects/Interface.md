# Interface

## 用途

`Interface` 由 `network.interfaces` 插件使用，用来生成 ifupdown 配置片段：

```text
/etc/network/interfaces.d/dros-<name>.cfg
```

这是多例配置。每个 Interface 对象对应一个 Linux interface name，名称来自 `metadata.name`。

当前支持三个 interface type：

- `eth`
- `bridge`
- `vlan`

所有 Interface 都可以引用 `DevGroup`。如果引用了未定义的 DevGroup，`gw update` 会报错，并且不会写入对应 Interface 文件。

## 内置配置

DROS 当前没有内置 `Interface` YAML。

没有 Interface 配置时，`gw update ifaces` 不会写任何 interface 文件。

## 常见配置

### Ethernet DHCP

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
  dhcp: true
  devGroup: wan
```

### Bridge

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  address: 10.0.0.1/24
  ports:
    - eth1
    - eth2
  vlanAware: true
  devGroup: lan
```

### VLAN

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br0.10
spec:
  type: vlan
  parent: br0
  id: 10
  address: 10.10.0.1/24
  devGroup: lan
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

现阶段 loader 会忽略这个字段，但保留它可以让文件形态稳定，后续做版本校验和迁移时更清晰。

默认值：无。示例中建议显式写出。

### `kind`

必须为 `Interface`。

默认值：无。必须显式写出。

### `metadata.name`

Interface 名称，也是最终 Linux interface name。

例如 `metadata.name: br0` 会生成 `/etc/network/interfaces.d/dros-br0.cfg`，并渲染：

```text
auto br0
iface br0 ...
```

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

是否禁用该对象。

设置为 `true` 后，这个对象会被忽略，效果等同于文件不存在。当前 `gw update` 不会因为 disabled 自动删除已经写入系统的旧文件，后续由 `gw remove` 处理清理。

默认值：`false`。

### `spec.type`

Interface 类型。

当前可选值：

- `eth`
- `bridge`
- `vlan`

默认值：无。必须显式写出。

### `spec.dhcp`

是否使用 DHCP。

为 `true` 时渲染：

```text
iface <name> inet dhcp
```

为 `false` 且配置了 `address` 时渲染 `inet static`。为 `false` 且没有配置 `address` 时渲染 `inet manual`。

默认值：`false`。

### `spec.address`

主地址，直接映射到 ifupdown 的 `address` 字段。

示例：

```yaml
address: 10.0.0.1/24
```

默认值：无。

### `spec.gateway`

默认网关，直接映射到 ifupdown 的 `gateway` 字段。

示例：

```yaml
gateway: 10.0.0.254
```

默认值：无。

### `spec.extraAddresses` / `spec.extra_addresses`

额外地址列表。两个字段名都支持。

每个地址会渲染为：

```text
up ip addr add <address> dev $IFACE
down ip addr del <address> dev $IFACE || true
```

支持 IPv4 和 IPv6 地址。

默认值：`[]`。

### `spec.devGroup` / `spec.devgroup`

引用的 DevGroup 名称。两个字段名都支持。

如果设置了该字段，配置目录中必须存在同名 `DevGroup`，否则该 Interface 无法更新。

默认值：无。

### `spec.ports`

仅 `type: bridge` 使用。bridge 的成员接口列表。

示例：

```yaml
ports:
  - eth1
  - eth2
```

为空时渲染 `bridge_ports none`。

默认值：`[]`。

### `spec.vlanAware` / `spec.vlan_aware`

仅 `type: bridge` 使用。两个字段名都支持。

为 `true` 时渲染：

```text
bridge_vlan_aware yes
```

默认值：`false`。

### `spec.parent`

仅 `type: vlan` 使用。VLAN parent interface。

示例：

```yaml
parent: br0
```

默认值：无。`type: vlan` 时必须显式写出。

该 parent 必须有对应的 `Interface` ConfigObject。DROS 在写入任何被更新的
Interface 文件之前会先检查这个依赖。

### `spec.id`

仅 `type: vlan` 使用。VLAN ID。

示例：

```yaml
id: 10
```

取值范围：`1` 到 `4094`。

默认值：无。`type: vlan` 时必须显式写出。
