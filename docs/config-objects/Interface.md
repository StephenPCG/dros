# Interface

## 用途

`Interface` 由 `network.interfaces` 插件使用，用来管理网关上的 Linux interface。

这是多例配置。每个 `Interface` 对象对应一个 interface name，名称来自
`metadata.name`。

当前支持这些 `spec.type`：

- `eth`
- `bridge`
- `vlan`
- `loopback`
- `docker`
- `gre`
- `pppoe`
- `wireguard`
- `openvpn`

大多数类型会生成 ifupdown 配置：

```text
/etc/network/interfaces.d/dros-<name>.cfg
```

`pppoe` 还会生成 `/etc/ppp/peers/<name>`。`wireguard` 还会生成
`/etc/wireguard/<name>.conf`。`openvpn` 还会生成
`/etc/dros/openvpn/<name>.ovpn`，以及必要时生成 `/etc/dros/openvpn/<name>.up`。

`docker` 不生成 ifupdown 配置。它由 Docker 管理生命周期，DROS 只负责创建自定义 Docker
bridge network，并在运行时应用 `devGroup` 和 `extraAddresses`。

所有 Interface 都可以引用 `DevGroup`。如果引用了未定义的 DevGroup，`gw update` 会报错，
并且不会写入任何被选中的对象。

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

### Loopback

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: lo
spec:
  type: loopback
  extraAddresses:
    - 10.255.0.1/32
    - fd00::1/128
```

### Docker Bridge

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br-app
spec:
  type: docker
  subnet: 172.30.0.0/24
  devGroup: container
```

`metadata.name: docker0` 表示 Docker 默认 bridge。DROS 不会对它执行
`docker network create`，只会尝试设置运行时属性。

### GRE

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: gre-office
spec:
  type: gre
  localVip: 10.91.0.1
  remoteVip: 10.91.0.2
  localPublicIp: 203.0.113.10
  remotePublicIp: 203.0.113.20
  ttl: 255
  devGroup: wan
```

### PPPoE

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: pppoe-wan
spec:
  type: pppoe
  device: eth0.35
  user: account@example
  password: pppoe-password
  usePeerDNS: false
  devGroup: wan
```

### WireGuard

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  address: 10.20.0.1/24
  privateKey: example-private-key
  listenPort: 51820
  peers:
    - publicKey: example-peer-public-key
      allowedIPs:
        - 10.20.0.2/32
      endpoint: peer.example.net:51820
      persistentKeepalive: 25
  devGroup: vpn
```

### OpenVPN

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: ovpn-lab
spec:
  type: openvpn
  configFile: /opt/gateway/ovpn/lab/client.conf
  crlFile: /opt/gateway/ovpn/lab/pki/crl.pem
  devGroup: vpn
```

也可以直接写 inline 配置：

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: ovpn-inline
spec:
  type: openvpn
  config: |
    client
    dev-type tun
    proto udp
    remote vpn.example.net 1194
  up: echo openvpn-up
  devGroup: vpn
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

例如 `metadata.name: br0` 会生成 `/etc/network/interfaces.d/dros-br0.cfg`。

默认值：如果省略，loader 视为 `default`。实际使用中不建议省略。

### `metadata.disabled`

是否禁用该对象。

设置为 `true` 后，这个对象会被忽略，效果等同于文件不存在。当前 `gw update` 不会因为
disabled 自动删除已经写入系统的旧文件，后续由 `gw remove` 处理清理。

默认值：`false`。

### `spec.type`

Interface 类型。

可选值：`eth`、`bridge`、`vlan`、`loopback`、`docker`、`gre`、`pppoe`、`wireguard`、`openvpn`。

默认值：无。必须显式写出。

### `spec.dhcp`

是否使用 DHCP。`eth`、`bridge`、`vlan`、`wireguard` 等使用普通地址族渲染的类型支持该字段。

为 `true` 时渲染：

```text
iface <name> inet dhcp
```

为 `false` 且配置了 `address` 时渲染 `inet static`。为 `false` 且没有配置 `address` 时渲染
`inet manual`。

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

对于 ifupdown 类型，每个地址会渲染为：

```text
up ip addr add <address> dev $IFACE
down ip addr del <address> dev $IFACE || true
```

对于 `docker` 和 PPP hook 运行时处理，DROS 使用 `ip addr replace <address> dev <name>`。

支持 IPv4 和 IPv6 地址。

默认值：`[]`。

### `spec.devGroup` / `spec.devgroup`

引用的 DevGroup 名称。两个字段名都支持。

如果设置了该字段，配置目录中必须存在同名 `DevGroup`，否则本轮 `gw update` 会在应用前失败。

默认值：无。

### `spec.ports`

仅 `type: bridge` 使用。bridge 的成员接口列表。

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

默认值：无。`type: vlan` 时必须显式写出。

该 parent 必须有对应的 `Interface` ConfigObject。DROS 在写入任何被更新的 Interface 文件之前会先检查这个依赖。

### `spec.id`

仅 `type: vlan` 使用。VLAN ID。

取值范围：`1` 到 `4094`。

默认值：无。`type: vlan` 时必须显式写出。

### `spec.subnet`

仅 `type: docker` 使用。Docker bridge network 的 IPv4/IPv6 CIDR。

`metadata.name` 不是 `docker0` 时，如果 Docker network 不存在，DROS 会执行：

```text
docker network create --driver bridge --subnet <subnet> --opt com.docker.network.bridge.name=<name> <name>
```

如果 network 已存在但 subnet 或 bridge name 不一致，DROS 会删除并重建该 Docker network。

默认值：无。不设置时仍会创建 Docker bridge network，但不指定 `--subnet`。

### `spec.localVip`

仅 `type: gre` 使用。GRE tunnel 的本端内层地址。

不带前缀时自动按 `/32` 渲染。

默认值：无。`gre` 至少需要 `address` 或 `localVip` 之一。

### `spec.remoteVip`

仅 `type: gre` 使用。GRE tunnel 的对端内层地址，渲染为 ifupdown `pointopoint`。

默认值：无。

### `spec.localPublicIp`

仅 `type: gre` 使用。GRE outer source address。

默认值：无。`type: gre` 时必须显式写出。

### `spec.remotePublicIp`

仅 `type: gre` 使用。GRE outer destination address。

默认值：无。`type: gre` 时必须显式写出。

### `spec.xfrmTransport`

仅 `type: gre` 使用。预留给后续 XFRM transport 对象。

当前会在 GRE ifupdown fragment 中渲染 `gw start xfrm/<name> --verbose 0` 和对应的
`gw stop`，但 XFRM ConfigObject 本身还没有实现。

默认值：无。

### `spec.ttl`

仅 `type: gre` 使用。GRE tunnel TTL。

取值范围：`1` 到 `255`。

默认值：`255`。

### `spec.device`

仅 `type: pppoe` 使用。底层以太网或 VLAN interface，例如 `eth0`、`eth0.35`。

默认值：无。`type: pppoe` 时必须显式写出。

### `spec.user`

仅 `type: pppoe` 使用。PPPoE 账号。

默认值：无。`type: pppoe` 时必须显式写出。

### `spec.password`

仅 `type: pppoe` 使用。PPPoE 密码，会写入 `/etc/ppp/peers/<name>`，文件权限为 `0600`。

默认值：无。`type: pppoe` 时必须显式写出。

### PPPoE 选项

以下字段仅 `type: pppoe` 使用：

- `hidePassword`，默认 `true`，写入 `hide-password`
- `noauth`，默认 `true`，写入 `noauth`
- `maxfail`，默认 `0`
- `persist`，默认 `true`，写入 `persist`
- `debug`，默认 `true`，写入 `debug`
- `holdoff`，默认 `5`
- `manageDevice`，默认 `true`，ifup 前检查并拉起 `device`
- `noipdefault`，默认 `true`，写入 `noipdefault`
- `defaultroute`，默认 `true`，写入 `defaultroute`
- `replacedefaultroute`，默认 `false`，写入 `replacedefaultroute`
- `noproxyarp`，默认 `true`，写入 `noproxyarp`
- `ipv6`，默认 `true`，写入 `+ipv6`
- `ipv6cpUseIpaddr`，默认 `true`，写入 `ipv6cp-use-ipaddr`
- `usePeerDNS`，默认 `false`，为 `true` 时写入 `usepeerdns`

### `spec.privateKey`

仅 `type: wireguard` 使用。WireGuard 私钥，写入 `/etc/wireguard/<name>.conf`。

默认值：无。当前 `wireguard` 需要 `privateKey` 或 `privateKeyFile` 之一；实际渲染优先使用
`privateKey`。

### `spec.privateKeyFile`

仅 `type: wireguard` 使用。从目标机器上的文件读取私钥。

设置后，ifupdown fragment 会在 `wg setconf` 前执行：

```text
wg set $IFACE private-key <privateKeyFile>
```

如果同时设置了 `privateKey` 和 `privateKeyFile`，当前渲染优先使用 `privateKey`。

默认值：无。

### `spec.listenPort`

仅 `type: wireguard` 使用。WireGuard UDP 监听端口，写入 `ListenPort`。

取值范围：`1` 到 `65535`。

默认值：无。

### `spec.peers`

仅 `type: wireguard` 使用。WireGuard peer 列表。

每个 peer 支持：

- `publicKey`：必填，peer 公钥
- `allowedIPs`：可选，CIDR 列表，写入 `AllowedIPs`
- `endpoint`：可选，写入 `Endpoint`
- `persistentKeepalive`：可选，写入 `PersistentKeepalive`

默认值：`[]`。

### `spec.config`

仅 `type: openvpn` 使用。Inline OpenVPN 配置内容，会原样写入：

```text
/etc/dros/openvpn/<name>.ovpn
```

`config` 和 `configFile` 必须二选一，不能同时设置。

默认值：无。

### `spec.configFile`

仅 `type: openvpn` 使用。从外部文件读取 OpenVPN 配置，再写入
`/etc/dros/openvpn/<name>.ovpn`。

如果是绝对路径，DROS 按目标系统路径读取；如果是相对路径，则相对于当前 ConfigObject
YAML 文件所在目录读取。

`config` 和 `configFile` 必须二选一，不能同时设置。

默认值：无。

### `spec.crlFile`

仅 `type: openvpn` 使用。可选 CRL 文件路径。

设置后 ifupdown fragment 会把该路径传给 `/usr/lib/dros/openvpn-iface`，helper 在启动
OpenVPN 前检查该文件可读，并追加 `--crl-verify <crlFile>`。

默认值：无。

### `spec.up`

仅 `type: openvpn` 使用。可选单行 shell 命令。

如果设置了 `devGroup` 或 `up`，DROS 会生成 `/etc/dros/openvpn/<name>.up`。该脚本会在
OpenVPN 设备出现后先设置 `devGroup`，然后执行 `up` 命令。

默认值：无。

### OpenVPN 运行方式

`openvpn` interface 由 ifupdown 调 `/usr/lib/dros/openvpn-iface` 管理：

```text
pre-up /usr/lib/dros/openvpn-iface start <name> /etc/dros/openvpn/<name>.ovpn <pid> <up-script> <crl-file> <log-file>
post-down /usr/lib/dros/openvpn-iface stop <name> <pid>
```

helper 会向 OpenVPN 追加 `--dev <name>`，因此最终设备名以 `metadata.name` 为准。

PID 文件默认使用当前 settings 中的 `paths.run`，日志文件默认使用 `paths.logs`。
