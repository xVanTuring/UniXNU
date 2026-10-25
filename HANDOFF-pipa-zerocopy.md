# 零拷贝扫描输出：可行性评估与分阶段计划（2026-10-25）

> 由规划代理产出，主代理保存。源码路径以当前工作树为准：驱动在 `/Users/xvan/agents-home/UniXNU-pipa/drivers/`（与主仓库 `/Users/xvan/agents-home/UniXNU/drivers/` 同步）。

---

# 零拷贝扫描输出：可行性评估与分阶段计划

> 建议存放位置：单独立 `HANDOFF-pipa-zerocopy.md`（与 `HANDOFF-pipa.md` 同级），并在 `HANDOFF-pipa.md` 里加一节 `## 10.12 指向该文件`（10.11 已被"三个关键调用的内部分段"占用）。内容已在下面写好，可直接落盘。

## 0. 先说结论

**技术上可行，而且比 10.10 的悲观估计便宜**，原因是三条关键事实：

1. **DPU 的寄存器和格式不用猜**：SM8250 是 SDE/DPU 6.0，SSPP/CTL/LM/INTF/PP 的块偏移有公开定义，且这些块里的 `SRC0_ADDR`/`SRC_YSTRIDE` 正是每帧要改的两个寄存器（见 §2 寄存器表）。
2. **像素格式不用改**：现在这一步拷贝是**逐字节**把 BGRA 行搬进被 DPU 扫描的那块内存（`NativeLegacyRequest.h:115-145`），颜色是对的 ⇒ DPU 当前的 `SRC_FORMAT` 已经精确描述了合成器 surface 的字节布局。零拷贝只需要改**地址和行距**。
3. **SMMU 不必从零写**：`kits/ArmSmmu4K/` 已有"编程 context bank + 挂 stream + 在线映射 + TLB 同步"的完整实现，`drivers/NativeAdreno/` 已经用它在本机跑起 GPU；`drivers/NativeUfs/SmmuTakeover.h:22-126` 更有一份"安全接管一条已有 stream"的写法可以照抄。

但**还有一个决定工作量的分叉没定**：显示控制器那条 stream 在 bootloader 交接时是**旁路/恒等**还是**走页表翻译**。这一条只有只读探针能回答，它把总工作量在"约 6–8 轮"和"约 12–18 轮"之间切换。所以**第一件事必须是 P0 只读探针，不要先写翻转代码**。

---

## 1. 已确认的事实（带出处）

### 1.1 现在的显示链路（代码事实）

- 面板本身已经由 bootloader 点亮，内核只拿到一块内存，驱动里**没有任何 MDSS/DSI 寄存器访问**：`NativeLegacyDisplay.cpp:489-512` 只做 `PE_current_console()` 取 `PE_Video`、`provider->mapDeviceMemoryWithIndex(0)` 映射、校验 `getPhysicalAddress()==v_baseAddr` 且 `getLength()==v_rowBytes*v_height`。
- 同一个 GOP 内存被两处映射：显示驱动（`bootMapping`）和 presenter（`NativeGOPPresenter.cpp:82-98`，并把物理/虚拟地址发布到 `native_gop_physical` / `native_gop_virtual`）。
- 每帧的路径：`kickOnce()` 把 surface 交给 GPU 客户端（`NativeLegacyDisplay.cpp:297-310`），客户端在 `GpuScanoutClient.h:69-99` 里 `present()`，`GpuScanoutPresenter.h:39-74` 发一条 blit；完成回调 `finishGpuSwap()`（`NativeLegacyDisplay.cpp:366-384`）退役。
- 空帧（全层关闭）走 GPIO 直接填充：`NativeLegacyDisplay.cpp:254-275`。
- **没有 vblank 中断**：`NativeLegacyDisplay.cpp:42-47` 明确写了"GOP scanout has no vblank interrupt"，vsync 是 `sheng-vsync` 的定时器伪造的（`:113-127`）。这是零拷贝之后最值得顺便修掉的东西（见 §6）。

### 1.2 硬件事实（本机 Android 内核日志 + 上游 SM8250 定义）

日志来源：`out/pipa/device-logs/20261025-095116/dmesg`（读日志程序抓到的**上一次 Android 启动**的 ramoops console，同一块硬件）。

| 事实 | 值 | 出处 |
|---|---|---|
| 显示子系统 | `mdss@0xae00000`（顶层 0x1000） | 上游 `sm8250.dtsi` `mdss: display-subsystem@ae00000`；Android 日志 `msm_drm ae00000.qcom,mdss_mdp`（dmesg:2544） |
| MDP/DPU | `ae01000`，长度 `0x8f000`；`vbif@aeb0000`（0x3000） | 同上 `mdp: display-controller@ae01000` |
| SDE 版本 | **6.0**（日志：`sde hardware revision:0x60000000`） | dmesg:2565；上游 `dpu_6_0_sm8250.h` |
| DSI | ctrl0 `ae94000`、ctrl1 `ae96000`、PHY `ae94400`/`ae96400`，**双 DSI、video mode** | dmesg:1738-1755、2575 |
| 面板拓扑 | `lm: 2 comp_enc:2 intf: 2`，即两半各一个 LM/CTL/INTF | dmesg:2575 |
| 刷新率 | 面板能力 120/144/90/60/48/50/30（DFPS），默认 120 | dmesg:2576-2593 |
| Display SMMU | `15000000.apps-smmu`（SMMUv2），87 个 SMR 组；显示有 `smmu_sde_unsec_cb` 与 `smmu_sde_sec_cb` 两个上下文 | dmesg:425-427、2566-2572 |
| 帧缓冲位置 | **`0x9c000000`**，carveout `0x2300000`（36.7 MB），`simple-framebuffer` 1800×2880 **stride 7200**，格式 `a8r8g8b8` | 上游 `sm8250-xiaomi-pipa.dts` 的 `/chosen/framebuffer@9c000000` 与 `cont_splash_mem` |
| MDSS 电源/时钟 | `mdss_core_gdsc`（pm8150a_s4）、`dispcc@af00000` | dmesg:694-722 |

`stride 7200` 与任务里给的"固件 GOP framebuffer stride 7200"一致，`0x9c000000 + 7200*2880 = 0x9c000000 + 20,736,000` 落在 carveout 内 ⇒ **几乎可以确定 `PE_Video.v_baseAddr == 0x9c000000`、`v_rowBytes == 7200`**（P0 直接读出来确认即可，无需假设）。

carveout 只有 1.77 帧大小，**装不下第二块 1800×2880 缓冲**，所以静态测试的第二块缓冲区不能指望它（见 §4 P1）。

### 1.3 内核看到的设备树是**生成的 ADT**，不是厂商 FDT

`out/pipa/logs/payload-adt.log:1`：`pipa ADT: 13912 bytes, dram 0x980000000, cpus 8, timebase 19200000`。也就是说能让 `ArmDTPlatform` 发布成 `IOPlatformDevice` 的节点（`ArmDTPlatform.cpp:87-109`）由设备仓库的 ADT 生成器决定，**厂商 FDT 里的 mdss / iommus / qcom,sde-* 属性不一定在内核里可读**。

这直接影响 P0 的做法（两条都可行）：

- **A**：在设备仓库的 ADT 生成器里加一个 mdss 节点（`reg`、`iommus`、`qcom,sde-sspp-*` 等），之后按现有 `Info.plist` 的 `IOProviderClass=IOPlatformDevice`（`drivers/NativeAdreno/Info.plist:10-22`）匹配即可，**不占内核命令行**。
- **B**：先不碰 ADT，用 boot arg 传基址/SID。注意 10.6 记录的**命令行 1000 字节上限**已经很紧，这条路只能塞 2–3 个参数。

推荐 A（干净、可复现），B 只用于最早一轮"只想看一眼"。

### 1.4 XNU 侧现成可复用的设施

| 需要 | 已有东西 | 位置 |
|---|---|---|
| SMMU context bank 编程 | `ArmSmmu4KContext` / `ArmSmmu4KProgram` | `kits/ArmSmmu4K/ArmSmmu4KContext.h`、`ArmSmmu4KProgram.c:26-40`（TLBIASID / TLBIALL + TLBSYNC） |
| 挂 stream（SMR/S2CR/CBAR 顺序 + 回读） | `ArmSmmu4KAttachStreams` / `FaultStreams`，以及 UFS 那份"接管已有 stream"的规划器 | `kits/ArmSmmu4K/ArmSmmu4KStreams.h:24-59`；`drivers/NativeUfs/SmmuTakeover.h:22-126` |
| 在线改映射（4K 表 + 发布 + TLB 同步） | `ArmSmmu4KLive*` | `kits/ArmSmmu4K/ArmSmmu4KLive.h`、`NativeAdreno/NativeAdrenoVm.*` |
| 物理连续大块内存 | `kIOBufferMemoryDescriptor` + `kIOMemoryPhysicallyContiguous`（GPU 池已在用，几百 MB 级） | `drivers/NativeAdreno/NativeAdrenoMemory.cpp:78`；`NativeUfs.cpp:906-920` |
| SMMU/MMIO 只读探针对照 | MIB 例程（IDR0/IDR1/IDR2/SMR/S2CR/CBAR/SCTLR 偏移） | `SmmuTakeover.h:26-75` |
| 内核日志遥测行 | `display: stage …`、`NativeAdreno: … ` `IOLog` 模式 | `NativeLegacyDisplay.cpp`、`NativeAdrenoService.cpp:272-343` |

---

## 2. 改 SRC0 到任意 buffer 需要什么

### 2.1 每帧真正要写的寄存器（SDE 6.0，偏移相对 `0xae01000`）

| 块 | 块基址 | 寄存器 | 偏移 | 说明 |
|---|---|---|---|---|
| SSPP VIG0 | `0x4000` | `SRC0_ADDR` | `+0x14` | 平面 0 基址（每帧写） |
| | | `SRC_YSTRIDE0` | `+0x24` | 低 16 位 = 平面 0 行距（**7200 → 7424 必须写**）；高 16 位 = 平面 1 |
| | | `SRC_SIZE` `+0x00`、`SRC_XY` `+0x08`、`SRC_FORMAT` `+0x30` | | **只读校验**，不改 |
| SSPP VIG1..VIG3 / DMA0-3 | `0x6000`/`0x8000`/`0xa000`/`0x24000…` | 同上 | | 面板是左右两半，可能用两个 SSPP |
| CTL0..CTL5 | `0x1000`+`0x200*n` | `CTL_FLUSH` | `+0x18` | 写 flush mask，帧边界生效；`CTL_LAYER(lm)` 在 `+lm*4` |
| INTF1 / INTF2 | `0x6a800` / `0x6b000` | `FRAME_COUNT` `+0x0AC`、`LINE_COUNT` `+0x0B0`、`HSYNC_CTL` `+0x008`、`VSYNC_PERIOD_F0` `+0x00C`、`STATUS` `+0x26C` | | 数帧/等帧边界/反算时序 |
| LM0/LM1 | `0x44000`/`0x45000` | 混合级（`CTL_LAYER` 里编码） | | 只读 |
| PP0/PP1 | `0x70000`/`0x70800` | | | 一般不用碰；`INTF_MUX(+0x25C)` 是 PP→INTF 绑定 |
| VBIF | `0xaeb0000` | | | 不动 |

寄存器名与偏移来源（上游、公开）：[`dpu_hw_sspp.c`](https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/msm/disp/dpu1/dpu_hw_sspp.c)（`SSPP_SRC0_ADDR 0x14`、`SSPP_SRC_YSTRIDE0 0x24`、`SSPP_SRC_SIZE 0x00`、`SSPP_SRC_XY 0x08`、`SSPP_SRC_FORMAT 0x30`）、[`dpu_hw_ctl.c`](https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/msm/disp/dpu1/dpu_hw_ctl.c)（`CTL_FLUSH 0x18`、VIG0=bit0、VIG1=bit1、LM0=bit6/7、CTL=bit17）、[`dpu_hw_intf.c`](https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/msm/disp/dpu1/dpu_hw_intf.c)（`INTF_FRAME_COUNT 0x0AC` 等）、块基址 [`catalog/dpu_6_0_sm8250.h`](https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/msm/disp/dpu1/catalog/dpu_6_0_sm8250.h)。

> 厂商 DT 里通常还有 `qcom,sde-sspp-off` / `qcom,sde-ctl-off` 之类属性，**以 DT 为准、上游值为交叉校验**；两者不一致时按 DT 走。

### 2.2 提交（commit）与"扫描时不能改"

- DPU 的所有 SSPP/CTL 配置都是**双缓冲**：写进去先进 pending，`CTL_FLUSH` 置对应 SSPP 位（VIG0=bit0）后，在**该接口的帧边界**原子生效。视频模式下**不需要** `CTL_START`/`CTL_PREPARE`（那两个是 cmd mode / WB 用的），见 `dpu_hw_ctl.c` 里 `trigger_flush` 的实现与 `dpu_hw_ctl_intf_cfg_v1` 只在 `core_major_ver>=7` 才碰 `CTL_TOP`。
- "扫描输出正在读这块内存时不能改"这条约束，对**寄存器**是硬件替你保证的（帧边界生效，同一帧内不会一半新一半旧）；真正危险的是**改缓冲内容**——那是 buffer 生命周期间题（§5）。
- **左右两半必须同帧生效**：面板是双 DSI，左右各有 LM/CTL/INTF。做法是先把两个 SSPP 的 `SRC0_ADDR`/`SRC_YSTRIDE0` 都写完，再连写两个 `CTL_FLUSH`。两条 DSI 共用 PLL/时序，落到同一帧的概率很高，但**要用"整屏纯色交替翻转"目视确认没有半屏撕裂帧**。
- 想更稳：在 `INTF_LINE_COUNT` 处于垂直消隐区时发起翻转（读 `LINE_COUNT` 轮询），或用 `INTF_FRAME_COUNT` 递增作为"帧边界到了"的确认。这套轮询不需要中断，先这么做；中断（`mdss` 的 GIC SPI 83 + MDP 子中断）留到以后再说。

### 2.3 SMMU：三种情形，工作量差一倍

显示这条 stream 在 bootloader 交接时只有三种可能，**P0 一读就知道**：

| 情形 | 判据（P0 读到的） | 翻到我们自己的非连续 buffer 要做什么 |
|---|---|---|
| **A 旁路/恒等** | S2CR 的 TYPE=bypass，或该 SID 无有效 SMR 且全局 GBPA=bypass；`SRC0_ADDR` ≈ `0x9c000000` | **什么都不用做**：连续物理内存的地址直接写进 `SRC0_ADDR`。GPU 池已经能分配连续大块（`NativeAdrenoMemory.cpp:78`） |
| **B 走页表（固件表）** | S2CR TYPE=translation，CBANK 有效，`SRC0_ADDR` 是个不像物理地址的 IOVA | 把这条 SID **接管到我们自己的 bank**（照 `SmmuTakeover.h:22-126` 的规划器），把 surface 页映射进一个 2 MB 对齐的 IOVA 槽 |
| **C 掩码匹配/冲突** | 该 SID 的 SMR 带 MASK 且与多个 stream 共享 | 先按 B 尝试；接管被库拒绝时，退而"复用固件 bank、往它的表里补 PTE"（风险最高，只在 B 不可行时考虑） |

SMMU 相关寄存器（SMMUv2，基址 `0x15000000`）：`IDR0 0x20`、`IDR1 0x24`、`IDR2 0x28`、`GBPA 0x00`、`SMR 0x800+4i`、`S2CR 0xc00+4i`、`CBAR 0x1000+4i`、`CB_n_SCTLR (pages+n)*0x1000`、`TLBIASID 0x610`、`TLBIALL 0x618`（偏移见 `SmmuTakeover.h:26-75` 与 `ArmSmmu4KProgram.c:26`）。

两个重要细节：

- **TLB 只能按 ASID 或全刷**：这个库不支持按地址失效（`ArmSmmu4KProgram.c:26-27`）。所以显示必须用**自己的 ASID**，每帧 `TLBIASID + TLBSYNC` 只影响自己，不会把 GPU/UFS 的 TLB 打掉。
- **稳态可以做到零 SMMU 开销**：合成器其实只轮换少数几块 surface。按 `IOMemoryDescriptor` 指针建一张"已映射"缓存，同一块 surface 第二次出现就直接复用 IOVA，**稳态每帧只有 2 次寄存器写 + 1 次 flush**。

### 2.4 编译期/运行期需要新增的开关（建议）

`pipa-mdss=0`（默认关，保持现状）`1`（只读探针）`2`（静态图案翻转）`3`（线上零拷贝）。全部走 ADT 属性或 boot arg，失败即回落现有拷贝路径。

---

## 3. 未知项（只能靠探针回答）

| # | 未知 | 为什么关键 |
|---|---|---|
| U1 | `SRC0_ADDR` 的实际值 = `0x9c000000` 还是某个 IOVA | 决定 §2.3 的 A/B 分支，工作量差一倍 |
| U2 | 真正在用的**哪几个 SSPP**、`SRC_SIZE`/`SRC_XY`/`SRC_FORMAT`/`SRC_YSTRIDE` 各是多少 | 决定要改几个寄存器；也验证"左右各 900 宽"的猜测 |
| U3 | 面板当前实际刷新率与 h/v total（读 `HSYNC_CTL`/`VSYNC_PERIOD_F0`） | 决定翻转调度窗口和"每帧省 7 ms 值多少" |
| U4 | 显示 SID 的 SMR/S2CR/CBAR 现状、全局 GBPA、有没有空闲 context bank | §2.3；也用来判断"接管"是否安全 |
| U5 | 合成器**实际轮换几块 surface**、它们的页是否连续（P0 顺带统计页段数） | 决定 N=2 还是 N=3，以及有没有可能走"连续 + 旁路"这条最省事的路 |
| U6 | 一次翻转的生效延迟（0 帧还是 1 帧）与左右半屏是否同帧 | 决定退役时机（能不能继续 early-retire） |
| U7 | 内核 ADT 里有没有 mdss/iommus 节点；设备仓库 ADT 生成器加节点的成本 | 决定 P0 用 A 还是 B 路线 |
| U8 | `pipa` 上"黑屏但系统还在跑"时 USB/fastboot 是否照常（读日志程序能否按时回 fastboot） | 决定自愈策略是"软件回滚"还是"直接复位" |

> 这几个问题里有 3 个（U1/U2/U4）在 **UEFI payload 里读**比在内核里读更省事：payload 本来就有 `FdtLib` 能读 `EFI_DTB_TABLE`（`package/UniXNUPkg/Test/HelloPayload/HelloPayload.c:91-119`），MMIO 也是直通的。但 payload 的输出要另找通道（屏幕/ESP 文件），内核 `IOLog` 有现成的日志管线。**推荐：读寄存器放内核探针（有日志），读厂商 FDT 放 payload 或 ADT 生成器（有 FdtLib）**。

---

## 4. 分阶段计划

### P0 只读探针（1–2 轮）

**做什么**（全部只读，不写任何 MDSS/SMMU 寄存器）：

1. 新增 `NativeMdssProbe`（IOService，匹配 ADT 里的 mdss 节点）：
   - 打印 `PE_Video` 全字段与 presenter 的 `native_gop_physical`；
   - 打印 MDP 版本字（`mdp+0x0`）、`CTL_LAYER(0..1)`、`INTF1/2` 的 `STATUS`/`FRAME_COUNT`/`LINE_COUNT`/`HSYNC_CTL`/`VSYNC_PERIOD_F0`；
   - 对 VIG0..3、DMA0..3 逐个打印 `SRC_SIZE`/`SRC_XY`/`SRC0_ADDR`/`SRC_YSTRIDE0`/`SRC_FORMAT`；
   - 打印 `INTF_MUX`、`CTL_INTF_ACTIVE`；
   - 隔 1 秒再打印一次 `FRAME_COUNT`（算刷新率）与 `SRC0_ADDR`（确认没人在改）。
2. SMMU 只读：`IDR0/1/2`、全局 `GBPA`、显示 SID 对应的 SMR/S2CR/CBAR/SCTLR，以及对整个 SMR 表做一次"谁的 SMR 命中这个 SID"扫描。
3. surface 统计（可选，挂在现有 offer 路径上）：每帧记 `IOMemoryDescriptor` 指针 + `getPhysicalSegment` 的段数，10 秒后打印"不同 surface 数 / 平均段数"。

**判据**（这一轮就该能全部拿到）：

- `mdss: base` 行确认 MDP 可读，版本字与 `0x60000000` 同族；
- 至少一个 SSPP 出现 `SRC_SIZE = 2880×1800` 量级、`SRC_YSTRIDE0` 低 16 位 `= 7200`，`SRC0_ADDR` 在 `[0x9c000000, 0x9db00000)` 内；
- 两个 `INTF_FRAME_COUNT` 在 1 秒内各自递增、且频率一致（得到真实刷新率）；
- SMMU 行给出 U1/U4 的答案；
- surface 行给出 U5 的答案。

**风险控制**：只在"屏幕亮着"的轮次跑（不要开 `pipa-power-at`）；先读一个字节级寄存器，读回值合理再继续读其它块（避免"块没上电还去读"导致总线挂死）。

**2026-10-25 实测修正：MDSS 寄存器在 XNU 里不可读，P0 只做 SMMU + 帧缓冲。**

- 第一版探针（`package/MdssProbe.cpp`，late probe 之后 6 s）读 `0xae01000+0x0`，内核 panic：
  `panic: Unhandled implementation specific error`。该字符串是 `osfmk/arm64/sleh.c:399`，由 `sleh_serror`（`:2261`）经
  `arm64_platform_error(:446, PLAT_ERR_SRC_ASYNC)` 到达——即**异步 SError**（总线/interconnect 报告事务失败），本内核没有
  任何 SError 处理函数（`PE_handle_platform_error` 返回 false），一次坏读就是 panic。
- SError 是异步的：abort 可能在指令退休之后、在任意 CPU 上到达，**所以"先读心跳再读整块""按块分别映射""第 N 帧之后才读"都不能
  让它变安全**。实证：同一镜像两轮，一轮读出了 8 个 SSPP 和 CTL 后卡死，另一轮第一次读就 panic。
- 另外内核里没有任何代码给 MDSS 的 GDSC/时钟投票，该块能否应答只取决于 bootloader 留下的状态，这个状态会变。
- 结论：P0 的 XNU 侧只读 **apps SMMU（`0x15000000`，TouchProbe 每轮都在同一窗口里写 SMR/S2CR，UFS 也走它，必然是上电的）**、
  `PE_Video`、以及 offer 路径上的 surface 统计；**MDSS 侧事实（版本字、SSPP/CTL/INTF/PP、刷新率）移到 UEFI payload**，
  那里固件自己的显示编程还在、同一批寄存器已知会应答。payload 侧要读的寄存器清单见 `package/MdssProbe.cpp` 头注释；
  它还需要一个自己的输出通道（`payload-adt.log` 是主机脚本写的，不是 payload 写的）。
- 由此 U1（地址是物理还是 IOVA）由 SMMU 的 S2CR/SCTLR 判定（A/B/C）回答，U3（刷新率）由 offer 计数（`fps`）回答，U5 由
  surface 统计回答；只有 U2（具体哪几个 SSPP）暂时缺失，等 payload 读。

### P1 静态图案翻转（2–3 轮）

**目标**：把扫描输出源切到**我们自己分配的**、7424 行距的连续 buffer，显示固定图案，再切回原地址——全程不动 SMMU（情形 A）或用一次性的 context 映射（情形 B）。

步骤与判据：

1. 分配 2 块 `7424×2880`（21,381,120 B）**物理连续**内存（`inTaskWithPhysicalOptions` + `kIOMemoryPhysicallyContiguous`，照 `NativeAdrenoMemory.cpp:78`），填红/绿，`clean` 到 PoC。
   - 判据：日志打印两块物理基址与 `contiguous` 长度 ≥ 21.4 MB（分配失败就直接停在这里，说明要换小尺寸或换池）。
2. （仅情形 B）为显示 SID 建一个我们自己的 context bank + ASID，把两块 buffer 映射到两个 2 MB 对齐的 IOVA 槽；**接管前先把原 SMR/S2CR/CBAR/SCTLR 存下来**。
   - 判据：`program`/`attach` 全步骤回读通过（库本身会验），且 `ArmSmmu4KObserveStreams` 看到路由确为我们。
3. 读当前 `SRC0_ADDR`/`SRC_YSTRIDE0`（两半各一份）并保存为"安全值"。
4. 把两半的 `SRC0_ADDR`/`SRC_YSTRIDE0` 写成红块，flush 两个 CTL。
   - **判据**：屏幕变红；无斜切（说明 stride 对）；左右都变（说明两个 SSPP 都写到了）；屏幕无花点（说明没 UBWC/格式问题）。
5. 等 1–2 秒，切成绿块，flush。
   - 判据：变绿；用手机录 120 fps 视频看有没有"半红半绿"的撕裂帧（U6）。
6. 切回保存的安全值，flush。
   - **判据（最重要）**：桌面画面完整恢复，系统不重启。这一步证明整个方案**可逆**。
7. 记录 `FRAME_COUNT` 在翻转前后的差值 → 生效延迟。

**若 P1 第 4 步黑屏**：不重启也能恢复——但需要第 3 步的安全值仍在内存里，所以**第 3 步之后必须挂一个独立的内核定时器**：5 秒内没收到"用户确认"（用一个 boot arg 或"再写一次原值"的固定超时）就自动写回安全值。这是零拷贝实验的**必备安全网**，P1 就要有，不能等到 P3。

### P2 单缓冲在线翻转（2–4 轮）

**目标**：翻转源改成**合成器真实 surface**（先固定用第一块，不做轮换），确认格式/行距/时序都对，测量收益。

- 在 `kickOnce()` 的 GPU 分支里加一条旁路：不 offer 给 GPU 客户端，而是把 surface 的 `IOMemoryDescriptor` 映射进显示 context 的**固定槽 0**，写两个 SSPP 地址（`7424` 行距），flush；swap 退役推迟到"翻转已生效"之后（不再 early-retire）。
- 判据：桌面正常、颜色/几何正确、`A2 copy` 行为 0、帧时间下降 ≥6 ms、`display: pacing` 的 2 周期帧占比下降；连拍视频无撕裂。
- 这一阶段最容易踩的坑：**surface 什么时候可以被复用**。P2 仍然只允许一块在飞，所以退役必须晚于翻转生效。

### P3 N 缓冲 + 生命周期（3–5 轮）

- IOVA 槽位缓存（按 `IOMemoryDescriptor` 指针），N=3；每次用新槽后等一帧再回收旧槽。
- 退役语义：翻转生效后退役当前 swap；**空闲（>200 ms 无 swap）/客户端关闭/息屏时，立刻翻回我们自己拥有的一块黑 buffer**（否则合成器释放 surface 后 DPU 还在读 → 硬件 UAF）。
- GPU 侧的"帧已落地"保证：把 `serveOne` 里的拷贝换成**同队列上的 1 像素 blit + wait**（队列顺序保证渲染已完成），再 `COMPLETE_SWAP` 触发内核翻转。代价 0.1–0.5 ms，换掉 7 ms。
- 判据：连续 200 帧无花屏/无 SMMU fault；锁屏/解锁、翻页、切 App 都正常；`sheng-fb-gpu=1` 关闭时仍走老路；异常注入（拔掉客户端、强制 GIF 失败）能回落到拷贝。

### P4 收尾（1–2 轮）

把 `sheng-vsync` 的定时器换成真实 `INTF` 帧计数/中断（可选，收益是 vsync 时间戳真实）；把 `A4/A5` 类遥测换成"翻转耗时/生效延迟/SMMU 映射命中率"。

---

## 5. 风险与回退

| 风险 | 后果 | 处置 |
|---|---|---|
| 写坏 SRC0/STRIDE 后黑屏 | 面板一直黑，但系统仍在跑 | ①启动时就保存安全值；②5 秒看门狗写回；③`pipa-mdss` 默认 0，坏镜像下一轮直接不启用；④最终手段 PSCI SYSTEM_RESET（`ArmDTPlatform.cpp:112-121` 已有同款） |
| SMMU fault（映射错/漏页） | DPU 取指失败 → 画面冻结/欠载色；可能持续 fault 占总线 | 映射后**先写 1 个页面做验证性读**再翻转；fault 发生时立即写回安全值 + 拆掉我们的 SMR；把 SMMU fault 状态寄存器读出来打日志 |
| **残留 SMR 跨重启** | 我们的 SMR/S2CR 在热复位后可能还在（UFS 代码里明确提到别的 OS 留下的映射会存活，`SmmuTakeover.h:78-81`） | ①正常路径退出时恢复原 SMR/S2CR；②P0 探针每次启动都检查"这个 SID 是不是指向我们自己"，是就修复；③绝不把"改 SMMU"放在无法回滚的路径上 |
| 与固件其它使用者冲突（secure display / WB） | 抢了 secure context 的 SID 可能触发安全侧异常 | 只碰 `smmu_sde_unsec_cb` 那条；secure SID 一律不动；P0 先列出两类 SID |
| buffer 生命周期（合成器每帧换 surface） | DPU 读到已被复用的 surface → 撕裂/花屏；读到已释放内存 → 更糟 | N=3 + 按描述符缓存 IOVA + 退役晚于翻转 + 空闲翻回自有 buffer（§4 P3） |
| 左右半屏不同帧 | 一帧的半屏错位 | 两个 CTL 连写 flush；用录屏验证；必要时等消隐区 |
| 面板/屏被合成器关掉后仍扫描 | 息屏时黑屏但 DPU 仍在读 | 允许（这正是现在的行为），但源必须是自有 buffer |
| 合成器只轮换 1 块 surface | 无法零拷贝（渲染时就写在屏上） | P0 的 U5 先测；若真是 1 块，需要 CA 侧多缓冲，方案要重新评估 |
| "改坏了要不要重启到 fastboot" | 恢复时间 | 轮次流程本身就会回 fastboot（`HANDOFF-pipa.md:60`）；卡死时按同族 ABL 习惯长按电源 10–15 s、再"音量下+电源"（tb128fu 上验证过，**pipa 上待 P0 轮次顺带确认**） |

---

## 6. 顺带能拿到的额外收益

- **带宽/功耗**：每帧省掉"读 21.4 MB + 写 20.7 MB"（约 41 MB，48 fps 下 ≈ 2 GB/s），DPU 的读取不变。
- **GPU 队列变短**：现在拷贝命令经常排在下一帧渲染后面（`HANDOFF-pipa.md:33-35`），去掉后命令从提交到完成的尾部延迟会明显下降。
- **vsync 可以变真**：现在是定时器（`NativeLegacyDisplay.cpp:113-127`），有了 `INTF_FRAME_COUNT`/中断就能给合成器真实时间戳。

---

## 7. 工作量估计

| 阶段 | 轮次 | 主要不确定性 |
|---|---|---|
| P0 只读探针 | **1–2** | ADT 里加节点的成本（U7）；若是走 boot arg，受 1000 字节命令行限制 |
| P1 静态翻转 | **2–3**（情形 A）／**4–6**（情形 B） | SMMU 接管是否被库的安全检查拒绝（U4）；连续内存分配 |
| P2 单缓冲在线 | **2–4** | 格式/行距/退役时机（U6） |
| P3 N 缓冲与生命周期 | **3–5** | surface 复用节奏（U5）、空闲回退、fault 处置 |
| P4 收尾 | **1–2** | — |
| **合计** | **约 9–17 轮** | 情形 A ≈ 9–13，情形 B ≈ 13–17 |

按一轮（注入 + 200 秒交互 + 读日志，读日志本身偶尔 8–9 分钟）15–30 分钟估，**集中投入约 3–6 个工作日**；分散在别的任务之间做，按"每天 3–4 轮"算大约一周半。

**如果 P0 给出情形 A（旁路/恒等），可以把 P1 和 P2 合并成一阶段**，因为"翻到连续 buffer"和"翻到 surface"只差地址来源。

---

## 8. 给上级代理的两点提醒

1. **路径不一致**：任务里给的源码路径 `/Users/xvan/agents-home/UniXNU-pipa/` 在本机不存在；上述 `file:line` 全部来自当前工作树 `/Users/xvan/agents-home/UniXNU/`（内含 `drivers/NativeMobileDisplay`、`drivers/NativeMetal`、`kits/ArmSmmu4K`）。
2. **有三个文件只存在于 pipa 设备仓库、本机读不到**：`package/` 下的探针（`TouchProbe.cpp`、`Gestures.c`、`Buttons.c`）、ADT 生成器（`out/pipa/logs/payload-adt.log` 提到的那一支）、以及注释里引用的 `docs/native-ufs.md` / `docs/gpu-hardware-plan.md`。**如果希望把 P0 的"ADT 加节点"成本估准，建议让 explore 代理去 UniXNU-pipa 仓库看这三处**：
   - ADT 生成器怎么描述节点（能否直接加 `mdss` 的 `reg`/`iommus`/`qcom,sde-*`）；
   - `board.toml` 的 `[[bootimg.dtb_node]]` 是否已经在用；
