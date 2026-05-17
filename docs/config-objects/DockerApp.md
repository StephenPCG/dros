# DockerApp

## 用途

`DockerApp` 由 `docker.resources` 插件使用，用来管理 DROS 内置 Docker app
模板。它和 `DockerContainer` 一样最终渲染成独立 compose project，但用户只需要配置 app
级参数。

这是多例配置。每个对象会生成：

```text
{paths.containers}/<name>/docker-compose.yml
{paths.containers}/<name>/data/
{paths.containers}/<name>/config/
{paths.containers}/<name>/generated/
```

默认 `paths.containers` 是 `/opt/gateway/containers`。

当前实现的 app：

- `vlmcsd`
- `nginx`
- `ddns-go`
- `unifi`
- `certimate`

`collectd` 和 `collectd-web` 暂不实现。

## 内置配置

DROS 没有内置 `DockerApp` YAML。

## 常见配置

### Vlmcsd

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: vlmcsd
spec:
  app: vlmcsd
```

### Nginx

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: nginx
spec:
  app: nginx
  variant: openresty
  network: host
  nginxConfFile:
    sourceType: inline
    name: nginx.conf
    source: |
      events {}
      http {
        include /etc/nginx/conf.d/*.conf;
      }
```

### Ddns-Go

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: ddns-go
spec:
  app: ddns-go
  network: default
```

### UniFi

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: unifi
spec:
  app: unifi
  network: br-app
```

### Certimate

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: certimate
spec:
  app: certimate
  mounts:
    - sourceType: dir
      source: certs
      target: /certs
      mode: rw
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

### `kind`

必须为 `DockerApp`。

### `metadata.name`

对象名称，同时作为 compose project、container name 和目录名。

只允许字母、数字、点、下划线和短横线。

### `metadata.disabled`

设置为 `true` 后，该对象会被忽略。当前不会自动删除已经生成的 compose project。

### `spec.enabled`

是否启用。为 `false` 时 `gw update docker` 会跳过该 app。

默认值：`true`。

### `spec.app`

必填。可选 `vlmcsd`、`nginx`、`ddns-go`、`unifi`、`certimate`。

### `spec.network`

容器网络。

- `default`：Docker 默认 bridge，也就是 `docker0`
- `host`：host network
- 其他值：自定义 Docker network 名称，必须存在同名 `Interface` 且 `spec.type: docker`

默认值：`default`。

### `spec.image` / `spec.imageName` / `spec.imageTag`

镜像覆盖字段。`image` 是完整镜像名，不能和 `imageName` 同时使用。`imageTag` 可替换或追加
tag。

默认镜像：

- `vlmcsd`：`mikolatero/vlmcsd:latest`
- `nginx` + `openresty`：`openresty/openresty:1.29.2.3-alpine-apk`
- `nginx` + `nginx`：`nginx:1.30-alpine`
- `ddns-go`：`jeessy/ddns-go:latest`
- `unifi`：`ghcr.io/goofball222/unifi:10.3`
- `certimate`：`certimate/certimate:latest`

### `spec.variant`

仅 `app: nginx` 使用。可选 `openresty` 或 `nginx`。

默认值：`openresty`。

### `spec.nginxConfFile`

仅 `app: nginx` 使用。挂载 nginx 主配置。

`variant: openresty` 时目标为 `/usr/local/openresty/nginx/conf/nginx.conf`；
`variant: nginx` 时目标为 `/etc/nginx/nginx.conf`。

### `spec.confFiles`

仅 `app: nginx` 使用。挂载站点配置。未设置 `target` 时，自动挂载到
`/etc/nginx/conf.d/<name>`。

### `spec.environment`

环境变量 mapping。

默认值：`{}`。

### `spec.mounts`

额外挂载列表。字段语义同 `DockerContainer.spec.mounts`。

默认值：`[]`。

## 内置挂载

`ddns-go` 固定挂载 `{paths.containers}/<name>/data` 到 `/root`。

`unifi` 固定挂载：

- `/etc/localtime:/etc/localtime:ro`
- `{paths.containers}/<name>/cert:/usr/lib/unifi/cert:rw`
- `{paths.containers}/<name>/data:/usr/lib/unifi/data:rw`
- `{paths.containers}/<name>/logs:/usr/lib/unifi/logs:rw`

`certimate` 固定挂载：

- `/etc/localtime:/etc/localtime:ro`
- `/etc/timezone:/etc/timezone:ro`
- `{paths.containers}/<name>/data:/app/pb_data:rw`
