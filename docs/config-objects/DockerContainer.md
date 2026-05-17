# DockerContainer

## 用途

`DockerContainer` 由 `docker.resources` 插件使用，用来管理一个通用 Docker
compose project。

这是多例配置。每个对象会生成：

```text
{paths.containers}/<name>/docker-compose.yml
{paths.containers}/<name>/data/
{paths.containers}/<name>/config/
{paths.containers}/<name>/generated/
```

默认 `paths.containers` 是 `/opt/gateway/containers`。

`gw update docker`、`gw update docker-container/<name>` 会写入 compose 文件并执行
`docker compose up -d`。当 inline 生成文件发生变化时，会额外执行一次
`docker compose restart`。

## 内置配置

DROS 没有内置 `DockerContainer` YAML。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  network: default
  restart: unless-stopped
  environment:
    TZ: Asia/Shanghai
  mounts:
    - sourceType: inline
      name: default.conf
      target: /etc/nginx/conf.d/default.conf
      mode: ro
      source: |
        server {
          listen 80;
        }
```

使用自定义 Docker bridge network：

```yaml
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br-app
spec:
  type: docker
  subnet: 172.30.0.0/24
---
apiVersion: dros/v1alpha1
kind: DockerContainer
metadata:
  name: app
spec:
  image: nginx:stable-alpine
  network: br-app
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

### `kind`

必须为 `DockerContainer`。

### `metadata.name`

对象名称，同时作为 compose project、container name 和目录名。

只允许字母、数字、点、下划线和短横线。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。当前不会自动删除已经生成的 compose project。

### `spec.enabled`

是否启用。为 `false` 时 `gw update docker` 会跳过该容器。

默认值：`true`。

### `spec.image`

必填。完整 Docker image 名称。

### `spec.network`

容器网络。

- `default`：Docker 默认 bridge，也就是 `docker0`
- `host`：host network
- 其他值：自定义 Docker network 名称，必须存在同名 `Interface` 且 `spec.type: docker`

默认值：`default`。

### `spec.restart`

compose restart policy。

可选值：`no`、`always`、`on-failure`、`unless-stopped`。

默认值：`unless-stopped`。

### `spec.environment`

环境变量 mapping。

默认值：`{}`。

### `spec.mounts`

额外挂载列表。

默认值：`[]`。

### `spec.capAdd` / `spec.capDrop`

映射到 compose 的 `cap_add` / `cap_drop`。

默认值：`[]`。

### `spec.devices`

映射到 compose 的 `devices`。

默认值：`[]`。

### `spec.privileged`

映射到 compose 的 `privileged`。

默认值：`false`。

### `spec.command`

映射到 compose 的 `command`。可为字符串、字符串数组或省略。

默认值：无。

### `spec.dnsNames`

Docker DNS 名称列表。设置后会替换默认 `<metadata.name>.<DockerDNS.spec.suffix>`。

默认值：`[]`。

### `spec.additionalDomains`

额外完整域名列表，会追加到 Docker DNS 记录中，不自动追加 suffix。

默认值：`[]`。

## `mounts[]`

- `sourceType`：必填。可选 `inline`、`file`、`dir`、`data-dir`
- `source`：来源。`inline` 时是文件内容；`file` / `dir` 时是宿主路径；`data-dir` 可省略
- `target`：必填，容器内绝对路径
- `mode`：`ro` 或 `rw`，默认 `rw`
- `name`：inline 生成文件名，默认使用 `target` basename

相对 `source` 会解析到 `{paths.containers}/<name>/` 下。`dir` 和 `data-dir` 会自动创建目录。
