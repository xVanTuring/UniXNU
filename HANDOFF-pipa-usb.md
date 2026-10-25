# 交接：pipa 的 USB 设备模式（被 macOS 当作 iPad 识别并可访问数据）

> 状态：**只读调研（2026-10-25），没写一行驱动、没改 board.toml、没上机**。设备当时不可用，USB 口还被
> fastboot 占着。本文所有结论都标了来源（`file:line`、DTS 行号、kernelcache 里的地址）；凡是推断的都写
> 成"未知项 U#"，并给了查证办法。

---

## 0. 一句话结论

Apple 的 `AppleUSBXDCI`（`IOUSBDeviceFamily.kext` 的插件）**本身就是一颗 DWC3 设备控制器的驱动**——它用
TRB + `DEPCMD`/`param0..2` + `DCTL/DSTS`，和 SM8250 的 `dwc3@a600000` 是同一套 Synopsys IP。所以我们不需要
重写整条 gadget 栈：**写一个 `AppleUSBXDCI` 的子类，只补上高通侧的时钟/GDSC/PHY/复位/SMMU 和中断桥接**，
上层（`IOUSBDeviceController` → 配置描述符 → `IOUSBDeviceInterface` → `AppleUSBDeviceMux`）全部是现成的，
而用户态的 `lockdownd` / `afcd` 已经在镜像里。剩下的主要风险在 ABI 逆向（vtable、几个 DT 属性的语义）和
USB 的 SMMU（我们的内核可用物理内存在 4 GiB 以上，而 dwc3 的 TRB 地址宽度可能只有 32 位，见 U7）。

---

## 1. 事实核查

### 1.1 仓库里确实没有 USB 驱动

```
UniXNU/drivers/          ArmCpu ArmDTPlatform ArmVirtPlatform DarwinBoot NativeAdreno NativeAES
                         NativeGeniSpi NativeHidInput NativeKeyStoreEvents NativeMetal
                         NativeMobileDisplay NativeRamBlock NativeUfs NativeVirtioBlock NativeVirtioConsole
UniXNU-pipa/drivers/     ArmCpu NativeAdreno NativeMetal NativeMobileDisplay NativeNovatekTouch NativeUfs
```

没有 `NativeUsb*`，也没有任何 `dwc3`/`xhci`/`gadget` 相关代码。`NativeHidInput` 是 HID 事件注入，不是 USB。

### 1.2 硬件事实（`/Users/xvan/agents-home/uefi-pipa/Resources/DTBs/pipa.dts`）

| 项 | 值 | 出处 |
| --- | --- | --- |
| USB0 控制器 | `ssusb@a600000`，`compatible = "qcom,dwc-usb3-msm"`，`reg = <0xa600000 0x100000>` | 18677-18679 |
| dwc3 核心 | `dwc3@a600000`，`snps,dwc3`，`reg = <0xa600000 0xd93c>`（≈ 55 KiB 核心寄存器窗） | 18709-18711 |
| 核心中断 | `interrupts = <0 0x85 4>` → **SPI 133，电平高**，INTID = 32+133 = **165** | 18712 |
| 速度配置 | `maximum-speed = "super-speed"`、`dr_mode = "drd"`、`snps,usb3-u1u2-disable` | 18717-18722 |
| BAM（QDSS 侧） | `qcom,usbbam@a704000`，`reg = <0xa704000 0x17000>`，IRQ SPI 132（INTID 164） | 18726-18729 |
| HS PHY | `hsphy@88e3000`，`qcom,usb-hsphy-snps-femto`，`reg = <0x88e3000 0x110 0x88e2000 0x04>`，带 `qcom,param-override-seq` / `qcom,global-param-override-seq` | 18755-18770 |
| SS PHY | `ssphy@88e8000`，`qcom,usb-ssphy-qmp-dp-combo`，`reg = <0x88e8000 0x3000>`，带完整 `qcom,qmp-phy-init-seq`、`qcom,qmp-phy-reg-offset`、**`qcom,vbus-valid-override`** | 18772-18791 |
| 空 PHY | `usb_nop_phy`（`usb-nop-xceiv`） | 18801 |
| 第二个控制器 | `ssusb@a800000`，`status = "disable"`，`dr_mode = "host"` | 18806-18876 |
| USB3 GDSC | `usb30_prim_gdsc`，`qcom,gdsc@10f004`（GCC + 0x10f004），`qcom,retain-regs` | 3884-3892 |
| GCC | `qcom,gcc@100000`，`reg = <0x100000 0x1f0000>`，phandle 0x16 | 3737-3748 |
| 时钟 | `clocks = <0x16 0xb6 0x16 0x0f 0x16 0x08 0x16 0xb8 0x16 0xbb 0x16 0xc7>`（core / iface / bus_aggr / utmi / sleep / xo） | 18689-18690 |
| 复位 | `resets = <0x16 0x22>`（core_reset）；hsphy `<0x16 0x1b>`；ssphy `<0x16 0x24 0x16 0x26>` | 18691, 18767, 18788 |
| IOMMU | `iommus = <0x47 0x00 0x00>`；`qcom,iommu-dma = "atomic"`；**`qcom,iommu-dma-addr-pool = <0x90000000 0x60000000>`** | 18680-18682 |
| apps SMMU | `apps-smmu@15000000`，phandle 0x47 | 11939-11957 |
| 互联（NoC） | `mas-xm-usb3-0`，`cell-id = 0x3d`，`qos-clks = <0x16 0x08>` | 9098-9115 |
| PDC | `interrupt-controller@b220000`，`qcom,kona-pdc`，phandle 0x75；`ssusb` 的 4 个 phy/event 中断全走 PDC | 3699-3707, 18684-18686 |
| `extcon` | `<0x654 0x3b0>` = PM8150B 的 USB PD / 充电器，用于线缆与角色检测 | 18698 |

`qcom,vbus-valid-override`（18776）很重要：它让 QMP PHY 不看 VBUS 有效位，所以我们**不必**先把 PM8150B
的充电器/PD 驱动跑起来就能枚举。

### 1.3 固件侧（`uefi-pipa`）：没有任何 USB 控制器驱动

- `Platforms/Xiaomi/pipaPkg/pipa.dsc` 的 `[Components]`（96-108）只含 ACPI、UniXNU、ShengBootMemoryDxe、
  DarwinBoot；`KonaPkg.dsc.inc` 与 `pipa.fdf` 里 `grep -i usb` 无结果。
- `Resources/MuPatches/Usb-Bus.patch` 只是把 `UsbBusDxe` 的 TPL 从 `TPL_NOTIFY` 改成 `TPL_CALLBACK`，是 host
  侧 USB 总线补丁，与设备模式无关。
- Mu-Silicium 的 `Status.md:2963` 给 pipa 标了 "USB Device Mode ✅"，但**这个固件构建里没有对应组件**，所以
  那一行不能当作事实依据（大概是 ABL/fastboot 留下的状态，或从别的板子抄的）。
- 存在**预编译的**高通二进制：`Binaries/<device>/QcomPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.inf` 与
  `UsbDeviceDxe/UsbDeviceDxe.inf`（b4q、marble、spaceport… 多块板子都有；pipa 的 Binaries 目录未查，见 U8）。
  这是唯一现成的"高通自己怎么初始化 dwc3 + PHY + 时钟"的参考实现（二进制，无源码）。

**结论**：XNU 接手时，dwc3 的状态完全由 ABL 的 fastboot 栈决定，固件没碰过它。ABL 交棒前是否做了 soft
disconnect、时钟/PHY 是否还开着，未知（U5）。这既是机会（可能省掉大部分 PHY 初始化）也是坑（状态不确定）。

### 1.4 目标内核里有哪些 USB kext（`out/ipsw/21H461/kernelcache.decompressed`）

从 `__PRELINK_INFO`（文件偏移 `0x292c000`，大小 `0x318000`）里解析出 176 个 kext。USB 相关的：

**设备模式（gadget）这一族**

| Bundle | 可执行文件偏移 | 角色 |
| --- | --- | --- |
| `com.apple.iokit.IOUSBDeviceFamily` | `0xfffffff0054789c0`, 0x4873 | `IOUSBDeviceController`、`IOUSBDeviceInterface`、`IOUSBDevicePipe`、`IOUSBDeviceControlRequest`、`IOUSBDeviceConfigurator`、`IOUSBDeviceInterfaceReserved`、user client |
| `com.apple.driver.AppleUSBXDCI` | `0xfffffff00556d8c0`, 0x8878 | **DWC3 设备控制器驱动**（见 1.5） |
| `com.apple.driver.AppleT8011USBXDCI` | `0xfffffff005576140`, 0x1108 | iPad7,1（T8011）的薄子类：找寄存器窗、开关 Apple PHY 服务 |
| `com.apple.driver.AppleUSBDeviceMux` | `0xfffffff005887140`, 0x1957 | usbmux 的 USB 侧：在 bulk pipe 上实现 TCP，连本机 socket |
| `com.apple.driver.AppleUSBDeviceNCM` | `0xfffffff005874340`, 0x10d9 | NCM 网卡功能 |
| `com.apple.driver.AppleUSBDeviceAudioController` | `0xfffffff00560e0c0`, 0x914 | USB 音频功能 |
| `com.apple.iokit.IOAccessoryPortUSB` | `0xfffffff0058a7880`, 0xc08 | `IapOverUsbHid` 功能（我们不需要） |

**主机模式这一族**（我们不需要，但顺带记下）：`IOUSBHostFamily`、`AppleUSBXHCI`、`AppleUSBEHCI`、
`AppleUSBOHCI`、`AppleUSBHub`、`AppleUSBHostT8011`、`AppleUSBHostCompositeDevice`、`IOUSBHostHIDDevice`…

**注意**：`[kernel.prelink].roots`（`UniXNU-pipa/board.toml:78-110`）里**一个 USB kext 都没有**，
而 `kits/NativePrelink/native_prelink.py:130-159` 的 `plan()` 是"roots + OSBundleLibraries 传递闭包"。
也就是说**现在构建出来的内核里根本没有这些 kext**，`roots` 必须加：

```
com.apple.driver.AppleUSBXDCI          → IOUSBDeviceFamily, AppleUSBCommon
com.apple.driver.AppleUSBDeviceMux     → IOUSBDeviceFamily
```
（`kpi.*` 是 `OSKernelResource`，会被跳过；三者可执行段合计约 60 KiB，可忽略。**不需要**
`AppleEmbeddedUSB` / `AppleT8011USBXDCI` / `IOPortFamily` / `AppleARMPlatform`——见 §2.3 的方案 A。）
—— 这是 board.toml 的改动，本次按要求**没有动**。

### 1.5 关键发现：`AppleUSBXDCI` 就是 DWC3 设备控制器驱动

从 `AppleUSBXDCI` 的 `__cstring`（vaddr 基址 `0xfffffff00556df18`）里能直接读出它的硬件模型：

- `0xfffffff00556ed33`：`epIndex %d depcmd 0x%x param0 0x%x param1 0x%x param2 0x%x` —— DEPCMD +
  三个参数寄存器，是 DWC3 端点命令的写法。
- `0xfffffff005570c9a`：`Setting DCTL.DEVCTRLHLT timed out DSTS: 0x%08x` —— DWC3 的 `DCTL`
  （device control）与 `DSTS`（device status），且 `DEVCTRLHLT` 就是 DWC3 的位名。
- `0xfffffff005570b80` / `0xfffffff005570bea`：`Core version register value: 0x%08x` /
  `USB Core GUID: 0x%08x` —— DWC3 的 `GSNPSID` 与 `GUID`。
- 端点命令枚举名：`kEpCommandStartXfer`、`kEpCommandEndXfer`、`kEpCommandUpdateXfer`、
  `kEpCommandStartNewConfig`、`kEpCommandSetConfig`、`kEpCommandSetXferResConfig`。
- TRB：`AppleUSBXDCITRBBlock`、`AppleUSBXDCITRBPool`、`AppleUSBXDCIIsocTRBBlock`、
  `prepareControlTRBs`、`prepareNonIsocTRBs`、`prepareIsocFrame`。
- 链路状态字符串：`SS.Disabled / Rx.Detect / SS.Inactive / Polling / Recovery / Hot Reset / Compliance /
  Loopback`，以及 `U1SEL / U1PEL / U2SEL / U2PEL`（DCTL 的 LPM 字段）、`XferNotReady`、`Missed Isoc`、
  `Event buffer overflow` —— 全是 DWC3 的 DSTS 链路状态与事件类型。
- 每个"interrupter"一个事件环：`AppleUSBXDCIEventHandler::initWithParameters`、
  `failed to allocate event ring buffer for interrupt %d`、`initRegisters`、`getCurrentEvent`、`popEvent`、
  `flushEvents`（`0xfffffff0055736b2`-`0xfffffff005573855`）。

所以：**Apple 的 XDCI = Synopsys DWC3 的一个 Apple 化版本**。这是整件事能不能省力的分水岭。

### 1.6 真实 iPad7,1 的 USB 设备树（`out/ipsw/21H461/DeviceTree.j120ap.im4p`）

ADT 是 LZFSE 压缩的 IM4P；解出来（`scripts/unixnu/ipsw.py:183` 的 `im4p_payload()` +
`/usr/bin/compression_tool -decode -a lzfse`）后按 Apple 的 ADT 格式能读。摘录 `/arm-io/usb-complex`：

```
usb-complex                       compatible = "usb-complex,t8011"
  reg      = 0xc00000000, 0x10000      ranges = <0 0xc00000000 0x600000000>
  clock-mask = <8 11 10 91 13 12>      usb_widget = <...>
  usb-device                      compatible = "usb-device,t8011"      ← 设备模式
    reg                    = 0xc0300000, 0x1000
    vendor-id              = 0x05ac
    product-id             = 0x12ab          ← iPad 的 USB PID
    bcd-usb-version        = 0x0200
    product-version        = 0x0701
    vendor-string          = "Apple Inc."
    product-string         = "iPad"
    configuration-string   = "stdMuxPTPEthValIDA"
    eps-dir-bitmap         = 0x264           ← IN 端点号 2,5,6,9
    device_type            = "usb-device"
    interrupts             = <0x102 0x103 0x104 0x105>   ← 4 个中断源 = 4 个 interrupter
    interrupt-parent       = <&pdc>
    ahb-burst = 0xe   clock-mask = 0x10   tunable_setting = <...>
    function-usb_500_100   = <0xc7 ...>
    host-mac-address / device-mac-address = "macaddr/wifiaddr,syscfg/WMac/6,zeroes/6" …
  usb-xhci                        compatible = "usb-xhci,t8011"        ← 主机模式（我们不用）
    port-count = 2, usb3host_setting = <...>, 两个 xhci-port 子节点
  usb-charger                     charger,d2355
```

这些属性名和 `IOUSBDeviceFamily` 里的字符串一一对应（见 §2.1），所以**照抄这套键名就是"像 iPad"的最短
路径**。

### 1.7 用户态：`lockdownd` / `afcd` 已经在镜像里，`usbmuxd` 不在

挂载 `out/ipsw/21H461/044-76040-038.dmg`（APFS，只读）后：

- `/usr/libexec/lockdownd`（1.82 MB）、`/usr/libexec/afcd`（75 KB）都在。
- **`/usr/libexec/usbmuxd` 不存在**，`System/Library/LaunchDaemons` 里也没有 `com.apple.usbmuxd`。iOS 13
  以后 usbmux 的**设备侧**由 `lockdownd` 自己承担（它的字符串里有 `Misc usbmuxd event:`、
  `AppleUSBMux`、`IOUSBDeviceInterface`、`IOUSBDeviceControllerForceOffBus`、"Failed to create IOKit
  matching dictionary for 'IOUSBDeviceInterface'"）。**主机侧的 `usbmuxd` 是 macOS 自己的事，不用我们写。**
- `com.apple.mobile.lockdown.plist`：`ProgramArguments = /usr/libexec/lockdownd`、`RunAtLoad = true`、
  socket `/var/run/lockdown.sock` + TCP **62078**，并注册了 `com.apple.iokit.matching` 启动事件：
  `IOProviderClass = IOPortTransportState` / `IOPropertyMatch = {Active:true, TransportType:2|3, DataRole:1}`、
  以及 `IOProviderClass = IOResources` / `AppleUSBCableType = USBHost`。
  → 好消息：`RunAtLoad` 为真，**lockdownd 一开机就会起**，不依赖上面那些事件。
- `com.apple.afcd.plist`：`ProgramArguments = /usr/libexec/afcd`，Mach service `com.apple.afcd`，
  lockdownd 通过 `com.apple.afc` / `com.apple.afc.unrestricted` 这两个 StartService 名字把它拉起来。
- `/System/Library/AppleUSBDevice/USBDeviceConfiguration.plist`（5.3 KB）**在**，里面确实有
  `stdMuxPTPEthValIDA` 这个 key，其 `DefaultConfiguration = true` 的那一项是
  **`Interfaces = ["PTP", "AppleUSBMux"]`**；另外 `stdMuxIDA` 的第 0 项是 **只有 `AppleUSBMux`** 且
  `DefaultConfiguration = true`。这个 plist 就是 `configuration-string` 的解码表。
- `/System/Library/ApplePTP/PTPDeviceInfoDataset.plist` 在，但**内核里没有任何 kext 注册名为 `PTP` 的
  功能**（把整个 kernelcache 按 `\0PTP\0` 搜是 0 命中；`USBDeviceFunction` 的取值只有 Reserved、
  AppleUSBEthernet、USBAudioStreaming/Control、USBAudio2*、AppleUSBNCM*、AppleUSBMux、IapOverUsbHid、
  IDAMInterface）。见 U4。

---

## 2. iOS 侧的栈：谁绑谁

### 2.1 真实 iPad 上的 provider 链

```
/arm-io/usb-complex            AppleARMIODevice   ("usb-complex,t8011")
   └─ AppleT8011USBArbitrator  （AppleT8011USB.kext，IOProviderClass = AppleARMIODevice，
                                 也会去拿 otgphyctrl,t8011 的 AppleT8011USBPhy 服务）
        └─ AppleEmbeddedUSBNub（每个子节点一个，名字来自 compatible）
             └─ AppleT8011USBXDCI   IOProviderClass = AppleEmbeddedUSBNub,
                                    IONameMatch = "usb-device,t8011", IOProbeScore = 0x2711
                  （= AppleUSBXDCI 的子类；父类才是 DWC3 驱动）
        └─ AppleEmbeddedUSBNub（usb-xhci 子节点）
             └─ AppleT8011USB / AppleUSBHostT8011（主机模式，另一套）
AppleUSBXDCI : IOUSBDeviceController
   → 按配置描述符发布 IOUSBDeviceInterface 子节点（每个 interface 一个，
     带 USBDeviceFunction = <接口名>）
        → AppleUSBDeviceMux   IOPropertyMatch = {USBDeviceFunction: "AppleUSBMux"},
                              IOProviderClass = IOUSBDeviceInterface, IOResourceMatch = IOBSD
        → AppleUSBDeviceNCM   USBDeviceFunction = AppleUSBNCMControl/Data
        → AppleUSBDeviceAudioController, AppleUSBAudio…, IapOverUsbHid …
```

`AppleT8011USBXDCI` 自己只做三件事（从它的 `__cstring` 看得很清楚）：`start` 里
`could not find register map` → `physical address for registers - 0x%016llx`；`powerOn`/`powerOff` 里
`using USB2PHY` / `using USB3PHY` / `could not create USB2/USB3 PHY service client`；以及
`skip-usb-charging`、`function-usb_500_100`（限流）。**DWC3 的全部寄存器操作都在父类
`AppleUSBXDCI` 里。**

### 2.2 `IOUSBDeviceController`（父类）需要的 DT 属性

从 `IOUSBDeviceFamily` 的 `__cstring`（`prepareDefaults` 一段，按地址顺序）：

`vendor-id`/`vendorID`、`manufacturer-string`/`manufacturerString`、`product-id`/`productID`、
`product-string`/`productString`、`product-version`、`device-class`/`deviceClass`、
`device-subclass`、`device-protocol`、`bcd-usb-version`/`DefaultBcdUSBVersion`、`max-power`/`MaxPower`、
`attributes`/`Attributes`、`%s-configuration`、`IOUSBDeviceController-configuration`、
`configuration-string`、`DefaultDeviceDescription`、`soft-disconnect-interval`、`allow-subpage-mapping`、
`usb_enumeration_timeout`、`bus-number`、`local-mac-address`、`serialNumber`、
`IOPlatformSerialNumber`、`IOPlatformUUID`、`/chosen` 的 `unique-chip-id`。

调试/运行期还会用：`usb_dev_nmi`、`usb_dev_reset`、`usb_max_current`、`usb_compliance_mode`、
`MapperPageSize`（`did not find a mapper`）、`SetDeviceConfiguration`/`SetDeviceDescription`/
`GoOffAndOnBus`/`ForceOffBusEnable`（user client 命令）。

其中 `unique-chip-id` 我们已经在 ADT 里给了（`make-pipa-adt.py:138`，`ipad_identity` 分支），
`IOPlatformUUID`/序列号这条链是通的；`MapperPageSize`/`did not find a mapper` 说明**没有 IOMapper 时它
也能继续**（走物理地址），这点对 §3.2 的 SMMU 决策很关键。

### 2.3 四种接法，和"最省力"

| 方案 | 做法 | 新代码量 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| **A. 子类化 `AppleUSBXDCI`** | 自己造 provider（nub），personality 指向自己的类；补 `powerOn/powerOff`（GCC/GDSC/复位/PHY）、寄存器基址、中断桥接。上层全用 Apple 的 | 中（1 个驱动 + ABI 绑定 + 1 个 XNU personality patch） | 中：vtable/ABI 逆向；"register map"与"interrupter count"两个语义未知（U1/U2） | **推荐** |
| B. 子类化 `IOUSBDeviceController`，自己实现 EP0/TRB | 相当于自己重写 `AppleUSBXDCI` | 大 | 大：等于重写 DWC3 设备栈 + 描述符机 | 不推荐 |
| C. 完全自己写一个 gadget，直接发 `IOUSBDeviceInterface` nub，或者自己在 bulk pipe 上跑 usbmux 协议转发到本机 `127.0.0.1:62078` | 不碰 Apple 的栈 | 中（但要自己实现 usbmux 帧/TCP 语义） | 中：`AppleUSBDeviceMux` 就是"内核里的小 TCP"，自己写要复刻它的语义（session、RST、ack、窗口） | 备选，见 §6 |
| D. 复用 Apple 的 `AppleT8011USBXDCI` 原样加载 | 只要 DT 里出现 `usb-device,t8011` 它就会绑（score 0x2711） | 0 | 必崩：它的 `powerOn` 要 Apple 的 USB2/USB3 PHY 服务（`AppleUSBPhyServiceClient`），还要 `AppleEmbeddedUSBNub`；我们两样都没有 | 不可行 |

**方案 A 的具体形状**（推荐；不写代码，只列接口边界）：

1. ADT 里给 `/arm-io/usb-complex` + `/arm-io/usb-complex/usb-device` 两个节点（见 §3.3）。
2. 一个内核里的小 provider（可以是 `ArmDTPlatform` 造出来的 `IOPlatformDevice` 直接用，也可以让我们的
   USB 驱动自己在 `start()` 里 `OSMetaClass::allocClassWithName("AppleEmbeddedUSBNub")` 造一个 nub 再
   `attach()`）——**parent 只要能被 `IOUSBDeviceController::start()` 当成"有 DT 属性的 IOService"即可**，
   不必须是 `AppleARMIODevice`（那是 `AppleEmbeddedUSBArbitrator` 的要求，我们不装它）。
   **这一点未验证**，属 U2。
3. `NativeUsbXdci : AppleUSBXDCI`，覆写 `powerOn`/`powerOff`，并根据 U1/U2 的结论决定是否需要覆写
   `start`（寄存器窗、interrupter 数）。
4. 中断：用 `drivers/ArmCpu/ArmCpu.cpp:877` 的
   `armcpu_spi_attach(intid, edge, handler, ctx, &readback, &router)`（`package/GpuProbe.cpp:896` 用过，
   `32 + 300` 取 GPU 的 INTID）拿 SPI 133 → INTID 165，再把回调转给 Apple 的
   `AppleUSBXDCIEventHandler`（它自己要 `IOInterruptEventSource`，我们的 provider 没有中断控制器，
   所以这一段必须自己桥，见 U6）。
5. XNU 侧加一条 personality：`patches/xnu/native-usb-personality.patch` 的写法照
   `patches/xnu/native-aes-personality.patch`（往 `iokit/KernelConfigTables.cpp` 的字符串表里追加
   `IOClass` / `IOProviderClass` / `IONameMatch` / `IOProbeScore`）。
6. ABI：照 `scripts/build-native-virtio-bindings.py` 的路子（它给 `IOBlockStorageDevice` 做了 190 个
   vtable 槽的审计），先产出一份 `OriginalUSBABI.h` + vtable 清单。**区别**：storage 那套能拿到
   `build/target-kexts/<bundle>/segments.json`，而 kext 符号表在所有来源里都被剥掉了——所以 vtable
   必须从反汇编推（配方见 §7 的 U1）。

---

## 3. 硬件链路：从内核启动到 USB 枚举，逐项谁提供

### 3.1 逐项

| 环节 | 需要什么 | 谁来提供 | 现状 | ADT 要不要加 |
| --- | --- | --- | --- | --- |
| 时钟 | GCC 的 USB3 prim 6 路（core 0xb6 / iface 0x0f / bus_aggr 0x08 / utmi 0xb8 / sleep 0xbb / xo 0xc7），PLL 与 CBCR 使能 | 新驱动直接写 GCC（`0x100000`，`pipa.dts:3737`）；写法照 `NativeUfs` 的 GCC 直编（`NativeUfs.cpp:190-203` 用 `mapPhys` + `read32`，`0x80000000` 是 PWR_ON、CBCR 的 CLK_OFF） | 无 | 不用（地址见 DTS） |
| USB3 GDSC | `usb30_prim_gdsc`（GCC+0x10f004）PWR_ON 等待 | 同上 | 无 | 不用 |
| 复位 | core_reset（gcc reset 0x22）、hsphy 0x1b、ssphy 0x24/0x26 | 同上 | 无 | 不用 |
| HS PHY | `qcom,usb-hsphy-snps-femto`：`0x88e3000` 的 0x110 字节 + `0x88e2000` 的 EUD 位；`qcom,param-override-seq` / `global-param-override-seq` 已给全 | 新驱动（照 DTS 序列写寄存器），或先"adopt" ABL 留下的状态 | 无 | 不用 |
| SS PHY（可选） | QMP combo PHY `0x88e8000` 的 `qmp-phy-init-seq`（~200 组） + `qmp-phy-reg-offset`；PIPE 时钟 | 新驱动；**建议第一阶段跳过**（HS 480 Mbps 就够 lockdown + AFC） | 无 | 不用 |
| DWC3 核心 | GCTL/DCFG/DCTL/DSTS/GSNPSID、事件环、TRB、DEPCMD | `AppleUSBXDCI`（现成代码，走方案 A） | kext 在 kernelcache 里，但**没进我们的 prelink**（§1.4） | 需要 `roots` |
| 中断 | 核心 SPI 133 → INTID 165；BAM SPI 132（不用）；4 个 PDC 中断（我们拿不到） | `ArmCpu::armcpu_spi_attach` | 已具备（GPU 用过 SPI 300） | 不用 |
| IOMMU | `apps-smmu@15000000`（phandle 0x47），USB0 的 SID 0；`qcom,iommu-dma-addr-pool = 0x90000000+0x60000000` | `kits/ArmSmmu4K`（GPU/UFS 已在用）+ 新的 USB context 配置 | 有 kit，没有 USB context | 不用（地址见 DTS） |
| DMA 内存 | dwc3 的 TRB/数据缓冲的 DMA 地址 | **见 §3.2：这是最硬的约束** | **未解决** | 不用 |
| 互联（NoC）带宽投票 | `mas-xm-usb3-0`（cell-id 0x3d） | 第一阶段可以先不投（HS 只有 480 Mbps，时钟默认够） | 无 | 不用 |
| 线缆/VBUS | `extcon`（PM8150B）、PD | **不需要**：SS PHY 的 `qcom,vbus-valid-override`；HS 侧 `usb2_vbus_det` 只影响检测语义 | — | 不用 |
| 角色（drd） | 强制到 device：DWC3 的 `GCTL.PRTCAPDIR`/`GUSB2PHYCFG` 或 `DCFG` 覆盖 | 新驱动（`dr_mode` 在 DT 里是 `drd`） | 无 | 不用 |
| 设备描述符 | VID/PID/字符串/配置 | `IOUSBDeviceController::prepareDefaults` 读 DT 属性 | 现成 | **要加**（§3.3） |

### 3.2 DMA 地址：为什么 SMMU 不是可选项（重点）

- XNU 在这台机器上管理的物理内存从 **`0x10007c000`** 起，**4 GiB 以下没有可用空间**（见
  `HANDOFF-pipa.md` §10.13 已确认的事实；`board.toml:278` 的 `owned_memory_size = 0x170000000`）。
- DWC3 的 TRB 里的缓冲地址宽度取决于核心配置（32 位或 64 位），SM8250 这颗未知（U7）。如果只有 32 位，
  所有 TRB/DMA 缓冲都必须落在 4 GiB 以下 —— 而那里没有内存。
- 高通自己的解法就是 IOMMU：`qcom,iommu-dma-addr-pool = <0x90000000 0x60000000>` 把 **IOVA 池放在
  0x90000000..0xF0000000（4 GiB 以下）**，物理页随便在哪。这也解释了为什么 `iommus` 直接写在控制器
  节点上、且是 `"atomic"` 映射。
- 所以：**USB context 要做成 translation（不是 bypass）**，用 `kits/ArmSmmu4K` 建表，把
  `0x90000000` 起的 IOVA 映射到内核实际分配的页；`AppleUSBXDCI` 走 `IODMACommand` 拿到的就是 IOVA。
  注意 `HANDOFF-pipa.md` §10.13 的教训：这一族 SMMU 一旦有漏映射会**挂住整机**，所以映射要覆盖
  "所有可能被访问的地址"，并且要加 `continue_after_fault`（kit 里有）。
- 备选：如果核心支持 64 位 TRB 地址，SMMU 可以先 bypass，省掉一大块工作（U7 决定）。

### 3.3 ADT 要加什么（`scripts/make-pipa-adt.py`，本次**没有改**）

现状：`arm-io` 的 `ranges = <0 0x17000000 0x08000000>`（`make-pipa-adt.py:193`），**覆盖不到
0xa600000**；`ufshc` 节点因此不带 `reg`（`:199-201`），驱动靠硬编码物理地址。

USB 这条路不行：`AppleUSBXDCI` 要通过 provider 的 `reg` 找"register map"，`AppleT8011USBXDCI` 还会把
它打印成 `physical address for registers`。建议：

```
arm-io
  usb-complex                      ← 新节点
    compatible      = "usb-complex,pipa"          # 不要用 t8011，避免 Apple 的仲裁器来抢
    device_type     = "usb-complex"
    #address-cells  = 2   #size-cells = 2
    ranges          = <0 0 0xa600000 0 0x200000>  # 子节点偏移 0 → 0xa600000
    usb-device                     ← 设备模式
      compatible    = "usb-device,pipa"           # 我们的 driver 用 IONameMatch 抓它
      device_type   = "usb-device"
      reg           = <0 0 0 0xd93c>              # → 0xa600000，与 dwc3@a600000 同
      vendor-id     = <0x05ac>
      product-id    = <0x12ab>
      bcd-usb-version = <0x0200>
      product-version = <0x0701>
      vendor-string = "Apple Inc."
      product-string= "iPad"
      configuration-string = "stdMuxIDA"          # 见 §4.2，比真机的 stdMuxPTPEthValIDA 稳
      eps-dir-bitmap= <0x264>
      interrupts    = <0 0x85 4>                  # SPI 133，电平
      ahb-burst     = <0xe>
      # host-mac-address / device-mac-address：U9（有值更像真机；但它是节点路径引用，写法要确认）
  intc（现有）
```

要点：
- `usb-complex` 自带 `ranges` 是被允许的（真机的 `usb-complex` 也有 `ranges`，§1.6），但**我们的
  `ArmDTPlatform` 是否按多级 `ranges` 解析子节点 `reg`，需要先确认**（`docs/porting/device-tree.md`、
  `ArmDTPlatform.cpp:79-81` 只说"通过 arm-io 解析 `reg`/`ranges`"）。若只支持一级，退而求其次：
  `usb-device` 的 `reg` 直接写成 `0xa600000` 并让 driver 自己按物理地址用——**但那样 Apple 的
  "register map" 又拿不到**。这是 U2 的一部分。
- 不要给 `usb-xhci`、`usb-charger`、`hsphy`/`ssphy` 节点：我们不用 Apple 的 PHY 服务和主机栈。
- `interrupts` 的个数就是 interrupter 数（真机 4 个）。我们只有 1 个核心中断（U1）。

---

## 4. 让 macOS 认出"这是 iPad"

### 4.1 必要条件（缺一不可）

1. **USB 枚举本身**：VID `0x05ac`、PID `0x12ab`、`bcdUSB 0x0200`、字符串 `Apple Inc.` / `iPad`。
   这就是真机 `usb-device` 节点里那几个属性（§1.6）——`IOUSBDeviceController` 直接把 DT 属性搬进描述符。
2. **一个 `AppleUSBMux` 功能**：macOS 的 `usbmuxd` 找到 Apple 设备后，会用 usbmux 协议在 bulk 口上
   建一条到设备 `62078` 的 TCP 会话；设备侧必须有 `AppleUSBDeviceMux` 在跑（它自己实现了内核小 TCP）。
   没有这一层，macOS 只会在 System Information 里看到一个 USB 设备，**Finder 不会出现设备，
   `ideviceinfo` 直接报 `No device found`**。
3. **lockdown 握手（必须）**：`lockdownd` 在设备侧监听 `/var/run/lockdown.sock` 与 `62078`
   （`com.apple.mobile.lockdown.plist`）。主机第一次连上会走 `QueryType` → 拿到设备身份
   （`ProductType`/`UniqueDeviceID` …）→ 然后要 **Pair**：设备弹出"要信任这台电脑吗？"，
   用户在设备上点"信任"后，设备的
   `/private/var/root/Library/Lockdown/pair_records/<HostID>.plist` 存下主机的根证书。
4. **配对之后** 才允许 `StartService`：`com.apple.afc` / `com.apple.afc.unrestricted`（→ `afcd`）、
   `com.apple.mobile.installation_proxy`、`com.apple.crashreportmover` 等。Finder 的"文件"页签走 AFC。

所以回答"lockdown pairing 是否必须"：**必须**。不配对时 macOS 看到的是"未信任的设备"，
`ideviceinfo` 只能拿到 `UniqueDeviceID` 之类的少量键，AFC 一定失败。

### 4.2 为什么建议 `configuration-string = "stdMuxIDA"`

真机用 `stdMuxPTPEthValIDA`，它的 `DefaultConfiguration` 是 `["PTP", "AppleUSBMux"]`
（§1.7）。问题：**这个内核里没有任何 kext 注册名为 `PTP` 的功能**（§1.7 的搜索结果为 0），而
`IOUSBDeviceController` 的日志明确说 `cable connected, but not all interface drivers have registered yet`
/ `waiting for these interface driver(s) to register:` —— 配置只有在**所有** interface 都有驱动注册后
才会激活。所以要么 PTP 缺失会被容忍（U4），要么我们永远停在"等驱动"。

`stdMuxIDA` 的第 0 项是 `Interfaces = ["AppleUSBMux"]` 且 `DefaultConfiguration = true`：
只有一个 interface，只需要 `AppleUSBDeviceMux` 一个功能驱动，**这是把变量降到最少的做法**。
（代价：主机侧 Image Capture / 照片导入看不到 PTP，Finder 的"文件"页签仍可用——Finder 用的是 AFC。）

如果坚持要跟真机一致，就先解 U4；确认 PTP 必须有人注册时，再把 `configuration-string` 换回
`stdMuxPTPEthValIDA` 并加一个"空 PTP 功能"（只为了满足注册检查）。

### 4.3 用户态现在能不能跑起来

- `lockdownd`：`RunAtLoad`，开机就起，不依赖 §1.7 里那些 `IOPortTransportState` 事件（那些只是
  重启用）。补充一个实测事实：`IOPortTransportState` 这个类在我们的 prelink 里**没有任何发布者**
  （`com.apple.iokit.IOPortFamily` 只有 0x2e0 字节、是个壳），所以那些 LaunchEvent 永远不会触发 ——
  但 `RunAtLoad` 已经把 lockdownd 拉起来了，它真正要的是
  `IOServiceGetMatchingServices("IOUSBDeviceInterface")`（这个字符串就在 lockdownd 里）；在匹配成功
  之前它就是个只监听本地 socket 的进程 —— 安全，不会 panic。这条是 §1.7 之外唯一需要盯的用户态依赖。
- `afcd`：由 lockdownd 按需拉起，无需预启动。
- **没有 `usbmuxd`**（设备侧），这是 iOS 13+ 的正常形态。
- 配对自动化（设备无人值守时有用的两条路）：
  1. 用现成的触摸注入（`HANDOFF-pipa.md` §10.9 的脚本化触摸）在 SpringBoard 的"信任"弹窗上点一下；
  2. 或把主机的 pair record 预置进 Data 卷
     （`/private/var/root/Library/Lockdown/pair_records/<HostID>.plist` + `SystemConfiguration.plist`），
     设备 UDID 由 `unique-chip-id` 决定、是稳定的，所以预置是可行的。
- `sheng-disable-jobs`（`board.toml:196`）里禁的是 identityservicesd / locationd / CommCenter /
  bluetoothd / PerfPowerServices / iomfb_fdr_loader，**不影响 lockdown**。
- **但"在镜像里"不等于"在 pipa 上跑起来了"**：仓库里只有内核日志（`out/pipa/device-logs/*/dmesg`
  是 Android/NVT 触屏的日志，里面出现的 `nvt_lockdown_wq` 与本话题无关），没有任何 launchd 用户态
  日志。lockdownd 是否真的被 launchd 拉起、是否因为缺硬件反复重启，要等 M2 之后用
  `sheng-*` 之外的用户态日志渠道确认（这也是 U4 之外的一个小未知）。

---

## 5. 分阶段里程碑与判据

下面每一阶段都给了"怎么算过"的客观判据。建议顺序严格照走：**每一阶段都必须是可回退的**（USB 一挂，
fastboot 就没了，见 §6 的自救条款）。

### M0 — 控制器活了（不要求枚举）

做：GCC 时钟 + `usb30_prim_gdsc` + core/PHY 复位释放 + HS PHY 初始化 + dwc3 核心 `GSNPSID`/`GCTL`/`DSTS`
回读 + 强制 device 模式。

判据（内核日志，屏上或 ramoops 都能看）：
- `GSNPSID` 读出**非 0 且非 0xffffffff**，并且 `GSNPSID >> 8` 的版本号与 0x5533/0x5531 一类相符；
- `GCC_USB30_PRIM_GDSCR` bit31（PWR_ON）= 1；
- `DSTS` 可读，`DCTL.DEVCTRLHLT` 能被置位并等到 DSTS 回应（`AppleUSBXDCI::stopDeviceController` 的那条
  日志）；
- HS PHY 的 PLL lock 位（femto PHY 的 `0x88e3000+0x??`）为 1（具体寄存器见 U10）。

**没过就不要往下走**——这一阶段没有任何总线风险（只要不 attach SMMU）。

### M1 — 主机侧看到设备（枚举）

做：EP0 + 标准请求（GET_DESCRIPTOR/ SET_ADDRESS / SET_CONFIGURATION）+ 描述符。

判据：
- 主机 `system_profiler SPUSBDataType` 或 Linux `lsusb -v` 出现
  `idVendor 0x05ac / idProduct 0x12ab`、`iManufacturer "Apple Inc."`、`iProduct "iPad"`、
  `bcdUSB 2.00`；
- 配置 1 里有一个 vendor-specific interface（mux）；
- **和真机 iPad7,1 的 `lsusb -v` 快照 diff 为空**（先在能连的真机上存一份基线）。

### M2 — Apple 的 mux 起来（主机能拿到 UDID，还不需要配对）

做：`AppleUSBDeviceMux` attach + bulk 端点 + `AppleUSBDeviceMux` 打印 session 建立。

判据：
- 设备端 IORegistry 里 `AppleUSBDeviceMux` 状态是 started，日志有
  `new session to port %d` / `matches existing session`；
- 主机 `usbmuxd`（`sudo /var/db/lockdown`…）日志出现一个 device attach，带着我们的 UDID；
- 主机 `idevice_id -l` 列出 UDID；设备端 `lockdownd` 有连接日志。

这一步就说明"macOS 认出了这是 Apple 设备"，也说明 usbmux 隧道通了。

### M3 — lockdown 配对 + 身份

做：配对（U9 的两条路选一条）。

判据：
- 设备 `/private/var/root/Library/Lockdown/pair_records/` 里出现一条记录；
- `idevicepair validate` 返回 SUCCESS；
- `ideviceinfo` 输出一堆键，且 `ideviceinfo -k ProductType` = `iPad7,1`、
  `-k ProductVersion` = `17.7.11`；
- Finder 侧栏出现设备。

### M4 — AFC 读文件

判据：
- `idevicecrashreport -l`、`ifuse --list`（或 `idevicedebug`）能列出目录；
- 能读到一个已知文件（例：`/DCIM` 或 `/PublicStaging`）。

`afcd` 的根目录由 lockdownd 的 service 名决定（`com.apple.afc` 受沙箱限制，`com.apple.afc.unrestricted`
是越狱/开发用的）——真机不配对时连 `com.apple.afc` 都拿不到，所以 M3 是硬前置。

---

## 6. 风险与工作量

### 6.1 按阶段估

| 阶段 | 内容 | 乐观 | 悲观 | 备注 |
| --- | --- | --- | --- | --- |
| M0a | GCC 时钟 + GDSC + 复位 | 1-2 天 | 3 天 | 有 `NativeUfs` 的直编写法可抄；DTS 给了时钟/复位 id |
| M0b | HS PHY 初始化 | 2-4 天 | 1 周 | DTS 有 `param-override-seq`，但 femto PHY 的 PLL 等待位要自己摸 |
| M0c | dwc3 核心初始化（adopt 或自己写） | 2-3 天 | 1 周 | `AppleUSBXDCI` 现成，但先要过 ABI |
| M0d | USB 的 SMMU context（translation + IOVA 池）**——M1 的前置** | 3-5 天 | 2 周 | `ArmSmmu4K` 有；风险是漏映射挂机（§10.13 的教训）；是否必需由 U7 决定 |
| M1 | ABI 绑定 + `AppleUSBXDCI` 子类 + EP0 | **1-2 周** | 4 周 | 主要是 U1/U2/U6 三个逆向项 |
| M2 | 配置描述符 + mux 起来 | 3-5 天 | 2 周 | U3/U4 决定要不要自己填 `DeviceDescription` |
| M3 | 配对 | 1-3 天 | 1 周 | 主要是"信任"弹窗的自动化 |
| M4 | AFC | 1-2 天 | 3 天 | 用户态都是现成的 |

**"只要枚举"（M0+M1，不含 mux）**：如果走方案 A，ABI 的成本跑不掉，约 **3-4 周**；如果放弃 Apple 的栈、
自己写一个最小 EP0 gadget（只回标准请求），**约 2 周**，但拿不到 M2/M3/M4。
**"要数据访问"（M0-M4）**：**4-6 周**（方案 A），或者 8-12 周（方案 C 自己写 usbmux 隧道）。

### 6.2 风险清单（按严重度）

1. **USB 一挂，fastboot 就没了。** 这条口是唯一的调试通道。现在的自救是
   `pipa-restart-after=240` / `pipa-restart-crashes=3`（`board.toml:225-226`，`package/BootRamoops.c`）
   和"panic 后 10 s 自动回 fastboot"。但**纯 hang（不 panic）不会被这些救到**：如果驱动在关中断的循环里
   转，定时器也不会跑。上机前必须：① 确认平板能长按电源断电；② 每一次实验都在 payload 层留一个
   "N 秒后强制 reboot 到 fastboot"的保险；③ 不要在同一次 boot 里同时改 SMMU 和 PHY。
2. **SMMU 漏映射会挂住整机**（§10.13 已经付过一次代价）。USB 的流量比 GPU 扫描输出更零散，更容易踩。
   顺序取决于 U7：若核心支持 64 位 TRB 地址，EP0 阶段可以先 bypass，稳定后再切 translation（切之前把
   "可能被访问的地址"全覆盖）；若只有 32 位，translation 从第一条 TRB 就必须上——那就先在一台能随时断电的
   状态下把映射表做全，再上机。
3. **U1（interrupter 数）** 是方案 A 的 go/no-go：真机 4 个中断源，我们只有 1 个核心 SPI。
   如果 `AppleUSBXDCI` 硬要求 4，方案 A 需要额外工作（甚至回到方案 C）。
4. **U2（register map 的来历）**：`could not find register map` + `physical address for registers` 说明
   它从 provider 拿一个地址。如果它期望的是 Apple 那种多段 `reg`/子节点结构，我们的单段 `reg` 可能不认。
5. **ABI 逆向没有符号表。** kext 的可执行文件在所有来源（kernelcache、OS 卷）里都被剥了符号，
   vtable 只能从反汇编推。这是"几天"级的活，但必须先做（见 §7）。
6. **U4（PTP 功能缺失）**：可能导致配置永远不激活。缓解：先用 `stdMuxIDA`。
7. **U5（ABL 留下的状态）**：adopt 能省事，但如果 ABL 在跳转前做了 soft disconnect 且没清干净的
   事件环，第一条 `DEPCMD` 就会超时。第一次要留"完全复位"的开关。
8. **`AppleUSBDeviceMux` 的内核 TCP** 依赖 BSD socket 与 mbuf；这条路径在 tb128fu/sheng 上没被走过，
   属于"第一次"。它一旦 panic 也是整机死。

### 6.3 与被否掉的方案划清界限

- 不要试图让 `AppleEmbeddedUSBArbitrator` / `AppleT8011USBXDCI` 原样跑起来：它们要 Apple 的
  `AppleARMIODevice`、`AppleUSBPhyService`、`otgphyctrl` 服务，我们一个都没有，而 `AppleT8011USBXDCI`
  的 `powerOn` 会去连 PHY 服务（失败即 `powerOn` 失败，USB 静默不动）。
- 不要用 `usb_nop_phy` 糊过去：那只是给别的驱动的占位符，没有任何 PHY 功能。

---

## 7. 未知项与查证办法

> 这些是按"最省力到最费劲"排序的。U1/U2 建议在写任何驱动之前先解决（各半天到一天）。

**U1. `AppleUSBXDCI` 接受的 interrupter 数是多少？**
真机 DT 给了 4 个中断（`interrupts = <0x102 0x103 0x104 0x105>`），SM8250 只有 1 个核心 SPI。
`AppleUSBXDCI` 里 `_interrupterCount` 是**继承来的成员**（`[x19+0x2410]`，本 kext 内只读不写 → 由
`IOUSBDeviceController` 从 provider 填）。查法：反汇编 `AppleUSBXDCI` 里那个设置
`CurrentState`/`ScatterGatherSupport` 属性、紧接着检查 `_interrupterCount` 的函数（函数起点
`0xfffffff006542f30`，八成就是 `start`；本次已定位到检查发生在
`0xfffffff006542fec`-`0xfffffff006543310` 一带），看它和哪个常数比。

**U2. "register map" 从哪来？`reg` 是单段够不够？**
`AppleT8011USBXDCI::start` 的 `could not find register map` → `physical address for registers - 0x%016llx`
说明它在 provider 上找"一张表"。候选：(a) provider 的 `reg` 属性；(b) provider 的
`IODeviceMemory`；(c) 某个命名子节点。查法：反汇编 `AppleT8011USBXDCI`（`0xfffffff005576140`，
只有 0x1108 字节，很快就看完），看它 `find` 的函数返回什么、又从哪个成员取地址。

**U3. 配置描述符的字典到底从哪读？**
`IOUSBDeviceFamily` 的字符串序列是 `%s-configuration` → `IOUSBDeviceController-configuration` →
`configuration-string` → `DefaultDeviceDescription`。三种可能：DT 里有个 `<model>-configuration`
属性（plist data）；DT 里有个 `IOUSBDeviceController-configuration`；或者读
`/System/Library/AppleUSBDevice/USBDeviceConfiguration.plist`（这个文件确实在，而且含 `std*` 全部 key）。
查法：在 `IOUSBDeviceFamily` 里搜 `AppleUSBDevice` / `.plist` 的字面量（本次 dump 里没看到，说明更可能
是 DT 属性或 `DefaultDeviceDescription` 内建表），再反汇编 `prepareDefaults`。

**U4. 没有 `PTP` 功能驱动的 interface 会不会挡住配置激活？**
`waiting for these interface driver(s) to register:` 是硬等待还是只等"有驱动的"。查法：反汇编
`IOUSBDeviceController` 里那条日志之前的分支；或者**直接实验**：先用 `stdMuxIDA`（只有 Mux）过 M2，
再换 `stdMuxPTPEthValIDA` 看是否卡住。

**U5. ABL 交棒时 dwc3 处于什么状态？**
上机后用只读探针（不做任何写）读 `GCTL`/`DCTL`/`DSTS`/`GUSB2PHYCFG`/`GCC_USB30_PRIM_*`/HS PHY 的
几个寄存器，和"ABL 刚进 fastboot 时"对比。判据：`DSTS.DEVSPD`/`DCTL.RUN_STOP` 是否还活着。

**U6. `AppleUSBXDCIEventHandler` 的中断源怎么接？**
它自己 `failed to allocate interrupt source`，说明它要一个 XNU 的 interrupt source（
`IOInterruptEventSource`），而我们的 provider 没有中断控制器。查法：看它 `initWithParameters` 调的是
`IOService::getInterruptEventSource` 还是 provider 的某个方法；如果绕不过去，就在我们的子类里把
`armcpu_spi_attach` 的回调直接转给 `interruptOccurredBusEvents`/`interruptOccurredIsocEvents` 一类
的方法（这些方法名在 cstring 里，`0xfffffff005571f96`、`0xfffffff00557219f`、`0xfffffff0055726ca`…，
可以从 vtable 里定位）。

**U7. dwc3 的 TRB 地址是 32 位还是 64 位？**
查法：读 `GHWPARAMS*`（SM8250 上 `0xa600000+0xc100` 附近的参数块）里的地址宽度位；或者看
`AppleUSBXDCI` 的 TRB 结构体大小（`AppleUSBXDCITRBBlock` 的 `initWithTRBCount` 里乘的常数）。
落到代码上就是"要不要 SMMU"。

**U8. pipa 的 `Binaries/` 里有没有预编译的 `UsbfnDwc3Dxe`？**
`ls /Users/xvan/agents-home/uefi-pipa/Binaries/` 里找 pipa 对应的目录（kona/SM8250 系列），看
`QcomPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.*` 是否存在。有的话，它是唯一"高通自己的 dwc3 初始化序列"
的参考（二进制），可以拿来做 M0 的寄存器序列对照，甚至可以考虑让 UEFI 先把 USB 拉起来、XNU 只做 adopt。

**U9. `host-mac-address` / `device-mac-address` 要不要给？**
真机是 `"macaddr/wifiaddr,syscfg/WMac/6,zeroes/6"` 这种"路径引用"，标志位 0x80000000。我们的 ADT 里
没有 `syscfg/WMac` 这种节点。缺它大概只影响 NCM（我们不装），但 `IOUSBDeviceController` 读不到时
是否会报错未知。先不给，M2 不通再补。

**U10. HS PHY 的 PLL lock / 校准完成位是哪个？**
femto PHY 的 `0x88e3000` 区里，Linux 的 `phy-msm-snps-hs` 用 `USB2_PHY_PLL`/`USB2_PHY_PARAM_OVERRIDE`
等；DTS 只给了 override 序列，没给 lock 位。查法：对照 Linux `drivers/phy/qualcomm/phy-qcom-usb-hs*`
的寄存器定义，或者读 ABL 留下的 PHY 寄存器做"已知好状态"基线。

---

## 8. 本次没做、也不该由本文档决定的事

- **没写任何驱动代码**，没动 `board.toml`（但 §1.4 的 `roots` 和 §3.3 的 ADT 节点是必须的前提，
  留给实现阶段改）。
- 没上机，所以所有"现在是什么状态"都停在"代码/二进制/镜像层面的证据"。
- 没决定 M0 是"完全自己初始化"还是"adopt ABL 的状态"——这取决于 U5，需要一次只读探针。
- 没决定 `configuration-string` 最终用哪个——取决于 U4。

---

## 附：本次用到的一次性工具（没有留在仓库里）

1. **从 kernelcache 里取某个 kext 的 load 地址与大小**：`__PRELINK_INFO` 在文件偏移 `0x292c000`
   （大小 `0x318000`），是一个 `<dict><key>_PrelinkInfoDictionary</key><array>` 的 XML；按
   `<dict><key>CFBundleName</key>` 切块，每条里有 `_PrelinkExecutableLoadAddr`、
   `_PrelinkExecutableSize`、`_PrelinkBundlePath`、`IOKitPersonalities`。
2. **vaddr → 文件偏移**（这个 kernelcache 的段表）：
   `__TEXT 0xfffffff007004000/0x0`、`__TEXT_EXEC 0xfffffff0071c0000/0x1bc000`、
   `__PRELINK_TEXT 0xfffffff00527c000/0xa50000`、`__PLK_TEXT_EXEC 0xfffffff005920000/0x10f4000`、
   `__PLK_DATA_CONST 0xfffffff006ca0000/0x2474000`、`__PRELINK_DATA 0xfffffff007adc000/0x27d8000`、
   `__PRELINK_INFO 0xfffffff007c30000/0x292c000`。
3. **反汇编单个 kext**（kext 自带的 Mach-O 头在 `__PRELINK_TEXT` 里，但段 fileoffset 指向原文件，
   不能直接 `otool`）：按 kext 头里的段表重建一个"自包含"的 Mach-O（每段
   `fileoff = 新文件里的位置`，`vmaddr` 保持原值），然后
   `llvm-objdump -d --section=__text <重建文件>`。本次就是这么确认 `AppleUSBXDCI` 是 DWC3 的。
4. **读真实 iPad 的 ADT**：`scripts/unixnu/ipsw.py:183` 的 `im4p_payload()` 取 IM4P 的 payload →
   `/usr/bin/compression_tool -decode -a lzfse` → 按 `[u32 nprops][u32 nchildren]`
   递归解析；**属性长度字段的高位是标志位（`0x80000000` = 值是一个节点路径引用），解析时要
   `& 0x7fffffff`**。
