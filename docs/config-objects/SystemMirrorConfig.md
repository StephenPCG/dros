# SystemMirrorConfig

## 用途

`SystemMirrorConfig` 由 `system.mirror` 插件使用，主要配置系统软件源：

- Debian apt mirror
- Docker CE apt mirror
- Tailscale apt mirror
- Docker registry mirror

这是单例配置。DROS 只期望当前配置目录 overlay 后存在一个有效的
`SystemMirrorConfig`。如果没有 `metadata.name: default` 的对象，但只有一个
`SystemMirrorConfig` 对象，DROS 会把它当作这个单例使用。

推荐命名为 `system`。

## 内置配置

DROS 没有内置的 `SystemMirrorConfig` YAML 文件。也就是说，用户配置目录里可以完全不写这个对象。

不过字段默认值在代码中定义。没有用户对象时，效果等价于：

```yaml
apiVersion: dros/v1alpha1
kind: SystemMirrorConfig
metadata:
  name: system
spec:
  aptMirror: https://mirrors.ustc.edu.cn/debian
  dockerAptMirror: https://mirrors.ustc.edu.cn/docker-ce
  tailscaleAptMirror: https://mirrors.ustc.edu.cn/tailscale
  dockerRegistryMirror: ""
```

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: SystemMirrorConfig
metadata:
  name: system
spec:
  aptMirror: https://mirrors.ustc.edu.cn/debian
  dockerAptMirror: https://mirrors.ustc.edu.cn/docker-ce
  tailscaleAptMirror: https://mirrors.ustc.edu.cn/tailscale
```

如果需要 Docker registry mirror：

```yaml
apiVersion: dros/v1alpha1
kind: SystemMirrorConfig
metadata:
  name: system
spec:
  dockerRegistryMirror: https://registry.example
```

## 字段

### `apiVersion`

当前建议写 `dros/v1alpha1`。

如果写了该字段，当前必须为 `dros/v1alpha1`；省略时按当前版本处理。

默认值：无。示例中建议显式写出。

### `kind`

必须为 `SystemMirrorConfig`。

默认值：无。必须显式写出。

### `metadata.name`

对象名称。

这是单例配置，所以名称不参与实际 mirror 语义。推荐使用 `system`。如果只有一个
`SystemMirrorConfig`，即使名称不是 `default`，DROS 也会使用它。

默认值：如果省略，loader 视为 `default`。

### `metadata.disabled`

是否禁用该对象。

设置为 `true` 后，这个对象会被忽略，效果等同于这个文件不存在。它不会自动清理已经写入系统的文件。

默认值：`false`。

### `spec.aptMirror`

Debian apt mirror。

`gw bootstrap` 会用它生成 `/etc/apt/sources.list`。当前默认 Debian codename 为 `trixie`，如果
`sysRoot` 中存在 `/etc/os-release` 且包含 `VERSION_CODENAME`，则优先使用该值。

默认值：`https://mirrors.ustc.edu.cn/debian`。

### `spec.dockerAptMirror`

Docker CE apt mirror。

`gw bootstrap` 会用它生成 `/etc/apt/sources.list.d/docker-ce.list`，并在真实系统上从该 mirror 下载
Docker apt GPG key 到 `/etc/apt/keyrings/docker.asc`。

默认值：`https://mirrors.ustc.edu.cn/docker-ce`。

### `spec.tailscaleAptMirror`

Tailscale apt mirror。

`gw bootstrap` 会用它生成 `/etc/apt/sources.list.d/tailscale.list`。Tailscale apt GPG key
固定从官方 `https://pkgs.tailscale.com/stable` 下载到
`/usr/share/keyrings/tailscale-archive-keyring.gpg`，因为部分镜像站不会同步 `*.noarmor.gpg`
文件。

默认值：`https://mirrors.ustc.edu.cn/tailscale`。

### `spec.dockerRegistryMirror`

Docker registry mirror。

非空时，`docker.core` 插件会写入 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": ["<dockerRegistryMirror>"]
}
```

默认值：空字符串，表示不配置 registry mirror。
