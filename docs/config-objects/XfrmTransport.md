# XfrmTransport

## 用途

`XfrmTransport` 由 `network.xfrm` 插件使用，用来配置静态 transport mode ESP，当前主要配合
`Interface` 的 `type: gre` 使用。

这是多例配置。每个对象对应一组 `ip xfrm state/policy`。

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

`spec.enabled`：默认 `true`。为 `false` 时 `gw update xfrm/<name>` 会删除对应 state/policy。

`spec.activation`：默认 `manual`。预留字段，表示该 transport 是否应由系统级 update 自动启动。
当前 DROS 对被选中的对象会直接应用。

`spec.localParty`：必填，`partyA` 或 `partyB`，表示当前机器是哪一端。

`spec.selector.proto`：默认 `gre`。当前只支持 `gre`。

`spec.partyA.publicIp` / `spec.partyB.publicIp`：必填，两端公网或 outer IP。

`spec.partyA.privateIp` / `spec.partyB.privateIp`：可选。本端存在云厂商 EIP 映射等情况时使用；
不填则等于对应的 `publicIp`。

`spec.spi.partyAToPartyB` / `spec.spi.partyBToPartyA`：必填，两个方向的 SPI，支持整数或十六进制字符串。

`spec.reqid.partyAToPartyB` / `spec.reqid.partyBToPartyA`：必填，两个方向的 reqid，支持整数或十六进制字符串。

`spec.keys.partyAToPartyB` / `spec.keys.partyBToPartyA`：必填，两个方向的 AEAD key。

`spec.aead.name`：默认 `rfc4106(gcm(aes))`。

`spec.aead.icvBits`：默认 `128`。
