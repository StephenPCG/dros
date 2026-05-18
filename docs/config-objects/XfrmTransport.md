# XfrmTransport

## 用途

`XfrmTransport` 由 `network.xfrm` 插件使用，用来配置静态 transport mode ESP，可由
`Interface` 的 `type: gre` 或 `type: wireguard` 引用，也可以通过 `activation: system`
生成独立 systemd unit 在开机时应用。

这是多例配置。每个对象对应一组 `ip xfrm state/policy`。XFRM state/policy 是内核运行时状态，
不会因为写入 ConfigObject 自动持久化；持久化入口由接口 hook 或 systemd unit 负责。

## 内置配置

DROS 没有内置 `XfrmTransport`。

## 常见配置

```yaml
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
```

## 字段

`spec.enabled`：默认 `true`。为 `false` 时，`gw update xfrm/<name>` 会删除 `activation: system`
生成的 unit，并执行 `gw stop` 等价的 state/policy 清理。

`spec.activation`：默认 `manual`。支持：

- `manual`：`gw update xfrm/<name>` 只校验配置并清理旧的 systemd unit，不主动创建
  XFRM state/policy。运行时由 `gw start xfrm/<name>`、`gw stop xfrm/<name>`，或引用它的
  `gre` / `wireguard` interface hook 管理。
- `system`：`gw update xfrm/<name>` 生成
  `/etc/systemd/system/dros-xfrm-<name>.service`，执行 `systemctl enable --now`，开机后由
  systemd 调用 `gw start xfrm/<name>` 自动应用。`activation: system` 的 XfrmTransport
  不能被 Interface 引用，避免同一组 state/policy 同时由 systemd 和接口生命周期管理。

`spec.localParty`：必填，`partyA` 或 `partyB`，表示当前机器是哪一端。

`spec.selector.proto`：默认 `gre`。支持 `gre` 和 `udp`。`udp` 表示匹配两端 outer IP
之间的全部 UDP 流量；当前不支持按 UDP 端口筛选。

`spec.partyA.publicIp` / `spec.partyB.publicIp`：必填，两端公网或 outer IP。

`spec.partyA.privateIp` / `spec.partyB.privateIp`：可选。本端存在云厂商 EIP 映射等情况时使用；
不填则等于对应的 `publicIp`。

`spec.spi.partyAToPartyB` / `spec.spi.partyBToPartyA`：必填，两个方向的 SPI，支持整数或十六进制字符串。

`spec.reqid.partyAToPartyB` / `spec.reqid.partyBToPartyA`：必填，两个方向的 reqid，支持整数或十六进制字符串。

`spec.keys.partyAToPartyB` / `spec.keys.partyBToPartyA`：必填，两个方向的 AEAD key。

`spec.aead.name`：默认 `rfc4106(gcm(aes))`。

`spec.aead.icvBits`：默认 `128`。

## 生效方式

`gw start xfrm/<name>` 会先静默删除同名配置对应的 policy/state，再重新 `ip xfrm state add`
和 `ip xfrm policy add`。`gw stop xfrm/<name>` 会静默删除 policy/state。两者都按重复调用不报错
设计，因此同一个 XfrmTransport 即使被接口 reload、人工 start/stop 或 systemd restart 重复触发，
也不会因为已有或已删除的 state/policy 直接失败。

`activation: manual` 不会在 `gw update` 时主动 start，适合交给 `Interface.spec.xfrmTransport`
随接口生命周期启停。接口生命周期中使用的是 `gw hook xfrm-start <name>` /
`gw hook xfrm-stop <name>`，只负责把事件送入 `drosd` 队列；daemon 后续串行执行
实际的 XFRM start/stop。因此它不会阻塞 ifupdown 等待 XFRM 已经应用，也不会和外层
`gw update` 竞争手动 CLI lock。`activation: system` 会由 `dros-xfrm-<name>.service`
管理，适合不依附某个接口的 selector。

## SPI、REQID 与 Key

`spi` 是 ESP 包头里的 Security Parameter Index。对端收到 ESP 包时，会用 `dst + proto esp + spi`
找到对应的 XFRM state。因此两个方向必须使用不同的 SPI，且同一台机器上面向同一对端的 SPI
不要重复。取值是 32-bit unsigned integer，DROS 接受十进制或 `0x...` 十六进制；实践中不要使用
`0x00000000`。

`reqid` 是 Linux XFRM 本机用于把 policy 和 state 关联起来的 ID，不会上 wire。它也是 32-bit
unsigned integer，支持十进制或十六进制。两端可以使用相同的 reqid 编号，也可以不同；但在同一台
机器上，建议为每个 XfrmTransport 或每个方向分配稳定、不冲突的 reqid，便于排障。

`keys` 是 AEAD key material。当前默认算法为 `rfc4106(gcm(aes))`，示例按 AES-128-GCM 写法生成：
20 字节 key material，其中前 16 字节是 AES key，后 4 字节是 salt。两个方向可以使用不同 key；
推荐不同方向使用不同 key。两端配置必须按方向一致：`partyAToPartyB` 的 SPI/key/reqid 描述
A 发往 B 的方向，`partyBToPartyA` 描述 B 发往 A 的方向。

快速生成示例：

```bash
# 生成两个 32-bit SPI；如果极小概率生成 0x00000000，重新生成即可。
printf 'partyAToPartyB: "0x%s"\n' "$(openssl rand -hex 4)"
printf 'partyBToPartyA: "0x%s"\n' "$(openssl rand -hex 4)"

# reqid 可以人工分配稳定小整数；同机不冲突即可。
printf 'partyAToPartyB: %d\n' 100
printf 'partyBToPartyA: %d\n' 101

# 生成两个 AES-128-GCM key material：20 字节 = 16 字节 AES key + 4 字节 salt。
printf 'partyAToPartyB: "0x%s"\n' "$(openssl rand -hex 20)"
printf 'partyBToPartyA: "0x%s"\n' "$(openssl rand -hex 20)"
```
