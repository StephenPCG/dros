# IPv6PD

## 用途

`IPv6PD` 由 `network.ipv6pd` 插件使用，用来管理 DHCPv6 Prefix Delegation 和
下游 RA：

- 使用 `wide-dhcpv6-client` 的 `dhcp6c` 向上游请求 delegated prefix
- 使用 `radvd` 向下游接口广播 delegated `/64` 和可选 ULA `/64`
- 写入 PPP / ifupdown hook，让接口上线时通过 `gw hook ipv6-refresh` 触发
  daemon 刷新
- 写入 `/etc/dros/nftables.d/15-ipv6pd.nft`，给 DHCPv6、RA、NDP 放行所需的
  nftables snippet

这是单例配置，名称固定为 `system`。

## 内置配置

DROS 没有内置 `IPv6PD` YAML。没有该对象时，`gw update ipv6pd` 不会应用任何
IPv6PD 配置。

`gw bootstrap` 已经通过 `network.core` 安装 `wide-dhcpv6-client` 和 `radvd`
相关包；是否启用 IPv6PD 由这个对象决定。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: IPv6PD
metadata:
  name: system
spec:
  enabled: true
  uplink: pppoe-telecom
  prefixLengthHint: 60
  delegatedPrefixLength: 60
  requestAddress: false
  acceptRA: 2
  dnsServers:
    - fd02::1
  searchDomains:
    - lan
  downstream:
    - iface: br-lan
      subnetId: 2
      ulaPrefix: fd02::/64
      advertise: true
```

只广播 ULA，不从 delegated prefix 给该接口分配公网 `/64`：

```yaml
apiVersion: dros/v1alpha1
kind: IPv6PD
metadata:
  name: system
spec:
  uplink: pppoe-telecom
  downstream:
    - iface: br-lan
      subnetId: 0
      delegated: false
      ulaPrefix: fd02::/64
```

## 生成文件

- `/etc/dros/ipv6/dhcp6c.conf`
- `/etc/dros/ipv6/dhcp6c-script`
- `/var/lib/dhcpv6/dhcp6c_duid`，仅配置 `spec.duid` 时写入
- `/etc/radvd.conf`
- `/etc/systemd/system/dros-ipv6-pd.service`
- `/etc/network/if-up.d/dros-ipv6`
- `/etc/ppp/ip-pre-up.d/dros-ipv6`
- `/etc/ppp/ip-up.d/dros-ipv6`
- `/etc/ppp/ipv6-up.d/dros-ipv6`
- `/etc/dros/nftables.d/15-ipv6pd.nft`

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。

### `kind`

必须为 `IPv6PD`。

默认值：无。

### `metadata.name`

固定为 `system`。

默认值：如果省略，loader 视为 `default`，但 `network.ipv6pd` 会拒绝非 `system`
名称。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。注意这不会停止已经运行的服务；要停止服务，应保留对象
并设置 `spec.enabled: false` 后执行 `gw update ipv6pd/system`。

默认值：`false`。

### `spec.enabled`

是否启用 IPv6PD。

默认值：`true`。

设置为 `false` 时，DROS 会写入 disabled placeholder，并执行：

```sh
systemctl disable --now dros-ipv6-pd.service
systemctl disable --now radvd.service
```

### `spec.client`

DHCPv6-PD 客户端类型。

默认值：`wide-dhcpv6`。当前只支持这个值。

### `spec.duid`

可选 DUID 十六进制字节串，会写入 `/var/lib/dhcpv6/dhcp6c_duid`。

默认值：`null`。不配置时由系统现有 DUID 行为决定。

字节串长度必须为 2 到 130 字节，可用冒号、短横线或空格分隔。

### `spec.uplink`

请求 PD 的上游 interface。

默认值：`null`。启用时必填。

### `spec.iaid`

写入 dhcp6c `ia-na` / `ia-pd` 的 IAID。

默认值：`1`。必须大于等于 `0`。

### `spec.prefixLengthHint`

请求前缀长度 hint。

默认值：`60`。允许 `1` 到 `64`，或 `null`。

兼容字段名：`prefix_length_hint`。

### `spec.delegatedPrefixLength`

实际按哪个 delegated prefix 长度计算下游 `sla-len` 和 `subnetId` 范围。

默认值：`null`。未设置时使用 `prefixLengthHint`；如果两者都是 `null`，按 `60`。

兼容字段名：`delegated_prefix_length`。

### `spec.requestAddress`

是否额外请求 IA_NA 地址。

默认值：`false`。

兼容字段名：`request_address`。

### `spec.acceptRA`

上游 interface 的 `/proc/sys/net/ipv6/conf/<iface>/accept_ra` 值。

默认值：`2`。允许 `0`、`1`、`2` 或 `null`。为 `null` 时不主动写该 sysctl。

兼容字段名：`accept_ra`。

### `spec.dnsServers`

默认 RDNSS IPv6 地址列表。下游未配置 `rdnss` 时使用这里的值。

默认值：`[]`。

兼容字段名：`dns_servers`。

### `spec.searchDomains`

默认 DNSSL 域名列表。下游未配置 `dnssl` 时使用这里的值。

默认值：`[]`。

兼容字段名：`search_domains`。

### `spec.downstream`

下游 RA 接口列表。

默认值：`[]`。启用时必须至少有一项。

## `spec.downstream[]`

### `iface`

下游 interface 名称。

默认值：无，必填。

### `subnetId`

从 delegated prefix 中选择哪个 `/64`。

默认值：无，必填。

如果 `delegatedPrefixLength: 60`，可用范围是 `0` 到 `15`。

兼容字段名：`subnet_id`。

### `prefixLength`

下游 prefix 长度。

默认值：`64`。当前实现固定要求为 `64`。

兼容字段名：`prefix_length`。

### `address`

下游接口从 delegated prefix 中分配的 interface id。

默认值：`::1`。当前会渲染为 dhcp6c `ifid 1`。

### `delegated`

是否从上游 delegated prefix 给该下游接口分配公网 `/64`。

默认值：`true`。

### `ulaPrefix`

额外广播的 ULA `/64`。

默认值：`null`。配置时必须是 IPv6 `/64`。

兼容字段名：`ula_prefix`。

### `advertise`

是否为该接口生成 radvd RA 配置，以及对应的 nft RA 输出规则。

默认值：`true`。

### `rdnss`

该下游接口的 RDNSS IPv6 地址列表。非空时覆盖 `spec.dnsServers`。

默认值：`[]`。

### `dnssl`

该下游接口的 DNSSL 域名列表。非空时覆盖 `spec.searchDomains`。

默认值：`[]`。

## 行为说明

- `gw update ipv6pd` 会写入 dhcp6c、radvd、systemd、hook、nft snippet，并在有变更时
  重启 `dros-ipv6-pd.service` 和 `radvd.service`。
- hook 只调用 `gw hook ipv6-refresh ...`，不在 ifupdown / ppp hook 中直接跑
  `gw update`。
- nft snippet 写入 `/etc/dros/nftables.d` 是安全的；只有 `Firewall` 对象应用后，
  `/etc/nftables.conf` 才会 include 这个目录。
- 如果下游接口配置了 `ulaPrefix`，并且存在同名 `Interface` ConfigObject，但该
  Interface 没有这个 prefix 内的地址，CLI 会打印 warning。网关自身的 ULA 地址仍建议写在
  `Interface.extraAddresses` 中。

## CLI

```sh
gw update ipv6pd
gw update ipv6pd/system
gw update ipv6
gw config create ipv6
```
