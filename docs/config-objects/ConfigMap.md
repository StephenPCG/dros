# ConfigMap

## 用途

`ConfigMap` 由通用配置资源插件校验，并可被 Docker 相关对象引用。它主要用来保存小段配置文件内容，
例如 nginx 配置、应用配置片段等。

这是多例配置。每个对象可以包含多个文件 key。

## 内置配置

DROS 没有内置 `ConfigMap`。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: ConfigMap
metadata:
  name: nginx-config
spec:
  files:
    default.conf: |
      server {
        listen 80;
      }
```

Docker mount 引用示例：

```yaml
mounts:
  - sourceType: configMap
    source: nginx-config
    key: default.conf
    target: /etc/nginx/conf.d/default.conf
    mode: ro
```

## 字段

`spec.files`：必填，类型为 mapping。key 是相对文件路径，value 是文件内容。

key 不能是绝对路径，也不能包含 `..` 路径段。DROS 会把被引用的文件渲染到对应容器目录的
`generated/configmap/<configmap>/<key>` 下，再挂载到容器中。
