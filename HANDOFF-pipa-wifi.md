# 交接：pipa 的 WiFi（QCA6390 on PCIe0）前期调研

> 只做调研：**没有写驱动代码，没有改 `board.toml`，没有上机**（设备当前不可用）。
> 写于 2026-10-25。所有硬件数字都标了出处（`pipa.dts` 行号 / 本机或上游源码路径 / URL）；
> 标"待确认"的是推断，验证方法写在 §6。
>
> 设备树来源：`/Users/xvan/agents-home/uefi-pipa/Resources/DTBs/pipa.dts`
> （下文写作 `dts:NNNN`，行号已逐条核对过）。

## 0. 一句话结论

1. 硬件链路是 **QCA6390 挂在 PCIe root complex 0** 上，但本仓库**一行 PCIe 主机控制器代码都没有**，
   ADT 里也只有 `intc` 和 `ufshc` 两个节点。所以第一步是从零写 DWC PCIe host + QMP PHY +
   GCC 时钟/复位/GDSC + PERST#，并在 `make-pipa-adt.py` 里补一个 `pcie`（和 `wlan`）节点。
2. 上机第一道门槛**不是 PCIe 本身，而是供电**：QCA6390 的 WLAN 四路（aon/dig 共用 `pm8150_s6`）和
   PCIe 三路全是 **RPMh 控制的 PM8150/PM8150A 稳压器**，
   而本仓库没有 RPMh 驱动（`NativeUfs` 明确把 RPMh 排除在外，见 `drivers/NativeUfs/GccUfs.h:30`）。
   从 fastboot 冷启动进 UEFI 时这些轨大概率是关的。这条要么绕（SPMI 直写 PMIC），要么补（RPMh RSC 写命令集），
   工作量从"几天"到"两周"不等，是整条链路里最不确定的一环。
3. **iPadOS 17.7.11 在这台机器上没有内核态 IO80211**。实测（§3.1）它的 WiFi 驱动是一个
   **DriverKit dext**（`com.apple.DriverKit-AppleBCMWLAN.dext`，在系统卷上），内核里只有 20 KB 的
   `AppleOLYHAL`。所以"把原生 IO80211 栈加进内核"这条路在这里不存在；要让系统 UI 显示 WiFi，
   只能自己实现 `IO80211.framework` 期望的 user client ABI（兄弟项目在 iOS 16 上已经这么做了，§3.3）。
4. 有可以直接借的先例：`tycykp/UniXNU-OnePlus-Ace5`（SM8650 + WCN7850，iOS 16）已经把
   PCIe / SMMU / MHI / QMI / WMI / 数据环 / WPA2/WPA3 / Apple80211 ABI 都写了一版，
   源码在它的 `ios16/wifi/` 下（约 330 KB，纯 C 头 + 一个 60 KB 的 C++ 实现）。
   pipa 的电源路径比它简单（没有 SM8650 那个 QMP/AOP 邮箱要开，见 §2.4 第 11 步），但 WMI/HTT 层要从 ath11k
   （不是 ath12k）重写。

---

## 1. 硬件链路：从零到"枚举成功"需要什么

### 1.1 拓扑与地址（全部来自 DTS，已核对行号）

| 项 | 值 | 出处 |
| --- | --- | --- |
| 端点挂在哪个 RC | `qcom,wlan-rc-num = <0>`，即 `qcom,pcie@1c00000` | dts:8221 |
| RC 寄存器块 | `parf 0x1c00000/0x3000`、`phy 0x1c06000/0x1000` | dts:10890-10891 |
| DWC core 寄存器（DBI） | `dm_core 0x60000000/0xf1d` | 同上 |
| ELBI | `0x60000f20/0xa8` | 同上 |
| iATU | `0x60001000/0x1000` | 同上 |
| **配置空间** | `conf 0x60100000/0x100000`，但**不是 ECAM**：厂商驱动把它当 4 KiB 窗口用，每次访问前把 **iATU region 0** 重配成 `(type=CFG0, 目标 BDF)`（bus 1 用 CFG0，其他 bus 用 CFG1） | dts:10890；`pci-msm.c:3062`（`msm_pcie_cfg_bdf`） |
| IO 窗口 | PCI `0x60200000` → CPU `0x60200000`，1 MiB | dts:10896 |
| MEM 窗口 | PCI `0x60300000` → CPU `0x60300000`，`0x3d00000`（61 MiB） | dts:10896 |
| 端点 | QCA6390，`17cb:1101`（Linux `ath11k/pci.c` 的 `QCA6390_DEVICE_ID`） | 上游源码，见 §7 |
| 端点 BAR0 | MHI 寄存器在 BAR0 低地址；更高偏移经 BAR0 的 remap window 访问（ath11k `pcic.c` 的 `window_read32/write32`） | 上游源码 |
| PHY 版本 | `qcom,pcie-phy-ver = 0x44e`；`phy-status` 在 `0x814` bit 6 | dts:10939-10941 |
| **PHY 初始化序列** | `qcom,phy-sequence` 已在 DTS 里，99 组 `(偏移,值,延时)`（297 个值） | dts:10943 |
| 参考时钟 | `qcom,use-19p2mhz-aux-clk`；PHY refgen 目标 100 MHz（mainline `sm8250.dtsi` 的 `assigned-clock-rates`） | dts:10932 |
| L0s/L1 | `qcom,no-l0s-supported`、L1.2 阈值 `0x02/0x46` | dts:10933-10935 |
| 端点 SMMU stream | RID 0 → SID `0x1c00`，RID `0x100` → SID `0x1c01`；`dma-coherent` | dts:10926-10928 |
| WL_EN | TLMM `gpio20`（`wlan-en-gpio`）；active 态 = 输出高、drive 16 mA、pull-up；sleep 态 = 输出低、2 mA、pull-down | dts:8215、13205-13231 |
| BT_EN | TLMM `gpio21` | dts:8216 |
| SW_CTRL | TLMM `gpio124`（芯片给 SoC 的状态线） | dts:8217 |
| PERST# | TLMM `gpio79`，默认态 2 mA、pull-down | dts:10904、13046-13059 |
| WAKE# | TLMM `gpio81`，pull-up | dts:10905、13076-13089 |
| CLKREQ# | TLMM `gpio80`，function `pci_e0` | dts:13061-13074（组 `pcie0` 起于 dts:13044） |

注意：`pcie@1c00000` 的 `interrupt-parent = <0x166>`（dts:10897）就是这个节点自己的 phandle，
这是**反编译 DTS 的伪影**，没有意义；真正的映射看 `interrupt-map` 和 mainline 的 `sm8250.dtsi`（§1.3）。

另外两条实现细节（都出自厂商 `pci-msm.c`，M3 会直接踩到）：

- **配置空间**：RC 自己（bus 0）的配置走 `dm_core`（DBI）；端点在 **bus 1** 上，
  访问前要把 iATU region 0 配成 `CFG0` + 目标 BDF（`msm_pcie_cfg_bdf`，`pci-msm.c:3062-3081`），
  然后在 `conf` 窗口的 4 KiB 内读写。`conf` 的 `reg` 写了 1 MiB，但驱动只用了 4 KiB。
- **BAR 分配**：端点的 BAR0 必须落在 MEM 窗口 `0x60300000..0x64000000` 内（61 MiB），
  否则要自己另加一条 iATU outbound 映射；giulia 的 `WIFI_MAPPING.md` 也是这么约定的
  （BAR ≥ 1 MiB、2 的幂、按大小对齐）。

### 1.2 逐项"谁提供"

| 需要的东西 | 这块板子上是谁 | DTS 依据 | ADT 现状 | 要不要在 `make-pipa-adt.py` 里加 |
| --- | --- | --- | --- | --- |
| DWC PCIe host（DBI/ELBI/iATU/CFG 窗口） | `qcom,pcie@1c00000` | dts:10888-10943 | 无 | **要**：`/arm-io/pcie`，只放 `compatible`（理由见 §1.6） |
| QMP gen3x1 PHY + PLL | `0x1c06000`，序列在 `qcom,phy-sequence` | dts:10943 | 无 | 不要（常量进 driver 的 Board 头，见 §1.6） |
| GCC 时钟使能 | `qcom,gcc@100000`（`0x100000/0x1f0000`） | dts:3736-3739 | 无 | 不要（寄存器号从 `gcc-sm8250.c` 抄） |
| GCC 复位 | `GCC_PCIE_0_BCR = 0x6b000`、`GCC_PCIE_0_PHY_BCR = 0x6c01c` | 上游 `gcc-sm8250.c:3540,3543` | 无 | 不要 |
| PCIe 电源域 GDSC | `qcom,gdsc@16b004`（= GCC + `0x6b004`，bit31 PWR_ON，`PWRSTS_RET_ON`） | dts:3843-3847，`gcc-sm8250.c:3224` | 无 | 不要 |
| CX 轨（GDSC 的前提） | `vreg-cx-supply = <pm8150_s3_level>`（ARC 稳压器，RPMh） | dts:10912 | 无 | **没有 RPMh 驱动——见 §1.5** |
| PCIe 1.8 V / 0.9 V | `pm8150_l9` / `pm8150_l5`（RPMh VRM） | dts:10910-10911 | 无 | 同上 |
| WLAN 五路电 | `pm8150_s6`（aon+dig 0.95 V）、`pm8150_s4`（io 1.8 V）、`pm8150_s5`（rfa1 2.0 V）、`pm8150a_s8`（rfa2 1.35 V） | dts:8234-8243；轨名 dts:6060/6095/6109/6123/6552 | 无 | 同上 |
| PERST# / WAKE# / CLKREQ# / WL_EN | TLMM `qcom,kona-pinctrl`（`0xf000000/0x1000000`）的 gpio79/81/80/20 | dts:12127、13044-13090、13203-13231 | 无 | 不要（gpio 号进 Board 头） |
| MSI | DWC 自带 MSI：GIC **SPI 141..148**（mainline 命名 `msi0..msi7`）；downstream 另有 `qcom,pcie0_msi@17a10040` → GIC **SPI 768..799** | dts:10972-10977；mainline `sm8250.dtsi` | 无 | 不要（见 §1.3） |
| INTx | GIC **SPI 149/150/151/152**（int_a..d），另有一条 `int_global_int = SPI 140` | dts:10902 | 无 | 不要 |
| 中断控制器 | GICv3 `0x17a00000/0x10000` + redist `0x17a60000/0x100000` | dts:2785-2793 | **有** `/arm-io/intc` | 已有 |
| SPI 挂接接口 | `armcpu_spi_attach(intid, edge, handler, ctx, …)`，16 个槽 | `drivers/ArmCpu/ArmCpu.cpp:784` | — | 已有（注意 INTID 口径，§6 U8） |
| 端点 DMA 的 SMMU | apps SMMU `apps-smmu@15000000`（1 MiB + TCU `0x15182000`，`qcom,skip-init`），可用 bypass | dts:11939-11942；`package/SmmuBypass.h`（UFS 已在用） | 无 | 不要 |
| IPA 侧 WLAN context bank（不用） | `ipa_smmu_wlan`，iommus SID `0x5c1` | dts:7131-7136 | — | 只有走 IPA 数据通路才需要 |

时钟/复位/GDSC 的具体寄存器（GCC 基址 `0x100000`，下面都是 GCC 内的偏移；从 `gcc-sm8250.c` 抽出）：

| 时钟 | 使能寄存器 | 停摆读回 |
| --- | --- | --- |
| `gcc_pcie_0_slv_axi_clk` | `0x52008` bit0 | `0x6b014` |
| `gcc_pcie_0_mstr_axi_clk` | `0x52008` bit1 | `0x6b01c` |
| `gcc_pcie_0_cfg_ahb_clk` | `0x52008` bit2 | `0x6b024` |
| `gcc_pcie_0_aux_clk` | `0x52008` bit3 | `0x6b028`（源 RCG 在 `0x6b038`，19.2 MHz） |
| `gcc_pcie_0_pipe_clk` | `0x52008` bit4 | `0x6b02c` |
| `gcc_pcie_0_slv_q2a_axi_clk` | `0x52008` bit5 | `0x6b010` |
| `gcc_pcie_phy_aux_clk` | `0x6f004` bit0 | — |
| `gcc_pcie0_phy_refgen_clk` | `0x6f02c` bit0 | — |
| `gcc_pcie_wifi_clkref_en` | `0x8c004` bit0 | — （给芯片的参考时钟 clkref；mainline 的 pcie0_phy 把它列成 `ref`） |
| `gcc_aggre_noc_pcie_tbu_clk` | `0x9000c` bit0 | — |
| `gcc_ddrss_pcie_sf_tbu_clk` | `0x8d058` bit0 | — |

### 1.3 中断：MSI 走哪条路（两套都在硬件上，必须试）

- **DWC 自带 MSI**：mainline `sm8250.dtsi` 的 pcie0 把 8 条 GIC 中断命名为 `msi0..msi7`，
  编号 **SPI 141..148**；端点写 MSI 目标地址 + data，DWC 拉其中一条线。这是最省事的一条：
  `armcpu_spi_attach` 挂 6 条（ath11k 要 3×MHI + CE + WAKE + DP，最多 6 个向量，失败可退化到 1 个）。
- **downstream 的 `qcom,pci-msi@17a10040`**（dts:10972-10977）：`reg = 0x17a10040` 是 GIC 的
  `GICD_SETSPI_NSR`，它把一次 MSI 写直接变成一条 SPI；列出的是 **SPI 0x300..0x31f（768..799）**。
  Android 侧的 `pci-msm` 驱动走这条。
- INTx 和 global：`interrupt-map` 解出来是 pin0→140、pin1..4→149/150/151/152（dts:10902），
  mainline 的 INTx 也是 149..152，两者一致。

**为什么要两套都留**：mainline 与 downstream 在 SM8250 上不一致（一个用 DWC MSI，一个用 pci-msi），
而这块板子是下游闭源 XBL/ABL 配的。M1 阶段先把两套的寄存器都读一遍（`PARF` 的 MSI 使能位、
`GICD_ISENABLER` 里 768..799 与 141..148 的可读写状态），再决定用哪套。

### 1.4 DMA 与地址空间

- `dma-coherent`（dts:10926）：端点 DMA 与 CPU 缓存一致，不用手动 flush/invalidate。
- ath11k 对 QCA6390 设 **coherent mask 32 位、streaming mask 36 位**（`ath11k/pci.c:23-24`）。
  即所有 MHI 环、ER、BHI 镜像必须落在 **4 GiB 以下**。
- pipa 的 DRAM 从 `0x80000000` 起，Android `/memory` 的三段是 `0x80000000+0x37700000`、
  `0xc0000000+0x1c0000000`、`0x800000000+0x200000000`（见 `UniXNU-pipa/board.toml` 的注释）。
  落在 4 GiB 以下的是前两段的一部分：`0x80000000..0xb7700000`（888 MiB）和
  `0xc0000000..0x100000000`（1 GiB），**合计约 1.9 GiB**。
  内核的内存窗口从 `0x80000000` 起、约 5.75 GiB（见 `HANDOFF-pipa.md` §11），所以低段物理页是有的，
  但**能不能一次拿到 18 MiB 物理连续、且确定全在 4 GiB 以下**是待确认项（§6 U5）。
  次选是走 S1 translation（giulia 的做法：64 MiB arena，IOVA 从 `0x20000000` 起），
  `kits/ArmSmmu4K` 有现成的表代码。
- 不走 translation 的话，用 `package/SmmuBypass.h` 把 stream `0x1c00`（和 `0x1c01`）映射到共享的
  bypass context bank——UFS 已经这么做，且 pipa 的 apps SMMU 几何（128 页 / 74 bank）已经读过。

### 1.5 电源：这条链路的真门槛

QCA6390 需要的四路电（aon/dig 共用 s6）和 PCIe 需要的三路电，全部是 **RPMh 稳压器**：

| 轨 | PMIC | 电压（DTS 的 config） | 用途 |
| --- | --- | --- | --- |
| `vdd-wlan-aon` | `pm8150_s6` | 950 mV（`0xe7ef0`） | WCSS always-on |
| `vdd-wlan-dig` | `pm8150_s6`（同一路） | 950 mV | WCSS 数字 |
| `vdd-wlan-io` | `pm8150_s4` | 1.8 V（`0x1b7740`） | IO |
| `vdd-wlan-rfa1` | `pm8150_s5` | 2.0 V（`0x1e8480`） | RFA1 |
| `vdd-wlan-rfa2` | `pm8150a_s8` | 1.35 V（`0x149970`） | RFA2 |
| `vreg-1p8` | `pm8150_l9` | 1.8 V | PCIe PHY |
| `vreg-0p9` | `pm8150_l5` | 0.9 V（`0xd6d80`） | PCIe PHY |
| `vreg-cx` | `pm8150_s3_level` | ARC/CPR，`0xffff/0x100` | CX，GDSC 的前提 |

本仓库**没有 RPMh**：`drivers/NativeUfs/GccUfs.h:30` 写明"`reset` 和 RPMh/TCSR/QMP-PHY 的编程不在这里"；
GPU 那条路的"CX"是 GPUCC 自己的 `gpu_cx_gdsc`（`package/GpuPowerBoard.h:51-53`），
靠清 `SW_COLLAPSE` 再轮询 `gds_hw_ctrl` 点起来的，**前提是 CX 轨本来就通**——这一点在 GPU 上成立，
但 WLAN 专用的 s4/s5/s6/s8 没有任何人打开过。

三条可选路线（建议按顺序试）：

1. **先只读诊断**（`pipa-wifi=1`，仿 `pipa-gpu=1`）：用项目已有的 PMIC arbiter/SPMI 通道
   （`package/Buttons.c`、`NativeBattery.cpp` 都在用）读 PM8150/PM8150A 的稳压器使能位和电压，
   再读 GCC GDSC 和 `parf` 的链路状态。判据：日志里出现 `wifi: rails s6=on(950000) s4=on ... cx=on gdsc=0x…`。
   如果全都在开，M2 可以直接做；如果关着，进 2 或 3。
2. **SPMI 直写 PMIC**（绕过 RPMh）：项目已经在 SPMI 上读写 PON/GPIO/gauge，加 SMPS 的
   VSET/使能写不算大改。风险是这些轨同时被 RPMh 的 TCS 命令集"托管"，AOP 可能在某个事件后把我们的写覆盖掉；
   只适合做"把轨点亮到上机验证 PCIe"的临时手段。
3. **补一个最小 RPMh 驱动**（RSC 的 TCS + `cmd-db`）：所有别的东西（CPU/GPU DVFS、充电、显示）迟早都要，
   而且 `package/GpuProbe.cpp` 已经有 AOP command DB（`0x80860000`）的只读解析，板级常量还有 host 测试
   （`GpuPowerBoardTests.c`）。
   参考实现是 mainline `drivers/soc/qcom/rpmh-rsc.c` + `rpmh-regulator.c`。估 1–2 周，且有和 AOP 抢 RSC 的风险（§5）。
   注意 pipa 的 cnss 节点带 `qcom,cmd_db_name = "smpf2"`（dts:8226）和 `reg` 里的 `tcs_cmd = 0xb2e5510/0x5c0`
   （dts:8213-8214），说明厂商的 CPR 电压也是靠 TCS 命令集发的。

### 1.6 ADT：要加什么、为什么只加这么点

现状（`UniXNU-pipa/scripts/make-pipa-adt.py:190-201`）`/arm-io` 下只有 `intc`（带 reg）和
`ufshc`（**故意不带 reg**，注释写"UFS 在 0x1d84000，落在 arm-io 窗口以下，所以节点不写 reg，
它只为类匹配存在"）。

PCIe 完全一样，而且更极端：

- `parf/phy` 在 `0x1c00000/0x1c06000`，**低于** arm-io 的 `0x17000000` 起址；
- `dbi/elbi/atu/conf` 在 `0x60000000..0x601fffff`，**远高于** arm-io 的 `0x1f000000` 上界。

所以照 `ufshc` 的写法最省事、也最符合本仓库的惯例：

```python
node('pcie', [('compatible', b'qcom,pcie-sm8250\0')]),   # arm-io 下，只为匹配
node('wlan', [('compatible', b'qcom,cnss-qca6390\0')]),  # arm-io 下，只为匹配
```

所有地址/时钟/GPIO 常量进 `UniXNU-pipa/package/WifiBoard.h` + 一个 host 测试
（`TouchBoardTests.c` / `NativeBatteryTests.c` / `GpuPowerBoardTests.c` 都是这个模式）。

理由（已核对 xnu 源码）：

- XNU **支持** DT 的 `interrupts` / `interrupt-parent` / `interrupt-map` / `interrupt-map-mask`
  （`iokit/Kernel/IODeviceTreeSupport.cpp:164` 的 `gIODTInterruptParentKey`、
  `:667` 起的 `IODTMapOneInterrupt`），所以中断按标准写法描述是可行的；
- XNU **不支持** `#clock-cells` / `#gpio-cells` / `#reset-cells` 这类 specifier 解析（没有 clock、
  reset、regulator、pinctrl 框架），这些只能由驱动自己算；
- `reg` 必须能被 `ranges` 翻译（`IODTResolveAddressCell`，`iokit/Kernel/IODeviceTreeSupport.cpp:1220`），
  这就是 `ufshc` 不带 reg 的原因，也是 PCIe 不该带 reg 的原因。

即便如此，中断号也不建议写死进 ADT：§1.3 的 MSI 路线还没定，ADT 里写死只会增加一轮改固件的成本。
把常量放 C 头还能像 GPU 那样做纯主机测试，不必上机。

**不建议**加第二个 `ranges` 把 `0x60000000` 塞进 arm-io：那会让所有 arm-io 子节点多一个地址窗口，
对 UFS/GIC 这些已经在跑的驱动是纯风险。driver 自己按常量建映射更简单。

---

## 2. QCA6390 的固件与协议

### 2.1 分层，以及"能扫到 AP"的最小集合

```
WMI（scan/assoc/key）  ── HTC over CE（copy engine，pipe 映射见 ath11k_target_service_to_ce_map_wlan_qca6390）
QMI（WLFW service）    ── QRTR 风格的消息，落在 MHI 通道 20/21 "IPCR"
MHI（BHI + 环 + 门铃） ── PCIe BAR0
PCIe（DWC + PHY + GCC + PMIC）
```

最小集合（按依赖排序，每层都必须做，没有捷径）：

1. PCIe link up + 能读端点 config 空间（`17cb:1101`）。
2. MHI：BHI 把 `amss.bin` 灌进去（镜像很大，走 BHI，不是 DMA 环），MHI reset/boot，EE=AMSS，
   打开 20/21 两个通道。
3. QMI WLFW（service `0x45` v1，instance `0x01` 是 QCA6390）：`VERSION` → `HOST_CAP` →
   `IND_REGISTER` → 收 `REQUEST_MEM`/回 `RESPOND_MEM` → 下载 BDF → `M3_INFO`（把 `m3.bin` 的地址交给固件）
   → 等 `FW_READY` / `FW_INIT_DONE`（cal 走 cold-boot-cal，默认要等 cal done 通知）。
4. CE + HTC + WMI：连 WMI 服务、`INIT_CMD`、`READY`、`REO/GEO` 能力，然后就能发 `SCAN_REQ` 收扫描结果。
   **扫到 AP 不需要数据通路。**
5. （关联之后才需要）HTT + HAL 环：TX 走 `TCL→WBM`，RX 走 `RXDMA→REO`；密钥安装、四步握手、
   DHCP 都在这一层之上。

参考代码（都能读）：Linux `ath11k/{pci,mhi,qmi,wmi,htc,ce,dp,hal}.c`；
`net/qrtr/mhi.c`（QMI 走 MHI 的那层，如果不想实现 QRTR 可以省掉，直接发 QMI 头）。

### 2.2 固件从哪来

上游（ath11k）路径，**推荐**（我们照 ath11k 的协议实现，就用它的固件）：

```
ath11k/QCA6390/hw2.0/amss.bin     ← BHI 镜像（WLAN 固件本体）
ath11k/QCA6390/hw2.0/m3.bin       ← M3 核，QMI M3_INFO 里给地址
ath11k/QCA6390/hw2.0/board-2.bin  ← BDF（板级数据），也可退回 board.bin
```
（`ath11k/pci.c:1077` 的 `MODULE_FIRMWARE(ATH11K_FW_DIR "/QCA6390/hw2.0/*")`；
`ath11k/hw.h` 里 `struct ath11k_hw_params.fw.dir`）

Android/下游（cnss2）用的是另一套文件名：`qwlan.bin`、`bdwlan.bin`、`otp.bin`、`utf.bin`
（`MiCode/Xiaomi_Kernel_OpenSource` 的 `pipa-t-oss` 分支、
`drivers/net/wireless/cnss2/main.c:51-56`；`cnss2/qmi.c:16` `BDF_FILE_NAME_PREFIX "bdwlan"`）。
两套是**同一颗芯片的同一套固件，只是打包/命名不同**。BDF 如果 linux-firmware 的 `board-2.bin`
里没有匹配我们 chip-id/board-id 的条目，就把厂商的 `bdwlan.bin` 当 `board.bin` 用（ath11k 支持固定 BDF）。

**本机实际找到的**（2026-10-25 核对）：

- `<repo>/out/ipsw/21H461`（iPadOS）里**没有任何 QCA 固件**（iPad 用的是 Broadcom，dext 自带
  `Firmware/C-4355__s-C0/`，见 §3.1）。
- pipa 的 `vendor` 逻辑分区（`android-devinfo/pipa-full-backup-20261022/partitions/super.img` 偏移
  `0x168100000`，EROFS）里只有配置：
  `/etc/wifi/qca6390/WCNSS_qcom_cfg.ini`（一个 `gEnableBDF*` 都没有，只有 `gDot11Mode` 等），
  `/firmware/wlan/qca_cld/qca6390/wlan_mac.bin`（MAC 地址），两者都是指向 `/vendor/etc/wifi/...` 的符号链接。
- **`amss/m3/bdwlan` 不在这块分区上**。它们是 cnss2 通过 `/vendor/firmware`（= 挂载 modem 分区的
  `/vendor/firmware_mnt/image`）读的；我们手上的 `modem_a.img`（F2FS，469 MB）里
  `grep wlan/bdwlan/amss` 全是 0 命中，说明这块镜像不是明文可搜（F2FS 压缩/加密）或没抓到。
  **待确认项 U6：上机 `adb shell ls -l /vendor/firmware /vendor/firmware_mnt/image` 确认三个文件的真名。**
- `UniXNU/out/pipa/tree/.cache/firmware/linux-firmware` 里只有 `a650_sqe.fw`（GPU 的），
  没有 ath11k 目录。

**备选来源**：`linux-firmware.git` 的 `ath11k/QCA6390/hw2.0/`（公开、许可清楚，和 ath11k 完全配套）。

### 2.3 放到本系统的哪里、谁加载

**由内核加载**，理由和 GPU 一样：必须在任何文件系统/网络之前完成，而且要参与初始化时序。

照 `kits/AdrenoFirmware` 的做法（`README.md`：GPU/GMU/SQE/zap 的镜像从厂商分区取出来，
放在 `.cache/firmware/gpu` 外面不入 Git，再由 `scripts/build-native-adreno-a650.py` 编进
`libNativeAdreno.a`），WiFi 应该是：

- 文件放 `UniXNU-pipa/.cache/firmware/wifi/`（**不入 Git**，和 `gpu/`、`touch/` 同级）。
- 新增 `scripts/build-native-wifi.py`，把三份镜像编成一个 `.a`（或生成一个 `const uint8_t[]` 表）。
- 驱动里 `wifi: fw amss 1234567B m3 123456B bdf 98765B` 这样打一行，哈希也打出来，
  便于确认"跑的是哪一份"（GPU 那边就是靠版本号抓出厂商 SQE 是 0x093 的）。

不走用户态的原因：iOS 的 `wifiFirmwareLoader` 是给 Broadcom 芯片用的（读 `/usr/share/firmware/wifi`），
和 QCA 无关；而且用户态加载会和 `launchd` 的启动顺序纠缠。

### 2.4 初始化时序（带来源）

```
1  vreg: aon/dig(s6,0.95V) → io(s4,1.8V) → rfa1(s5,2.0V) → rfa2(s8,1.35V)   cnss2/power.c:858 cnss_power_on_device
2  WL_EN(gpio20)=1（注意 QCA6390 要求 BT_EN 关后至少 100 ms 再拉 WL_EN）    cnss2/power.c:790 附近注释
3  PCIe RC：GCC 时钟/复位/GDSC → PHY PLL + phy-sequence → PERST#(gpio79) 释放
4  端点枚举：config 空间读 17cb:1101；给 BAR0 分配 0x60300000 窗口内的地址
5  ASPM 关掉（L0s/L1 都关，固件下载期间切 AMSS 状态会挂）                  ath11k/pci.c ath11k_pci_aspm_disable
6  MSI 使能（6 或 1 个向量）
7  MHI：BHI 灌 amss.bin → MHI 复位/启动 → 等 BHI 的镜像完成 → MHI M0
8  QMI：VERSION/HOST_CAP/IND_REGISTER → 等 REQUEST_MEM → RESPOND_MEM → BDF → M3_INFO
9  等 FW_READY 和 cal done                                                   
10 CE/HTC/WMI：WMI 服务连接 → INIT → READY → scan 可用
11 （可选）WLAON 电源控制：qcom,set-wlaon-pwr-ctrl 使能时，链路起来后经 BAR0 的
   window 读写 WLAON 寄存器（QFPROM_PWR_CTRL 等）                            cnss2/pci.c:1319 set_wlaon_pwr_ctrl
```

第 11 步值得单独说：pipa 的 DTS 带 `qcom,set-wlaon-pwr-ctrl`（dts:8228），
下游代码里这是**链路起来之后**经端点 BAR0 读写芯片内部寄存器完成的
（`cnss_pci_set_wlaon_pwr_ctrl` → `cnss_pci_reg_read(QCA6390_WLAON_QFPROM_PWR_CTRL_REG)`，
`cnss2/pci.c:1319-1360`）。读写的都是端点 BAR 窗口里的芯片内部寄存器，不是 SoC 侧的寄存器，
所以**这一步**不需要 RPMh（需要 RPMh 的只有电压，见 §1.5）——这对我们是好消息。

---

## 3. iOS 侧期望什么

### 3.1 实测：这台 iPadOS 17.7.11 里没有内核态 IO80211

对 `out/ipsw/21H461/kernelcache.decompressed`（46 MB，iPad7,1 / j120ap）的实际检查：

| 检查 | 结果 |
| --- | --- |
| `__PRELINK_INFO` 里的 kext 清单（约 170 个条目） | WiFi 相关只有 `com.apple.driver.AppleOLYHAL`（可执行段只有 `0x507a` ≈ 20 KB；personality：`IOProviderClass = AppleARMIODevice`、`IONameMatch = "wlan"`、`IONetworkRootType = airport`） |
| 全文件搜 `IO80211Family` | **0 命中**（连字符串都没有） |
| `com.apple.iokit.*` | 有 `IOPCIFamily`、`IONetworkingFamily`、`IONetworkFamily`、`IOSkywalkFamily`、`IOUserEthernet` |
| `IOUserNetworkWLAN` / `IOUserNetworkPacket` | 有（3 / 7 次）→ DriverKit 网络 family 在核里 |

系统卷（`Cryptex1,SystemOS` = `out/ipsw/21H461/044-76034-038.dmg`，raw APFS，可只读挂载）里找到：

```
/System/Library/DriverExtensions/com.apple.DriverKit-AppleBCMWLAN.dext   (4.3 MB 可执行)
  Info.plist 的 personalities：
    DriverKit_AppleBCMWLANBus : IOClass=IOUserService, IOProviderClass=IOPCIDevice,
                                IONameMatch="wlan", IOMatchCategory=com.apple.wifibus.driver,
                                IOUserClass=AppleBCMWLANBusInterfacePCIe,
                                IOUserServerName=com.apple.bcmwlan
    DriverKit_AppleBCMWLANCore: provider=IOUserService, IOUserClass=AppleBCMWLANCore
    DriverKit_IO80211Infra    : IOClass=IOUserNetworkWLAN, CFBundleIdentifierKernel=com.apple.iokit.IOSkywalkFamily,
                                IOUserClass=AppleBCMWLANSkywalkInterface,
                                IO80211APIUserClientProperties={IOClass=IOUserUserClient, IOUserClass=IO80211APIUserClient}
    …另有 AWDL / NAN / SoftAP / LowLatency / Proximity 五个虚接口，全部 IOClass=IOUserNetworkWLAN
  Firmware/C-4355__s-C0/（angera/joya/joyb，`C-4355` 是 Broadcom 的芯片型号字段）
  PlatformConfig/iOS/*.plist
  _ExternalMethod / "AppleBCMWLANUserClient::ExternalMethod[%qd]" / 一堆 APPLE80211_IOC_* 字符串
```

结论：**这台 iPadOS 的整个 802.11 栈（扫描、关联、密钥、软 AP、AWDL、NAN）都在用户态 dext 里**，
内核只负责把 `IOUserNetworkWLAN` 变成 BSD 接口（IOSkywalkFamily），80211 控制面是 dext 自己的
`IO80211APIUserClient` user client，用编号 external method 分发（dext 里有 195 处 `APPLE80211`/`IO80211` 字符串）。

用户态一侧（同一镜像里确认存在）：

```
/usr/sbin/wifid, /usr/libexec/wifiFirmwareLoader, wifip2pd, wifianalyticd, wifivelocityd
/System/Library/PrivateFrameworks/{CoreWiFi,MobileWiFi,WiFiPolicy,WiFiKit,WiFiAnalytics}.framework
/System/Library/PrivateFrameworks/IO80211.framework     ← 就是 80211 的客户端库
```

`IO80211.framework` 的符号（从 dyld 共享缓存的 `.symbols` 里抽出）说明它怎么工作：

```
_Apple80211FindService                   ← 先按 IOKit 服务匹配
_Apple80211BindToInterfaceWithIOCTL      ← 绑定到某个接口（"en0"）
_Apple80211IOCTLGetWrapper / SetWrapper  ← 真正的请求（GET/SET 信封）
_Apple80211EventMonitoringInitIOUC       ← 用 user client 收事件
_Apple80211ServiceMatched/TerminatedCallback
```

**"能不能加载原生 IO80211 栈"的答案是：不能，而且不是因为签名。**

- 这台 iPadOS 上根本不存在 `IO80211Family.kext`——它是 dext，不是 kext；iPad 上也没有
  `/System/Library/Extensions` 和 `KernelCollections`（两处都确认过是空的）。
- 就算有，本仓库的 pipa 内核没有 `IOPCIDevice`（IOPCIFamily 不在 xnu 源码里，我们是自己写驱动）、
  没有 kext 加载器在跑（`docs/drivers/kexts.md`："The shipped DarwinBoot … refuses a request with staged kexts"），
  也没有 DriverKit 的 user server 端到端验证过（源码有 `iokit/Kernel/IOUserServer.cpp`，但 pipa 没跑过）。
- 而且 Apple 的 dext 只认 Broadcom（`AppleBCMWLANBusInterfacePCIe` + `C-4355` 固件）。对 QCA6390 没有复用价值。

信任链这一块反而是通的：`board.toml` 的 `trust_cache_sha256` 装的是**同一份 IPSW 的
Static/System trust cache**，所以 Apple 签名的系统二进制在这个内核里是可信的（用户态 iPadOS 跑起来了就是证明）。
问题不在签名，在于 **iOS 17 把 80211 做成了 dext，而 dext 的 ABI 在用户态**。

### 3.2 用户态到底要什么（可用证据）

`CoreWiFi`/`IO80211.framework` 的调用形态，iOS 16 上已经被逆向出来并写进 giulia 的头文件
（`ios16/wifi/giulia_wifi_apple.h:6-30`）：

```c
IOConnectCallStructMethod(conn, selector, in, inSize, out, outSize)
  GET: inSize = 0x3c30, outSize = 0x3c00
  SET: inSize = 0x3c030
in  = [0] command(GET=0xc02869c9 / SET=0x802869c8) [4] 长度 [8..8+cap) 载荷 …
      末尾("en0" 起) [16] type [20] value
type = 1 SSID, 4 CHANNEL, 10 SCAN_REQUEST, 11 SCAN_RESULT, 12 CAPABILITIES, 13 STATE,
       43 DRIVER_VERSION, 103 CURRENT_NETWORK, 371 ASSOCIATE_RESULT_EXTENDED …
```

也就是说：**用户态看到的是一套"编号 + 固定大小结构体"的 user client ABI**，
和内核里它是 kext 还是 dext 无关（框架只管 `IOConnectCallStructMethod`）。
21H461 的这批常量必须重新采样（信封大小、type 号、`IO80211APIUserClient` 的 selector 布局），
但形态大概率一样（§6 U7）。

### 3.3 四条路与代价

| 路线 | 做法 | 代价 | 结果 |
| --- | --- | --- | --- |
| **A. 内核里实现 80211 nub + user client**（giulia 在 iOS 16 上走的路） | 自己 publish 一个 `IO80211Interface` 类的 nub，挂一个 user client 实现 §3.2 的信封；数据面我们自己有 ifnet | 逆向 21H461 的 ABI（绑定/电源/扫描/关联/当前网络/断开/事件）2–6 周；之后 UI 有机会出现 WiFi 开关 | **唯一能让系统 UI 显示 WiFi 的路**。前提是 `Apple80211FindService` 只按类名匹配（§6 U7） |
| **B. 复用 Apple 的 dext** | 不改 | — | 死路：dext 只认 Broadcom；且我们没有 DriverKit 端到端 |
| **C. 纯绕开：只做 ifnet** | 驱动建一个 `IFT_ETHER` 接口，802.11→Ethernet 由驱动做（ath11k 也是这么呈现的） | 少一半工作量 | socket 能通；`wifid` 看不到，系统 UI 没有 WiFi 图标，设置里也没有 |
| **D. 用户态全套** | 内核只暴露"发/收 802.11 帧 + 扫描"的 user client，扫描/关联/WPA/DHCP 全在用户态自己的守护进程 | 内核简单一点，用户态多；且要和 `configd` 抢接口配置 | 功能等价于 C，只多一个 DHCP 客户端 |

**建议**：C 先做（它是 A 的必经之路：A 也要先有 ifnet 和数据通路），A 在 M5/M6 之后再评估。
D 不要走——`configd`/`IPConfiguration` 已经在跑（`README`：iPadOS 跑到主屏幕），
只要接口是正常的 Ethernet 语义，DHCP 大概率能自己起来；这是一个**待确认项 U9**，
能省掉自己写 DHCP 客户端。

---

## 4. 分阶段计划与判据

每一阶段的判据都是**内核日志里的一行**（本仓库现有格式：`gpu: …`、`battery: …`、`touch: …`，
由 `package/BootRamoops.c` 的日志区带回来，一轮一条命令：`scripts/kernel-round.sh`）。
每行同时要打"失败时的证据"，否则上机一轮白跑。

| 阶段 | 做什么 | 日志判据（成功 / 失败都要有） | 估量 |
| --- | --- | --- | --- |
| **M0** 主机侧 | `package/WifiBoard.h`（§1 全部常量）+ host 测试；`scripts/build-native-wifi.py` 把三份固件编成 `.a`；`make-pipa-adt.py` 加 `pcie`/`wlan` 节点；`pipa_defconfig` 加开关；`board.toml` 里只加 kernel archive（**本次不做，按规矩留给实现者**） | `check-*.py` / host 测试全过；`./unixnu build kernel` 通过；ADT 里能 dump 出两个新节点 | 2–3 天 |
| **M1** 只读体检 | `pipa-wifi=1`：读 PMIC 稳压器状态（SPMI）、GCC GDSC、`parf` 链路状态、SMMU 里 stream `0x1c00` 的现状 | `wifi: rails s6=on(950000) s4=on s5=on s8=on l9=on l5=on cx=on` / `wifi: rails s6=off …`；`wifi: smmu sid 1c00 s2cr=… smr=…` | 2–4 天 |
| **M2** 供电 | 按 §1.5 选一条路把 7 路轨点亮（WLAN 的 s6/s4/s5/s8 + PCIe 的 l9/l5/CX）；WL_EN=1 | `wifi: power on s6=… gdsc=8000000f`；失败 `wifi: gdsc timeout pwr_sts=…` | 3 天（SPMI）/ 1–2 周（RPMh） |
| **M3** PCIe link up | GCC 时钟/复位、PHY PLL + `phy-sequence`、PERST# 释放、轮询 `phy-status(0x814) bit6`、读 config 空间 | `wifi: pcie link gen1 x1 ltssm=… ven=17cb dev=1101 rev=… class=028000`；失败 `wifi: link down parf=… phy=… phy_status=…` | 1–2 周 |
| **M4** MHI 起来 | BHI 灌 `amss.bin` → MHI boot → 开 20/21 通道 | `wifi: mhi m0 ee=4 ch20/21 up`；失败 `wifi: bhi timeout status=…` | 2–4 周 |
| **M5** 固件就绪 | QMI WLFW 全流程（§2.4 第 8–9 步），BDF、m3、FW_READY | `wifi: qmi fw "WLAN.HL…" m3 123456B bdf 98765B cal done` | 1–2 周 |
| **M6** 扫到 AP | CE + HTC + WMI：连服务、INIT、READY、`SCAN_REQ`（被动扫 2.4/5 G 几个信道） | `wifi: wmi ready pdev 0`；`wifi: scan done 9 bss "CMCC-xxx" -47 ch6 …` | 3–6 周 |
| **M7** 关联 + 密钥 + DHCP | WMI peer/vdev、认证/关联、HTT + HAL 环（TCL/WBM、RXDMA/REO）、WPA2 四步、受控端口 | `wifi: assoc 11:22:33:44:55:66 aid=1`；`wifi: rsn ptk/gtk ok port open`；`wifi: en0 ip 192.168.1.23/24 gw …` | 4–8 周 |
| **M8** 系统看见 | 先确认 `configd` 会不会自己给这个接口发 DHCP（U9）；再做 `IO80211Interface` nub + ABI（§3.3 A） | `wifi: ifnet published en0`；之后是 UI 层，判据要重写 | 2–6 周 |

M6 之前不要碰用户态：`wifid` 看不到任何东西是正常的。

---

## 5. 风险与工作量

### 5.1 分级

**几天级**（有明确依据、只差上机）：

- ADT 两个节点、Board 头、固件打包、host 测试。
- GCC 时钟/复位/GDSC 使能（寄存器号已抽好，见 §1.2 表）。
- PHY 序列（DTS 里有完整 99 组，`qcom,phy-sequence`）。
- config 空间读 device ID（iATU region 0 重定向到 bus1/dev0/fn0 后读 `conf` 窗口，前提是链路已经起来）。
- 只读体检（M1）。

**一两周级**：

- PCIe link up（PHY PLL 参数、LTSSM 轮询、PERST# 时序、iATU/BAR 分配）。
- 最小 RPMh 驱动（如果 M1 发现轨是关的）。
- QMI WLFW（协议本身是 TLV，ath11k 源码可逐条对照；难在调试没有 UART，只能靠日志行）。

**几周级**：

- MHI（BHI 镜像、MHI 环/事件环/门铃、EE 切换、M2 睡眠）——2–4 周。
- CE + HTC + WMI + 扫描——3–6 周。
- 数据通路（HTT + HAL 环）+ WPA2/WPA3——4–8 周，这部分是纯体力活，且有大量边界条件
  （giulia 的 `WIFI_NETWORK.md` 列了几十条）。
- Apple 80211 ABI——2–6 周，且**不确定性最大**（要先知道 21H461 匹配什么服务、要件多少 type）。

**结论**：从零到"扫到 AP"（M6），一个人全职大约 **2–3 个月**；
到"能上网"（M7）再 **2 个月**；到"系统 UI 显示 WiFi"（M8）**再加 1–2 个月**。
最大变量是 §1.5 的供电和 §3.3 的 ABI。

### 5.2 具体风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| WLAN 五路电没人开、又不想做 RPMh | M2 卡死，整个链路走不下去 | M1 先只读体检，用证据决定路线；SPMI 直写可做临时手段 |
| RPMh RSC 与 AOP 共用，乱写会把整机挂死（和 `HANDOFF-pipa.md` §10.13 的零拷贝挂死同类） | 上机一轮白跑 | 只写 DRV0 的 TCS，不碰 PDS/唤醒；先在 host 测试里把命令集算对 |
| 4 GiB 以下拿不到 18 MiB 物理连续内存 | M4 直接失败 | 早期就测一次；退路是 S1 translation（`kits/ArmSmmu4K`）+ IOVA |
| MSI 到底走 DWC 还是 `pci-msi` 不确定 | 中断收不到，M4 起不来 | M3 时两套都读一遍再选 |
| QCA6390 的 BDF 和 board-id 不匹配 | 固件起不来，QMI 报 `bdf` 错 | 先试 linux-firmware 的 `board-2.bin`，再试厂商 `bdwlan.bin` 当 `board.bin` |
| WMI/HTT 是 ath11k（不是 ath12k），giulia 的代码只能借结构不能照抄 | 工作量估计偏低 | 以 Linux ath11k 为准，giulia 只借 PCIe/MHI/QMI/ABI 四块 |
| 21H461 的 80211 ABI 与 20H392 不同 | M8 时间翻倍 | 尽早采样（用 dyld cache 把 `IO80211.framework` 拆出来读常量） |
| 设备目前不可用 | 所有估量都没有实测校正 | 每次上机都要带回"成功"和"失败"两种日志行 |

---

## 6. 未知项（按优先级，含验证方法）

| # | 未知 | 怎么验 |
| --- | --- | --- |
| U1 | 冷启动进 UEFI 后 PM8150 s4/s5/s6、PM8150A s8、l5/l9 是开是关 | `pipa-wifi=1` 通过 SPMI 读使能位+电压；和 Android 运行时对比 |
| U2 | CX 轨当前状态（GPU 能点 `gpu_cx_gdsc` 说明大概率是通的，但没证据） | 读 `pm8150_s3_level` 的 ARC 状态 / 读 GCC 里 CX 相关 GDSC 的 PWR_ON_ACK |
| U3 | 这块板的 LTSSM / `phy-status` 语义（`0x814` bit6） | 读一次 `parf+0x814`、`parf+0x840`，和 downstream `pcie-msm` 的 `pcie_phy_status()` 对照 |
| U4 | MSI 走 DWC SPI 141..148 还是 pci-msi 768..799 | M3 时读 `GICD_ISENABLER`/`GICD_ISPENDR` 两段，再各试一次 |
| U5 | 能不能拿到 4 GiB 以下、18 MiB 物理连续内存 | 一次 `IOMallocContiguous`/内存描述符探针，日志打物理地址范围 |
| U6 | QCA6390 固件/BDF 在设备的哪个分区、叫什么 | `adb shell ls -l /vendor/firmware /vendor/firmware_mnt/image`；或从 stock ROM 包里取 |
| U7 | 21H461 的 `Apple80211FindService` 匹配哪个类；信封常量 | 从 `Cryptex1,SystemOS` 的 dyld 共享缓存里把 `IO80211.framework` 拆出来（项目已有 `scripts/probe-target-dyld.py` / `refer/dyld`），读 `__cstring` 与常量；和 giulia 的 20H392 值逐项比 |
| U8 | `armcpu_spi_attach` 的 INTID 口径（DT 的 SPI 编号 vs GIC INTID） | 看 GPU 那条（`README` 写"SPI 300"）实际传的是 300 还是 332；本次不猜 |
| U9 | `configd`/`IPConfiguration` 会不会给一个非 WiFi 的 Ethernet 接口发 DHCP | M7 后看接口有没有自己拿到地址；没有就自己写 DHCP（或静态配置） |
| U10 | `mhi,buffer-len = 0x8000`、`mhi,timeout = 0x2710`（DTS）与 ath11k 的 `8192/2000` 冲突 | 先按 DTS（厂商固件是按它调的），不行再退回 ath11k 的值 |

---

## 7. 参考

**本机**（相对各自仓库根）

- `out/ipsw/21H461/kernelcache.decompressed`（UniXNU）：iPad7,1 内核（`__PRELINK_INFO` 可解析，kext 清单在这里）
- `out/ipsw/21H461/044-76034-038.dmg`（UniXNU）：`Cryptex1,SystemOS`（raw APFS，`hdiutil attach -readonly` 可挂）
- `out/ipsw/21H461/044-76040-038.dmg`（UniXNU）：`OS`（UDIF/lzfse，含完整根文件系统：`wifid`、`IO80211.framework`、dext）
- `~/agents-home/android-devinfo/pipa-full-backup-20261022/partitions/super.img`（vendor，EROFS，偏移 `0x168100000`）
- `UniXNU-pipa/.cache/firmware/{gpu,touch}/`：现有固件的存放惯例
- `UniXNU/out/feeds/xnu-0b4b910d/bsd/net/kpi_interface.h`：`ifnet_allocate` 等 KPI（驱动建接口用）
- `UniXNU-pipa/package/SmmuBypass.h`、`kits/ArmSmmu4K`、`package/GpuPowerBoard.h`、`package/TouchBoard.h`

**兄弟项目（强烈建议先读）**

- `tycykp/UniXNU-OnePlus-Ace5`（SM8650 + WCN7850，iOS 16）：`ios16/wifi/*.h`，
  最相关的是 `giulia_wifi_pci.h`、`giulia_wifi_mhi.h`、`giulia_wifi_qmi.h`、`giulia_wifi_apple.h`、
  `giulia_wifi_dp.h`、`giulia_wifi_rsn.h`；文档 `docs/WIFI_MAPPING.md`、`WIFI_FIRMWARE_READY.md`、`WIFI_NETWORK.md`
- `docs/devices/giulia.md:29`（本仓库对这一路的记载）

**上游**

- `drivers/net/wireless/ath/ath11k/{pci,mhi,qmi,htc,ce,dp,hal,wmi}.c`（固件名 `ath11k/QCA6390/hw2.0/*`）
- `drivers/pci/controller/dwc/pcie-qcom.c`、`pcie-designware*.c`；`arch/arm64/boot/dts/qcom/sm8250.dtsi`（pcie0 节点）
- `drivers/clk/qcom/gcc-sm8250.c`（PCIe 时钟/复位/GDSC 寄存器号，本文 §1.2 的表就是从这里抽的）
- `drivers/soc/qcom/rpmh-rsc.c`、`rpmh-regulator.c`、`Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml`、
  `drivers/power/sequencing/pwrseq-qcom-wcn.c`
- 厂商侧：`MiCode/Xiaomi_Kernel_OpenSource` 分支 `pipa-t-oss`，
  `drivers/net/wireless/cnss2/{pci,power,qmi,main}.c`（本文的供电顺序、WLAON、BDF 文件名都出自这里）
