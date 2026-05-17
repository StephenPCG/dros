# CronJob

## 用途

`CronJob` 由 `config.resources` 插件使用，用来生成 `/etc/cron.d/dros-<name>`。

这是多例配置。每个对象对应一个 cron.d 文件。

## 内置配置

DROS 没有内置 `CronJob`。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: CronJob
metadata:
  name: refresh-routes
spec:
  schedule: "*/10 * * * *"
  user: root
  command: /usr/local/bin/gw hook route-refresh --verbose 0
  environment:
    PATH: /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  comment: Refresh policy routes from daemon queue.
```

## 字段

`spec.enabled`：默认 `true`。为 `false` 时保留 cron 文件，但注释实际任务。

`spec.schedule`：必填，标准 5 字段 cron schedule。

`spec.user`：默认 `root`。cron.d 文件中的执行用户。

`spec.command`：必填，单行命令。

`spec.environment`：默认 `{}`。写入 cron 文件顶部的环境变量。key 必须是 shell 变量名。

`spec.comment`：默认无。可选注释，会写入 cron 文件。
