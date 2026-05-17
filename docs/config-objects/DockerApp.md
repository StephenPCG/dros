# DockerApp

## 用途

`DockerApp` 由 `docker.resources` 插件使用，用来管理 DROS 内置 Docker app
模板。每个 app 都应当是独立预配置好的模板，只暴露少量适合该 app 的配置项。

这是多例配置。每个对象会生成：

```text
{paths.containers}/<name>/docker-compose.yml
{paths.containers}/<name>/data/
{paths.containers}/<name>/config/
{paths.containers}/<name>/generated/
```

默认 `paths.containers` 是 `/opt/gateway/containers`。compose project name 为
`dros-<name>`，service 名固定为 `app`，container name 固定为 `metadata.name`。

当前实现的 app：

- `vlmcsd`
- `nginx`
- `ddns-go`
- `unifi`
- `certimate`

`collectd` 和 `collectd-web` 暂不实现。

## 内置配置

DROS 没有内置 `DockerApp` YAML。

## 通用字段

这些字段当前对所有 app 都生效：

- `apiVersion`：建议写 `dros/v1alpha1`。
- `kind`：必须为 `DockerApp`。
- `metadata.name`：对象名，同时作为目录名、container name 和默认 DNS 名称来源。只允许字母、数字、点、下划线和短横线。
- `metadata.disabled`：为 `true` 时对象会被忽略。当前不会自动删除已经生成的 compose project。
- `spec.enabled`：是否启用。默认 `true`。为 `false` 时 `gw update docker` 会跳过该 app。
- `spec.app`：必填。可选 `vlmcsd`、`nginx`、`ddns-go`、`unifi`、`certimate`。
- `spec.dnsNames`：Docker DNS 名称列表。默认 `[]`。设置后会替换默认 `<metadata.name>.<DockerDNS.spec.suffix>`。
- `spec.additionalDomains`：额外完整域名列表。默认 `[]`。会追加到 Docker DNS 记录中，不自动追加 suffix。

这些字段当前代码也对所有 app 接受，但从“每个 app 只暴露少量配置项”的角度看，后续可能需要继续收紧：

- `spec.network`：默认 `default`。可选 `default`、`host` 或自定义 Docker interface 名称。
- `spec.image` / `spec.imageName` / `spec.imageTag`：镜像覆盖。默认使用各 app 固定镜像。
- `spec.environment`：额外环境变量。默认 `{}`。所有 DockerApp 都会内置
  `TZ=Asia/Shanghai`，用户可在这里添加其他变量，也可以提供 `TZ` 覆盖默认值。
- `spec.mounts`：额外挂载。默认 `[]`。字段语义同 `DockerContainer.spec.mounts`。

下面按 app 列出当前实现里的固定配置和允许配置字段。

## `app: vlmcsd`

### 固定配置

- 默认镜像：`mikolatero/vlmcsd:latest`
- 默认网络：`default`
- restart policy：`unless-stopped`
- 固定挂载：无
- 固定环境变量：`TZ=Asia/Shanghai`，可通过 `spec.environment.TZ` 覆盖
- 固定 cap/device/command：无

### 允许额外配置

- `enabled`：默认 `true`
- `network`：默认 `default`
- `image` / `imageName` / `imageTag`：默认使用固定镜像
- `environment`：默认 `{}`，会叠加到内置 `TZ=Asia/Shanghai` 上
- `mounts`：默认 `[]`
- `dnsNames`：默认 `[]`
- `additionalDomains`：默认 `[]`

### 示例

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: vlmcsd
spec:
  app: vlmcsd
```

## `app: nginx`

### 固定配置

- 默认 variant：`openresty`
- `variant: openresty` 默认镜像：`openresty/openresty:1.29.2.3-alpine-apk`
- `variant: nginx` 默认镜像：`nginx:1.30-alpine`
- 默认网络：`default`
- restart policy：`unless-stopped`
- 固定挂载：无。只有配置 `nginxConfFile` / `confFiles` 后才生成挂载。
- 固定环境变量：`TZ=Asia/Shanghai`，可通过 `spec.environment.TZ` 覆盖
- 固定 cap/device/command：无

### 允许额外配置

- `enabled`：默认 `true`
- `variant`：默认 `openresty`，可选 `openresty`、`nginx`
- `network`：默认 `default`
- `image` / `imageName` / `imageTag`：默认由 `variant` 决定
- `nginxConfFile`：默认无。用于挂载 nginx 主配置。
- `confFiles`：默认 `[]`。用于挂载站点配置。
- `environment`：默认 `{}`，会叠加到内置 `TZ=Asia/Shanghai` 上
- `mounts`：默认 `[]`
- `dnsNames`：默认 `[]`
- `additionalDomains`：默认 `[]`

### `nginxConfFile`

- `sourceType`：可选 `inline`、`file`
- `source`：必填
- `mode`：默认 `ro`
- `name`：可选，`inline` 生成文件名

挂载目标由 `variant` 固定：

- `openresty`：`/usr/local/openresty/nginx/conf/nginx.conf`
- `nginx`：`/etc/nginx/nginx.conf`

### `confFiles[]`

- `sourceType`：可选 `inline`、`file`、`dir`
- `source`：必填
- `target`：可选。未设置时挂载到 `/etc/nginx/conf.d/<name>`
- `mode`：默认 `ro`
- `name`：可选

### 示例

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

## `app: ddns-go`

### 固定配置

- 默认镜像：`jeessy/ddns-go:latest`
- 默认网络：`default`
- restart policy：`unless-stopped`
- 固定挂载：`{paths.containers}/<name>/data:/root:rw`
- 固定环境变量：`TZ=Asia/Shanghai`，可通过 `spec.environment.TZ` 覆盖
- 固定 cap/device/command：无

### 允许额外配置

- `enabled`：默认 `true`
- `network`：默认 `default`
- `image` / `imageName` / `imageTag`：默认使用固定镜像
- `environment`：默认 `{}`，会叠加到内置 `TZ=Asia/Shanghai` 上
- `mounts`：默认 `[]`
- `dnsNames`：默认 `[]`
- `additionalDomains`：默认 `[]`

### 示例

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: ddns-go
spec:
  app: ddns-go
```

## `app: unifi`

### 固定配置

- 默认镜像：`ghcr.io/goofball222/unifi:10.3`
- 默认网络：`default`
- restart policy：`unless-stopped`
- 固定环境变量：`TZ=Asia/Shanghai`，可通过 `spec.environment.TZ` 覆盖
- 固定 cap/device/command：无
- 固定挂载：
  - `/etc/localtime:/etc/localtime:ro`
  - `{paths.containers}/<name>/cert:/usr/lib/unifi/cert:rw`
  - `{paths.containers}/<name>/data:/usr/lib/unifi/data:rw`
  - `{paths.containers}/<name>/logs:/usr/lib/unifi/logs:rw`

### 允许额外配置

- `enabled`：默认 `true`
- `network`：默认 `default`
- `image` / `imageName` / `imageTag`：默认使用固定镜像
- `environment`：默认 `{}`，会叠加到内置 `TZ=Asia/Shanghai` 上
- `mounts`：默认 `[]`
- `dnsNames`：默认 `[]`
- `additionalDomains`：默认 `[]`

### 示例

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: unifi
spec:
  app: unifi
  network: br-app
```

## `app: certimate`

### 固定配置

- 默认镜像：`certimate/certimate:latest`
- 默认网络：`default`
- restart policy：`unless-stopped`
- 固定环境变量：`TZ=Asia/Shanghai`，可通过 `spec.environment.TZ` 覆盖
- 固定 cap/device/command：无
- 固定挂载：
  - `/etc/localtime:/etc/localtime:ro`
  - `/etc/timezone:/etc/timezone:ro`
  - `{paths.containers}/<name>/data:/app/pb_data:rw`

### 允许额外配置

- `enabled`：默认 `true`
- `network`：默认 `default`
- `image` / `imageName` / `imageTag`：默认使用固定镜像
- `environment`：默认 `{}`，会叠加到内置 `TZ=Asia/Shanghai` 上
- `mounts`：默认 `[]`
- `dnsNames`：默认 `[]`
- `additionalDomains`：默认 `[]`

### 示例

```yaml
apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: certimate
spec:
  app: certimate
```

## 当前实现中需要重点审查的点

当前代码为了先跑通 DockerApp，仍然保留了较多通用扩展字段：

- 所有 app 都允许覆盖 `network`。
- 所有 app 都允许覆盖镜像。
- 所有 app 都允许追加 `environment` 和 `mounts`。

如果希望严格符合“每个 app 独立预配置，只暴露少量配置项”的设计，后续应在
`docker.resources` 的 validation 中按 app 收紧字段，而不是只靠文档约束。
