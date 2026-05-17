# DockerDNS

## 用途

`DockerDNS` 由 `docker.resources` 插件使用，用来把 DROS 管理的 Docker 容器同步成
dnsmasq `host-record`。

这是单例配置，名称固定为 `system`。执行 `gw update docker` 时，如果存在
`DockerDNS/system`，会在容器 compose 更新后同步 DNS；也可以单独执行
`gw update docker-dns`。

## 内置配置

DROS 没有内置 `DockerDNS` YAML。没有该对象时，不会生成 Docker DNS 记录。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DockerDNS
metadata:
  name: system
spec:
  enabled: true
  suffix: containers.lan
  file: /etc/dnsmasq.d/dros-40-containers.conf
  hostNetworkAddress: 10.0.0.1
```

容器侧配置：

```yaml
apiVersion: dros/v1alpha1
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  dnsNames:
    - web.containers.lan
  additionalDomains:
    - web.home.example.test
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

### `kind`

必须为 `DockerDNS`。

### `metadata.name`

必须为 `system`。

### `spec.enabled`

是否启用 Docker DNS 同步。

默认值：`true`。

为 `false` 时，DROS 会删除 `spec.file` 指向的 dnsmasq 片段，并重启 dnsmasq。

### `spec.suffix`

默认域名后缀。当容器未设置 `dnsNames` 时，DROS 会生成
`<container-name>.<suffix>`。

默认值：`containers.lan`。

### `spec.file`

生成的 dnsmasq 片段路径，必须为绝对路径。

默认值：`/etc/dnsmasq.d/dros-40-containers.conf`。

### `spec.hostNetworkAddress`

host network 容器默认解析地址。

默认值：无。未设置时，host network 容器不会生成 DNS 记录，除非
`hostNetworkAddresses` 为该容器单独指定地址。

### `spec.hostNetworkAddresses`

按容器名覆盖 host network 解析地址。

默认值：`{}`。

示例：

```yaml
hostNetworkAddresses:
  nginx: 10.0.0.1
  ddns-go: 10.0.0.2
```

## 行为

- 非 host network 容器通过 `docker inspect` 读取容器 IP，并解析到该 IP。
- host network 容器没有独立容器 IP，因此使用 `hostNetworkAddresses[name]`，否则使用
  `hostNetworkAddress`。
- `dnsNames` 会替换默认 `<name>.<suffix>`。
- `additionalDomains` 会追加额外完整域名，不自动追加 suffix。
- 生成文件有变化时会执行 `systemctl restart dnsmasq`。
