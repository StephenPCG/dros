# OpenVPN Management

OpenVPN 管理模块负责实例、CA、server/client profile、证书、CRL 和客户端
`.ovpn` 文件生成。它不是 ConfigObject，也不由 `gw update` 驱动。

`Interface type=openvpn` 只负责把某个 OpenVPN 配置接入 ifupdown，让接口能被
`ifup/ifdown` 管理。证书、profile、CRL 和 `.ovpn` 文件的生命周期由
`gw ovpn ...` 与 Web OpenVPN 页面管理。

## Storage

默认数据根目录为：

```text
/opt/gateway/ovpn/{instance}
```

这个路径来自 `paths.containers` 的父目录。例如默认
`paths.containers=/opt/gateway/containers`，则 OpenVPN 根目录为
`/opt/gateway/ovpn`。

每个实例内的结构：

```text
config.yaml
pki/
  ca/
    ca.crt
    ca.key
    crl.pem
servers/{name}/
  profile.yaml
  server.conf
  certs/{timestamp}/
clients/{name}/
  profile.yaml
  client-auto.ovpn
  client-{server}.ovpn
  certs/{timestamp}/
state/revoked.jsonl
```

`certs/latest` 是指向最新证书目录的 symlink。证书 renew 不覆盖旧证书，revoke
会记录到 `state/revoked.jsonl` 并刷新 CRL。

## CLI

创建实例和 CA：

```sh
gw ovpn init office --ca-cn "office OpenVPN CA"
gw ovpn bootstrap --instance office
```

创建 server profile：

```sh
gw ovpn server create beijing \
  --instance office \
  --endpoint beijing.vpn.example.net \
  --network 10.60.253.0 \
  --netmask 255.255.255.0
gw ovpn server update beijing --instance office
```

创建 client profile：

```sh
gw ovpn client create alice --instance office
gw ovpn client update alice --instance office
```

证书操作：

```sh
gw ovpn server renew beijing --instance office
gw ovpn client renew alice --instance office
gw ovpn cert revoke --client alice --instance office
gw ovpn crl renew --instance office
```

查看状态：

```sh
gw ovpn list instances
gw ovpn list profiles --instance office
gw ovpn list certs --instance office
gw ovpn doctor --instance office
```

会写入 OpenVPN 数据目录的命令会自动 sudo，并使用手动 CLI 的全局 apply lock，
避免并发修改 CA index、serial、CRL 或 profile 输出文件。

## Web

Web OpenVPN 页面支持：

- 查看实例列表，以及 server/client profile 和证书数量。
- 查看某个实例的 server/client profile 列表。
- 在已有实例中创建 server/client profile。
- Renew server/client profile 证书。
- 查看 profile 的证书列表。
- Revoke 证书并刷新 CRL。
- 下载最新的 `server.conf` 或 `client-auto.ovpn`。

Web 不支持创建 OpenVPN 实例。实例创建仍然只通过 CLI 完成：

```sh
gw ovpn init office
```

Web 创建 server profile 时，如果没有填写 endpoint，会使用
`{profile-name}.vpn.example.net` 作为默认 endpoint，和 CLI 的非交互默认值一致。

## Instance Config

实例配置文件为 `{instance}/config.yaml`。`gw ovpn init` 会写入完整默认配置；
后续可手动编辑。

默认配置：

```yaml
ca:
  cn: office OpenVPN CA
  days: 3650
cert:
  days: 825
crl:
  days: 365
auth:
  client_cert: required
  user_auth:
    type: none
    plugin: null
    config: null
    verify_command: null
options:
  proto: udp
  serverListenFamily: both
  clientConnectFamily: auto
  port: 1194
  dev_type: tun
  topology: subnet
  multihome: true
  allow_compression: "no"
  keepalive:
    - 10
    - 60
  verb: 3
  push: []
  raw_server_options: []
  raw_client_options: []
```

字段说明：

- `ca.cn`: CA 证书 CN。默认 `{instance} OpenVPN CA`。
- `ca.days`: CA 有效天数。默认 `3650`。
- `cert.days`: server/client 证书有效天数。默认 `825`。
- `crl.days`: CRL 有效天数。默认 `365`。
- `auth.client_cert`: client 证书策略。可选 `required`、`optional`、`none`。
- `auth.user_auth.type`: 用户认证类型。默认 `none`。
- `auth.user_auth.plugin`: OpenVPN auth plugin 路径。
- `auth.user_auth.config`: plugin 配置路径。
- `auth.user_auth.verify_command`: `auth-user-pass-verify` 命令。
- `options.proto`: 传输协议。可选 `udp`、`tcp`。
- `options.serverListenFamily`: server 监听地址族。可选 `both`、`v4only`、`v6only`。
- `options.clientConnectFamily`: client remote 协议地址族。可选 `auto`、`v4only`、`v6only`。
- `options.port`: server 监听端口。默认 `1194`。
- `options.dev_type`: OpenVPN 设备类型。可选 `tun`、`tap`。
- `options.topology`: OpenVPN topology。默认 `subnet`。
- `options.multihome`: 是否写入 `multihome`。默认 `true`。
- `options.allow_compression`: 写入 `allow-compression`。默认 `"no"`。
- `options.keepalive`: 写入 `keepalive <a> <b>`。默认 `[10, 60]`。
- `options.verb`: OpenVPN 日志级别。默认 `3`。
- `options.push`: server 端 push 选项列表。
- `options.raw_server_options`: 追加到 server 配置末尾的原始行。
- `options.raw_client_options`: 追加到 client 配置末尾的原始行。

## Server Profile

server profile 位于：

```text
servers/{name}/profile.yaml
```

常见配置：

```yaml
name: beijing
endpoint: beijing.vpn.example.net
cn: beijing.vpn.example.net
network: 10.60.253.0
netmask: 255.255.255.0
options:
  port: 1194
```

字段说明：

- `name`: profile 名称。目录名也使用这个值。
- `endpoint`: 客户端连接时写入 `remote` 的主机名或地址。
- `cn`: server 证书 CN。默认使用 `endpoint`。
- `network`: OpenVPN server 网段地址。默认 `10.8.0.0`。
- `netmask`: OpenVPN server 网段掩码。默认 `255.255.255.0`。
- `options`: 覆盖实例级可变选项，如 `port`、`proto`、`push`。

`client_dev` 和 `dev_type` 是实例级选项，不能在 profile 中覆盖。

## Client Profile

client profile 位于：

```text
clients/{name}/profile.yaml
```

常见配置：

```yaml
name: alice
cn: alice
options: {}
```

字段说明：

- `name`: profile 名称。目录名也使用这个值。
- `cn`: client 证书 CN。默认使用 `name`。
- `options`: 覆盖实例级 client 选项，如 `clientConnectFamily`、`raw_client_options`。

执行 `gw ovpn client update alice --instance office` 时，会为每个 server profile
生成一个 `client-{server}.ovpn`，并生成包含所有 server connection 的
`client-auto.ovpn`。

如果 `auth.client_cert: none`，client `.ovpn` 不内嵌 client 证书和 key，且
`gw ovpn client renew` 会失败。

## Interface Integration

证书管理完成后，可以在 `Interface` 中引用生成好的配置：

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: ovpn-office
spec:
  type: openvpn
  configFile: /opt/gateway/ovpn/office/clients/alice/client-auto.ovpn
  crlFile: /opt/gateway/ovpn/office/pki/ca/crl.pem
  listen:
    - proto: udp
      port: 1194
```

`gw update iface/ovpn-office` 会把配置复制到 `/etc/dros/openvpn` 并生成
ifupdown fragment。OpenVPN 证书和 profile 本身仍由 `gw ovpn ...` 管理。
