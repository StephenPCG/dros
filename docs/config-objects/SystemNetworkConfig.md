# SystemNetworkConfig

## 用途

`SystemNetworkConfig` 由 `network.core` 插件使用，主要配置系统级网络基础状态：

- 主机名和本地域名
- `/etc/hostname`
- `/etc/hosts`
- `/etc/sysctl.d/99-dros.conf` 中的网络相关 sysctl

这是单例配置。DROS 只期望当前配置目录 overlay 后存在一个有效的
`SystemNetworkConfig`。如果没有 `metadata.name: default` 的对象，但只有一个
`SystemNetworkConfig` 对象，DROS 会把它当作这个单例使用。

推荐命名为 `system`。

## 内置配置

DROS 没有内置的 `SystemNetworkConfig` YAML 文件。也就是说，用户配置目录里可以完全不写这个对象。

不过字段默认值在代码中定义。没有用户对象时，效果等价于：

```yaml
apiVersion: dros/v1alpha1
kind: SystemNetworkConfig
metadata:
  name: system
spec:
  hostname: gateway
  domain: lan
  nfConntrackMax: 524288
  disableCloudInitNetwork: true
  wideDhcpv6ClientService: false
```

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: SystemNetworkConfig
metadata:
  name: system
spec:
  hostname: gateway
  domain: test.init2.me
```

这个配置会让 `/etc/hosts` 中的本机 FQDN 为：

```text
gateway.test.init2.me gateway
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。示例中建议显式写出。

### `kind`

必须为 `SystemNetworkConfig`。

默认值：无。必须显式写出。

### `metadata.name`

对象名称。

这是单例配置，所以名称不参与实际网络语义。推荐使用 `system`。如果只有一个
`SystemNetworkConfig`，即使名称不是 `default`，DROS 也会使用它。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

是否禁用该对象。

设置为 `true` 后，这个对象会被忽略，效果等同于这个文件不存在。它不会自动清理已经写入系统的文件。

默认值：`false`。

### `spec.hostname`

系统主机名。

`gw bootstrap` 会：

- 写入 `/etc/hostname`
- 写入 `/etc/hosts`
- 在真实系统上运行 `hostname <hostname>` 修改 runtime hostname

默认值：`gateway`。

### `spec.domain`

本地域名，用于拼出 `/etc/hosts` 中的 FQDN。

例如 `hostname: gateway`、`domain: lan` 会生成 `gateway.lan gateway`。

默认值：`lan`。

### `spec.nfConntrackMax`

写入 `/etc/sysctl.d/99-dros.conf` 的 `net.netfilter.nf_conntrack_max`。

默认值：`524288`。

### `spec.disableCloudInitNetwork`

是否让 cloud-init 停止管理网络配置。

设置为 `true` 时，如果系统安装了 cloud-init，`gw bootstrap` 会写入：

- `/etc/cloud/cloud.cfg.d/99-dros-network.cfg`

内容等价于：

```yaml
network:
  config: disabled
disable_network_activation: true
```

同时会删除 cloud-init 常见的已渲染网络文件，避免它们继续被 ifupdown 或
netplan 读取：

- `/etc/network/interfaces.d/50-cloud-init`
- `/etc/network/interfaces.d/50-cloud-init.cfg`
- `/etc/netplan/50-cloud-init.yaml`

设置为 `false` 时，DROS 不写入这个 cloud-init network drop-in，也不会删除这些
cloud-init 已渲染网络文件。若此前由 DROS 写过
`/etc/cloud/cloud.cfg.d/99-dros-network.cfg`，再次 bootstrap 会删除它。

默认值：`true`。

### `spec.wideDhcpv6ClientService`

是否保留 Debian `wide-dhcpv6-client` 包自带的
`wide-dhcpv6-client.service`。

设置为 `false` 时，`gw bootstrap` 会执行：

```sh
systemctl disable --now wide-dhcpv6-client.service
```

DROS 的 IPv6PD 使用自己的 `dros-ipv6-pd.service` 管理 `dhcp6c`，不依赖这个包自带
service。没有 IPv6 需求的主机建议保持默认值。

设置为 `true` 时，DROS 不处理该 service，适合需要保留系统原有 DHCPv6 客户端行为的
特殊主机。

默认值：`false`。

当前 bootstrap 还会固定写入以下 sysctl：

- `net.ipv4.ip_forward = 1`
- `net.ipv6.conf.all.forwarding = 1`
- `net.core.default_qdisc = fq`
- `net.ipv4.tcp_congestion_control = bbr`
