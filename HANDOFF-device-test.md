# TB128FU 真机首测记录与交接

> 写于 2026-10-22，设备已连接、可进 fastboot。这是当前设备状态的权威记录；先读这一份，再读
> `HANDOFF-tb128fu.md` 第 10–12 节的移植背景。
>
> 一句话状态（最新）：**内核已启动到 IOKit，NativeUfs 在 SMMU 流映射处主动 panic，panic 屏
> 正常显示（见第 11 节）。** 早先卡在 `_arm_init` 的原因是 PAN：SM6225 的 CPU 是 ARMv8.0，
> `tb128fu_defconfig` 照抄了 `CONFIG_XNU_NATIVE_PAN=y`，已改为 `n`。UFS 流 ID 也照抄了 SM8550 的
> `0x60`，已改为 `0x100`，待真机验证。
>
> 较早的状态：UEFI/DarwinBoot 正常；探针复位（计数器等待 + PSCI SYSTEM_RESET）在真机可用；
> MMU 打开成功（stage 3 在 MMU 打开后等 5 秒复位，已观察到重启）。
>
> **更正（同日后续）**：本文最初记录的实验 2、3 无效。`payload.py` 原来只在 `panic_screen = true`
> 时才运行 `patch-xnu-entry-probe.py`，关掉 panic 屏后整个入口探针都没打进内核（payload 里的
> 内核哈希等于原始内核 `a696551b…`，入口字是原始的 `140006F5`，cave 全零）。已修正，见第 5、8 节。

## 1. 设备与连接

- 型号 TB128FU（联想小新 Pad 2022，SM6225 bengal），fastboot 序列号 `HA1MTVNE`，product `P89990JA1`。
- **当前设备**：`boot_a` 是 stage 3 复位镜像（`9fcb2f6c…`），每次启动都会在 MMU 打开约 5 秒后
  复位，所以设备在“启动 → 复位”之间循环，不会停在 fastboot。接手时在重启过程中按住音量下+电源
  进 fastboot。探针的复位是普通重启，不会自动回到 fastboot。
- 当前槽位 `a`；`boot_a` 96 MiB；`userdata` 112,248,791,040 字节。
- 设备在 fastboot 时 Mac 可见；进入内核后 USB 不枚举（没有 adb/fastboot）。
- USB 链路偶发掉线：`fastboot flash` 可能报 `usb_read failed with status e00002ed` 或
  `usb_write failed with status e000404f`；拔插 USB 线（直插 Mac，不经 hub）后重试即可。
- 设备卡住时的恢复：长按电源 10–15 秒强制关机，然后**音量下 + 电源**进 fastboot。

## 2. 已刷入的内容

| 分区 | 内容 | sha256 |
| --- | --- | --- |
| `boot_a` | 当前诊断镜像（stage 1 蓝条、stage 2 品红条 + 打开 MMU、stage 3 MMU 后 5 秒复位，无 panic 屏；payload 内核 `c9a70eb6…`） | `9fcb2f6c37cbd9071a1244057c4f47c17e40cd8e40436ad1994794b05252f045` |
| `userdata` | 正常 root 镜像（OS/SystemOS 窗口 + 签名策略 + 8 GiB Data seed 的 userdata 部分） | `6bfd1258c29aed02d56f3e5e882445aaacbc9f6dcd0da5b6cca2404304f36f0b` |

- 刷 userdata 用的是 sparse 包装镜像 `out/tb128fu/userdata/userdata-root.sparse.img`
  （9,019,771,760 字节，sha256 `5a597f4cf09214ad350f9a842ac4f29874313c3e70e99fd69468383631e12f57`），
  内容与 raw 镜像等价；12 个稀疏分块全部 `OKAY`，总耗时 224 秒。
- `misc` 已 `fastboot erase misc`；没有使用 `fastboot boot`，避免 `bootonce-bootloader` 循环。
- 8 GiB Data seed 通过 payload 的 `scratch-data.img` 提供（见第 10 节）。

## 3. fastboot 大镜像问题（重要 workaround）

`fastboot flash userdata <raw 9.46 GB>` 立即失败：

```
fastboot: error: Failed reading from userdata
```

原因（fastboot 37.0.1 源码 `fastboot/fastboot.cpp` 的 `copy_avb_footer`）：当 `partition_size >
image_size` 时，它用 `ReadFdToString` 把整个镜像读进 `std::string` 找 AVB footer；9.4 GB 读取失败。
**稀疏镜像（magic `0xed26ff3a`）会在函数开头直接 return**，跳过这个检查。

Workaround：把 raw 转成 Android sparse 格式再刷。转换逻辑：4 MiB 一块；全零块写 FILL
（`0xCAC2`，4 字节零），否则写 RAW（`0xCAC1`）。镜像头：

```
magic 0xed26ff3a, major 1, minor 0, file_hdr_sz 28, chunk_hdr_sz 12,
blk_sz 4096, total_blks 2308582, total_chunks 2255, checksum 0
```

`out/tb128fu/userdata/userdata-root.sparse.img` 就是这个转换结果；刷写命令：

```sh
fastboot flash userdata out/tb128fu/userdata/userdata-root.sparse.img
```

## 4. 启动观察

### 4.1 UEFI / DarwinBoot

屏幕先显示 Mu 固件日志，然后是我们固件的大字交接信息。关键行（照片转录，个别数字可能有误）：

```
UNIXNU: hand-off to the kernel
kernel   va 0xFFFFFFF00427C000  pa 0x8407C000
entry    pa 0x8B9F9400
args     pa 0x837E4000  window 0x80000000 bytes
video    base 0x5C000000  1200x2000  row 4800 bytes  32 bpp
entry words 140006F5 00000000 00000000 00000000
CurrentEL 1  SCTLR_EL1 0xC0180D  TCR_EL1 0x280803518
ID_AA64MMFR0_EL1 0x1122  (PARange 2, 4 KiB granule supported)
ID_AA64PFR0_EL1  0x1002222
Exiting boot services. A green bar across the top means the kernel entry code has run.
SecurityLock: LockType: SOFTWARE_LOCK, Module: 42857F0A-13F2-4B21-8A23-53B3F714BB40, Function: LockCapsuleInterface
```

- `kernel va 0xFFFFFFF00427C000` 是内核**最低段** `__PRELINK_TEXT` 的 VA，不是 `__TEXT`
  （`__TEXT` 是 `0xFFFFFFF007004000`）。DarwinBoot 的 `Kernel.Base` 取所有段的最小 vmaddr。
- `pa 0x8407C000`：窗口按 2 MiB 对齐后加上 `Kernel.Base & 0x1FFFFF`（0x7C000），这是
  DarwinBoot 的既定行为；pipa 的 `kernel.macho` 也有同样的 `__PRELINK_TEXT`
  `0xFFFFFFF00427C000`，所以“物理基址不是 2 MiB 对齐”本身不是本板独有的差异。
- `entry words` 第一个字是 `_start` 处的指令：`140006F5` 是**未打探针**的原始 `b _start_first_cpu`
  （+0x1BD4）；打了探针的内核这里是 `1400001E`（跳到 cave 1，+0x78）。这张照片来自没打探针的
  那次启动。刷机后可以用这一行确认探针是否在内核里。
- `SCTLR_EL1 0xC0180D`（UEFI 的映射仍开）、`TCR_EL1 0x280803518`、`ID_AA64MMFR0_EL1 0x1122`
  （PARange=2，40 位物理地址，4 KiB 粒度）、`ID_AA64PFR0_EL1 0x1002222`。
- 绿条是 DarwinBoot 的 `DarwinEnter`（`drivers/DarwinBoot/DarwinBootPkg/Driver/Entry.S`）在跳进
  内核**之前**画的，它只说明固件走到了交接，不说明内核跑了。

### 4.2 色条

入口探针的画法：帧缓冲顶部是 DarwinBoot 画的绿色带；下面是 1/16 高的条带，`band = stage`：

| 条带 | 含义 |
| --- | --- |
| 绿色 | DarwinBoot 画的（不是内核） |
| 蓝色 | stage 1：内核入口，MMU 关 |
| 品红/紫色 | stage 2：即将执行 `msr SCTLR_EL1` |
| （stage 3/4 不画条） | 仅等待后复位，用于判断是否走过了 MMU |

观察结果（有效的实验）：

- 正常镜像（`8d7be1f0…`，stage 1/2 + panic 屏，无复位）：绿、蓝、紫三条，之后**不再变化、
  不复位、没有 panic 屏**。
- 复位验证镜像（`374daea5…`，stage 2 只等待 5 秒再 PSCI 复位，不打开 MMU）：绿、蓝两条，
  约 5 秒后**重启**。→ `CNTPCT_EL0` 在 EL1 可读且在走，PSCI `SYSTEM_RESET`（SMC）可用。
- stage 3 镜像（`9fcb2f6c…`，stage 2 恢复为品红条 + 打开 MMU，stage 3 在 MMU 打开后等 5 秒复位）：
  绿、蓝、紫三条，几秒后**重启**。→ **MMU 打开成功**，打开后还能继续执行。

无效的实验（内核里没有探针，见文首更正）：`a789eacd…` 和 `2a1e9b35…`。`2a1e9b35…` 在真机上
只显示绿条，和“没有探针”一致；原记录里的“蓝条”“紫条”不可信。

## 5. 入口探针与实验

工具：`UniXNU-tb128fu/scripts/patch-xnu-entry-probe.py`（sheng 的探针脚本，payload 构建时
对内核副本打补丁）。原理：把入口/MMU 附近的指令替换成 `b` 到内核 `__TEXT` 尾部空 padding 的
cave；cave 画条、继续执行被替换的指令，或等待后 PSCI 复位。

本板内核的地址（由脚本的 `rebase` 从参考内核常量平移得到）：

```
LC_UNIXTHREAD pc / _start = 0xFFFFFFF0073F8488
delta = 0xFFFFFFF0073F8488 - 0xFFFFFFF00B9D0488 = -0x45D8000
cave 1 = 0xFFFFFFF0073F8500
cave 2 = 0xFFFFFFF0073F8560
cave 3 = 0xFFFFFFF0073F85C0
cave 4 = 0xFFFFFFF0073F8620
panic cave end = 0xFFFFFFF0073F8FF0
MMU enable (`msr SCTLR_EL1, x0`) ≈ 0xFFFFFFF0073F85xx（脚本用 `_start_first_cpu` 里的
唯一 SCTLR/ISB/CPACR 序列定位）
```

已做的实验：

| # | 镜像 | stage 2 cave | stage 3 cave | panic 屏 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 1 | `8d7be1f0…` | 画品红 + 执行 `msr` | 无 | 开 | 三条条带，之后不动，无 panic |
| 2 | `a789eacd…` | — | — | 关 | **无效**：探针没打进内核 |
| 3 | `2a1e9b35…` | — | — | 关 | **无效**：探针没打进内核，真机只有绿条 |
| 3' | `374daea5…` | 等待 5 s + PSCI 复位（不执行 `msr`） | （不会走到） | 关 | 绿+蓝，约 5 秒后重启 |
| 4 | `9fcb2f6c…` | 画品红 + 执行 `msr` | 等待 5 s + PSCI 复位 | 关 | 三条条带，几秒后重启 |

实验 3' 的 stage 2 cave 代码（只用于这次验证，脚本已恢复原版）：

```
mrs x1, cntpct_el0
ldr x2, =96000000          // 19.2 MHz × 5 s
add x2, x1, x2
3: isb
   mrs x1, cntpct_el0
   cmp x1, x2
   b.lo 3b
   movz w0, #0x0009
   movk w0, #0x8400, lsl #16
   mov x1, xzr; mov x2, xzr; mov x3, xzr
   smc #0
4: wfe
   b 4b
```

实验 3' 证明这段代码在真机上能用：计数器可读、在走，`smc #0` 的 PSCI 复位有效。

刷机前的自检：`payload.json` 里 `payload_kernel_sha256` 必须**不等于** `kernel_sha256`（否则探针
没打进去）；也可以用 `mcopy` 从 `payload.img` 取出 `kernel.macho`，用 `llvm-objdump -d
--start-address=…` 反汇编 `_start` 和 cave 核对。

## 6. 当前结论与未决问题

**已确认：**

- UEFI → UniXNU 菜单 → DarwinBoot → XNU 入口全部正常。
- 入口探针在真机可用：画条、计数器等待、PSCI 复位都能工作。
- 内核入口代码走过了 `_start_first_cpu` 的 bootstrap 页表构建，**MMU 打开成功**（实验 4）。
- 启动链的镜像（UFS verified root、owner 链、Data/trust cache）已刷入设备，但还没走到。
- `userdata` 已成功写入（sparse 方式），`boot_a` 可反复刷写，设备没有变砖。

**未决：**

- MMU 打开之后卡在哪里。实验 1 没有 panic 屏，可能是没有 panic，也可能是 panic 发生在内核
  映射帧缓冲之前（panic 屏要用内核自己的帧缓冲映射）。未打探针的内核也没有清屏画诊断页，说明
  没走到 `boot_progress_bind`。
- 交接文档原来列的 `arm_vm_init` 疑点（`gPhysBase/gVirtBase`、2 GiB 窗口、ADT `/chosen` 的
  `dram-base/dram-size`、PARange=40 位等）现在仍然适用，但它们属于 MMU 打开**之后**的
  `_arm_vm_init`，不是打开 MMU 这一步。
- 之后才是 UFS verified root / owner 链 / Data 挂载的真机验证。

## 7. 下一步建议（按优先级）

1. ~~让复位探针工作~~（实验 3' 已证明可用）。
2. ~~重跑 stage 3~~（实验 4：MMU 打开成功）。
3. **用 `--reset-after SYMBOL` 在 `_arm_init` 里二分**（stage 4 替代 stage 3）。`_arm_init`
   （`0xFFFFFFF0079D40F8`）约有 60 个调用，按顺序的关键点：
   `_PE_init_platform`(6) → `_boot_progress_init`(7) → `_thread_bootstrap`(19) →
   `_rtclock_early_init`(22) → `_kernel_startup_bootstrap`(24) → `_arm_vm_init`(33) →
   `_serial_init`(37) → `_kernel_startup_initialize_upto`(38) → `_PE_create_console`(48) →
   `_PE_init_platform`(51，第 2 次) → `_machine_startup`(61)。
   第一刀建议放在 `_arm_vm_init` 之后：它在中间，也是最可疑的。注意 `payload.py` 目前只支持
   `entry_probe_reset_delay`，还不能传 `--reset-after`，需要先加一个 board.toml 选项。
   （此段已过时：PAN 修正后 `_arm_vm_init` 已通过。另外 pipa 从未在真机上运行过，下面与 pipa 的对比没有参考价值。）
   `_arm_vm_init` 内的疑点：对比 pipa 的 `owned_memory_size=0x100000000`、
   `dram_size=0x980000000`（本板 `0x80000000`/`0x180000000`）；ADT `/chosen` 的 `dram-base`
   （0x40000000）与 `dram-size`（0x180000000）；本板 DRAM 在 0x7cc00000..0x80000000 有一个
   52 MiB 的洞，窗口本身在 0x8407C000..0x10407C000，不跨洞。
4. 如果以上都通过，再按 `HANDOFF-tb128fu.md` 第 11 节的顺序验证：
   `SHNGROOT` → `SHNGPOL1` → 策略验签 → 块设备发布 → root 授权 → SystemOS 缓存 → Data → launchd。

## 8. 临时改动清单（切换 agent 前必读）

当前工作树里有**为诊断做的临时改动**，恢复正常镜像前要处理：

1. `UniXNU-tb128fu/scripts/patch-xnu-entry-probe.py`：**已恢复**，和 `UniXNU-sheng`、`UniXNU-pipa`
   的原版逐字节相同。
2. `UniXNU-tb128fu/board.toml`（仍是临时改动）：
   - `panic_screen = false`（临时关掉，因为 panic 屏代码在启用 stage 3 时放不下，差 0x45 字节）；
   - `entry_probe_reset_delay = 5`（临时启用复位探针）。
   恢复正常镜像时改回 `panic_screen = true` 并删除 `entry_probe_reset_delay`。
3. `UniXNU/scripts/unixnu/payload.py`（通用改动，可保留）：
   - 新增 `entry_probe_reset_delay`：把 `--reset-delay N` 传给 `patch-xnu-entry-probe.py`；没设置时
     仍是 `--no-reset`；
   - 修正：只要 `panic_screen` 或 `entry_probe_reset_delay` 任一设置就打入口探针，`--panic-screen`
     只在 `panic_screen = true` 时才传。原来关掉 panic 屏会把整个探针一起跳过。
4. 设备仓库的其他文件（runtime VerifiedRoot、owner 链、boot progress、自检脚本）都是正常状态，
   见 `HANDOFF-tb128fu.md`。

## 9. 恢复与刷写命令

```sh
# 设备进 fastboot：长按电源 10–15 秒，关机后按住 音量下 + 电源

cd /Users/xvan/agents-home/UniXNU

# 刷 boot（当前诊断镜像）
fastboot flash boot_a out/tb128fu/boot/boot.img

# 刷 userdata（必须用 sparse 包装，raw 9.4 GB 会触发 fastboot 的 AVB 读取 bug）
fastboot flash userdata out/tb128fu/userdata/userdata-root.sparse.img

fastboot reboot
```

恢复正常启动镜像（诊断结束后）：

```sh
# 恢复 patch-xnu-entry-probe.py 的 SOURCES[2]
# board.toml: panic_screen = true，删除 entry_probe_reset_delay

export UNIXNU_UEFI=/Users/xvan/agents-home/uefi-tb128fu
export UNIXNU_IPSW=/Users/xvan/agents-home/UniXNU/out/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw
./unixnu defconfig tb128fu_defconfig -D ../UniXNU-tb128fu
./unixnu build kernel -D ../UniXNU-tb128fu
./unixnu build payload -D ../UniXNU-tb128fu \
  --unique-chip-id $(cat ../UniXNU-tb128fu/.cache/platform-seed) \
  --data-ramdisk build/owner-data-fixture/owner-data-8g.dbr --skip-setup \
  --trust-cache build/original-static-system-trust/original-static-system.bundle
./unixnu bootimg -D ../UniXNU-tb128fu \
  --dtb "$UNIXNU_UEFI/Resources/DTBs/tb128fu.dtb" -o out/tb128fu/boot/boot.img
```

## 10. 关键路径、哈希与值

| 项 | 值 |
| --- | --- |
| 正常内核 `kernel.macho` | 52,056,064 字节，sha256 `a696551b4f7186b43f560b5f7a1fa715639aff5a4b1dfb0a6dbabfb98f9c5adc` |
| 最后一次正常启动镜像 `boot.img` | `8d7be1f0ca2cf4fc3c5e94e0cdba27b15681fc0f79c98d28c4cc0f38f7c47480`（文件已被诊断镜像覆盖，按第 9 节重建） |
| 当前诊断镜像 `boot.img` | `9fcb2f6c37cbd9071a1244057c4f47c17e40cd8e40436ad1994794b05252f045`（payload 内核 `c9a70eb6…`） |
| userdata raw | 9,455,951,872 字节，sha256 `6bfd1258c29aed02d56f3e5e882445aaacbc9f6dcd0da5b6cca2404304f36f0b` |
| userdata sparse | 9,019,771,760 字节，sha256 `5a597f4cf09214ad350f9a842ac4f29874313c3e70e99fd69468383631e12f57` |
| Data seed（8 GiB，packed） | 1,028,224 字节，sha256 `2441dc72f052da43c25191b962ea84947026f9492fab5366caab15e18a41e255` |
| payload 里的 skip-setup seed | sha256 `56100a1d3010232dd5f9bee400fa0e1e62df890180cb0e9e8ffd6cc5ba2f9226` |
| trust cache pin | `7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7` |
| OS 卷 UUID | `04769D26-2CA0-4825-B443-E2E5F88300F2` |
| SystemOS 卷 UUID | `8846FE78-2910-4297-AF97-D59BB6F8FC16` |
| Data 卷 UUID | `73F1FA24-4189-446D-96D4-FB6324EBE955` |
| FD | 3 MiB，sha256 `01d70013876441f23d7f638b8c59ed600814501175e162bd2b3653807f698a0a` |

启动参数（payload 内）：

```
-v serial=3 debug=0x14e kt_fixed_zones=120 armcpu-diagnostics=1 sheng-pmgr=2
-no_slto_panic ml-timeout-lock-panic-scale=40 native-ufs-root=1
native-ufs-diagnostics=1 sheng-data-persist=2 boot-progress=2 sheng-build-tag=tb128fu-001
```

payload 内容：`kernel.macho`（52,056,064）、`kernel.macho.adt`（11,520）、
`kernel.macho.trustcache`（61,356）、`scratch-data.img`（1,261,728）。

刷前自检（在 `UniXNU-tb128fu` 目录）：

```sh
python3 scripts/check-boot-inputs.py \
  --trust-cache ../UniXNU/build/original-static-system-trust/original-static-system.bundle
```

正常镜像应输出 `PASS: boot inputs are internally consistent`。

## 11. 2026-10-22 上午的进展（PAN、自动回 fastboot、UFS SMMU）

### 11.1 二分结果与根因：PAN

| 镜像 | 探针 | 结果 |
| --- | --- | --- |
| `cc5a50ed…` | `_arm_vm_init` 之后等 5 秒复位 | 三条色条后不动（`a` 槽只用掉 1 次重试） |
| `897a958b…` | 调用 `_arm_vm_init` 之前立即复位（`--reset-before`） | 三条色条后不动 → 卡在 `_arm_init` 前半段 |

`_arm_init` 里调用 `_arm_vm_init` 之前有 `msr PAN, #1`。SM6225 的 CPU 是 Cortex-A73（`0xd09`）+
Kryo Silver（`0x801`，A53 内核），Android 下 `/proc/cpuinfo` 的特性只有
`fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid`，属于 ARMv8.0，没有 PAN，也没有 LSE。真正在硬件上跑过
iPadOS 的只有 sheng（SM8550）；**pipa 从未在真机上运行过，不能作为参考**。`patches/xnu/native-arm-pan.patch` 只在 `XNU_NATIVE_PAN=1` 时打开
`__ARM_PAN_AVAILABLE__`（注释写明“原始的 A53 目标没有 PAN”），`tb128fu_defconfig` 照抄了 pipa/sheng
的 `y`。

- 改为 `CONFIG_XNU_NATIVE_PAN=n`。这个开关以编译参数传入，XNU 的增量编译不会因此重编，所以把旧目标
  文件目录改名为 `out/tb128fu/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837.native-pan`（保留，可删），
  再完整重编（约 150 秒）。
- 旧内核里有 `msr PAN` 24 处、`mrs PAN` 14 处（`copyin`/`copyout` 系列、`_arm_init`、`_arm_init_cpu`、
  `_machine_switch_context` 等）；新内核为 0。整个内核本体（`__TEXT_EXEC`）和预链接的 iOS 驱动
  （`__PLK_TEXT_EXEC`）里都没有真正执行的 LSE/RCpc/v8.2+ 指令（扫描命中的几处都是代码段里的数据）。
- 以后的风险：iPadOS 用户态系统库可能用到 v8.1 指令（例如 RDM），走到用户态前要离线扫描 dyld 共享缓存。

改正后：`de298574…`（调用 `_arm_vm_init` 前复位）和 `ae0cc750…`（`_arm_vm_init` 之后 5 秒复位）都复位成功。

### 11.2 自动回 fastboot（A/B 重试计数）

- 探针复位是普通重启，会再次启动同一镜像。`a` 槽每次启动失败重试次数减一，7 次用完后
  `slot-unbootable:a:yes`，**这个 ABL 直接停在 fastboot，不会去启动 `b` 槽**（三次观察，`b` 槽重试次数始终是 7）。
- 所以“复位探针走到了”的判据是：重启后约 150–190 秒设备自己回到 fastboot，且 `slot-retry-count:a:0`。
  卡住时 `a` 槽只少 1 次，需要手动强制关机进 fastboot。
- **每次刷机后都要 `fastboot set_active a`**（重置为 7 次、可启动）。防止 ABL 某次改去启动 `b` 槽：`b` 里如果是
  Android，看到 userdata 不是自己的格式可能会格式化它。
- `board.toml [payload] timeout` 临时改为 2 秒（原 10 秒），缩短每次循环。
- 试过往 IMEM 重启原因（`0x0C12565C` 写 `0x77665500`）让 ABL 直接进 fastboot：无效（ABL 只看 PMIC 的重启原因，
  或者 PSCI 复位是硬复位、IMEM 被清掉）。代码已撤回。要立即进 fastboot 需要经 SPMI 写 PMIC `PON SOFT_RB_SPARE`，未做。
- 每轮用的脚本（`probe-round.sh BOOT_IMG [超时秒数]`：刷入、`set_active a`、重启、等回 fastboot，打印槽位状态）：

```bash
#!/bin/bash
# One bisection round: flash BOOT_IMG to boot_a, make slot a bootable (7 retries), reboot, and wait for the
# device to stop in fastboot again. A probe that resets boots the image again until slot a runs out of retries,
# so "back with slot a unbootable" means the probe point was reached; "not back" means the boot hung earlier.
set -u
img=$1
limit=${2:-420}
serial=HA1MTVNE
fastboot devices | grep -q "$serial" || { echo "device not in fastboot"; exit 2; }
echo "image $(shasum -a 256 "$img" | cut -c1-16)"
fastboot flash boot_a "$img" 2>&1 | grep -E "OKAY|FAILED|error" | tail -2
fastboot set_active a 2>&1 | grep -E "OKAY|FAILED|error"
start=$(date +%s)
fastboot reboot >/dev/null 2>&1
while fastboot devices | grep -q "$serial"; do sleep 1; done
if timeout "$limit" fastboot getvar current-slot 2>&1 | grep -q current-slot; then
  echo "RESULT: back in fastboot after $(( $(date +%s) - start )) s"
  fastboot getvar all 2>&1 | grep -E "current-slot|slot-(retry-count|unbootable):(a|b)"
else
  echo "RESULT: not back in fastboot within $(( $(date +%s) - start )) s"
fi
```

### 11.3 新的停止点：NativeUfs 的 SMMU 流映射

正常诊断镜像（`32006190…`：stage 1/2 色条 + panic 屏，无复位）在真机上走到 IOKit，诊断页出现，最后 panic：

```
UFS ROOT FAILURE: SMMU stream mapping readback
stage=SMMU readback result=e00002d8
HC cap=1587031f ver=00000300 hcs=0000010f hce=00000001 db=00000000 is=00000000
DMA=8ac69000 UTRL=0 SMMU smr=00000000 s2cr=0000002f cbar=00010000 sctlr=00000000
SMMU slot=49 id1=50000030 ... id2=5111 cba2r=1
@NativeUfs.cpp:1301  TCR=0x22a51a251a  SCTLR=0x34d4593d
```

驱动给 UFS 流在第 49 号 SMR 槽写映射，S2CR（指向第 47 号上下文）和 CBAR 写入生效，SMR 读回为 0。原因：
`drivers/NativeUfs/GccUfs.h` 的 SM6225 分支 `UFS_STREAM` 是从 SM8550 照抄的 `0x60`。主线 Linux
`sm6115.dtsi` 的 `ufshc@4804000` 是 `iommus = <&apps_smmu 0x100 0>`，本板活体设备树里其他设备的流 ID 和
`sm6115.dtsi` 一致（USB `0x120`、QUP `0xe3`），而且没有任何设备用 `0x60`。已改为 `0x100`（子模块里未提交的改动），
内核 `4c0d4429…`，镜像 `bb61827b…`，待真机验证。注意本板 Android 设备树的 UFS 节点没有 `iommus`；如果固件已经给
`0x100` 留了一条映射，驱动的规划逻辑可能报 `conflicting UFS stream mapping`，那是下一步要看的。

### 11.4 本节的配置/代码改动

- `UniXNU-tb128fu/configs/tb128fu_defconfig`：`CONFIG_XNU_NATIVE_PAN=n`（长期改动）。
- `UniXNU/drivers/NativeUfs/GccUfs.h`：SM6225 `UFS_STREAM 0x100`（长期改动）。
- `UniXNU/scripts/unixnu/payload.py`：新增 `entry_probe_reset_after`、`entry_probe_reset_in`、
  `entry_probe_reset_occurrence`、`entry_probe_reset_before`（传给探针脚本的 `--reset-after/--in/--occurrence/--reset-before`）。
- `UniXNU-tb128fu/board.toml`：`panic_screen = true`、`entry_probe_reset_delay = 0`（正常诊断镜像）；二分时把
  `panic_screen` 设为 `false`、`entry_probe_reset_delay` 设为非零，并设 `entry_probe_reset_after`。`timeout = 2`（临时）。

## 12. 2026-10-22 上午后段：进入 iPadOS 用户态、USB 取日志、自动回 fastboot

### 12.1 启动到了哪里

- UFS 流 ID 改成 `0x100` 后，根卷校验和发布成功，`launchd` 启动，约 100 个进程运行。
- 内存窗口 2 GiB 时空闲内存降到约 5 MiB；`owned_memory_size` 改为 `0x100000000`（4 GiB）后正常。
- `watchdogd` 在调用 IOKit 后 `brk #1` 中止，它退出会整机 panic；加 `sheng-disable-watchdogd=1`（与 sheng 相同）后
  系统稳定运行（最长一次 28 分钟，日志 803 轮无错误）。
- **SpringBoard 反复崩溃**：`FBSystemShellInitialize` → FrontBoardServices 断言 **`failed to find the main CADisplay`**
  （`brk #0`）。即系统里没有显示设备。sheng 的显示栈（`build-native-legacy-display.py`、`offline-legacy-*`、
  `CONFIG_XNU_NATIVE_LEGACY_DISPLAY`）还没有移植到 tb128fu，这是下一步。
- `liveactivitiesd` 在 `SessionSyncEngine` 里 Swift 断言失败（`brk #1`），次要问题。

### 12.2 USB 取日志（Linux 读取镜像）

- XNU 侧：`package/BootLog.c`（从 sheng 原样复制，加了 `[crashes]` 段）每 2 秒把诊断页、内核消息缓冲区、最近 3 次
  崩溃的 PC/LR/ESR/FAR/调用栈写到 userdata 最后 32 MiB（`sheng-log=1`，NativeUfs 的日志窗口）。
  `BootProgressStubs.c` 为 BootLog 读取的 GPU/显示/触摸符号补了零值占位，并删掉了旧的 `boot_log_tick` 占位。
- 读取镜像：`package/linux-log/build-log-reader.py --boot <原厂 boot_a.img> --out reader.img`。原厂内核
  `4.19.157-perf+`（备份在 `android-devinfo/devinfo-tb128fu-20261021-2244/firmware/boot_a.img`）加上一个不依赖
  C 库的 `init`（`package/linux-log/init.c`）。原厂内核没有 USB 串口、没有 devtmpfs：`init` 用 `mknod` 建
  `/dev/sda18`，把日志窗口拷进一个 FAT 镜像的 `LOG.BIN`，以**只读 U 盘**（configfs mass_storage，UDC
  `4e00000.dwc3`）交给 Mac；Mac 推出磁盘后 `reboot("bootloader")` 回到 fastboot（5 分钟超时也会回去）。
  `fastboot boot` 接受这个 20.8 MB 的镜像。
- Mac 侧：`UniXNU-tb128fu/scripts/read-tb128fu-log.py`：`fastboot boot` 读取镜像 → 等 `UNIXNULOG` 卷 →
  拷出 `LOG.BIN`/`STATUS.TXT` → 推出 → 等回 fastboot → 用 `uv run --python 3.13` 调
  `UniXNU-sheng/scripts/read-sheng-log.py --decode` 解码到 `decoded/`（`latest.txt`、`slots.txt`、`msgbuf.txt`）。
  输出在 `UniXNU/out/tb128fu/device-logs/<时间>/`。整个过程约 30 秒。
- 注意：每次 XNU 启动都会从头写日志窗口，**多启动一次就会覆盖上一次的日志**。手动进 fastboot 时要同时按住电源
  和音量下键，一直按到 fastboot 画面出现，避免多启动一次。

### 12.3 崩溃地址查符号

日志里的用户态地址落在 dyld 共享缓存里（记录中的 `+77b0000` 是共享缓存偏移）。做法：只读挂载 SystemOS
cryptex（`hdiutil attach -readonly -nobrowse -imagekey diskimage-class=CRawDiskImage out/ipsw/21H461/044-76034-038.dmg`），
对 `System/Library/Caches/com.apple.dyld/` 下的拆分缓存解析映射表、库列表、导出符号和 `.symbols` 内部符号，
找到库名和最近的函数；正式版的内部函数名是 `<redacted>`，需要时反汇编中止点前的代码，按 `adrp`/`add`
算出断言用的 CFString。本次用的两个脚本（`dsc_symbolize.py`、`dsc_dump.py`）在会话临时目录，尚未放进仓库。

### 12.4 自动回 fastboot（`package/BootReset.c`）

- 从 sheng 改写：sheng 是 v7 SPMI 控制器 + SDAM；SM6225 是 **v5 控制器**（APID 映射 core+0x900、所有权 cnfg+0x700、
  观察通道 0x10000×EE+0x80×APID、读写通道 0x10000×APID）+ **PM6125 PON 的 `SOFT_RB_SPARE`（0x88F）**，编码相同
  （第 7～1 位，bootloader = 2）。写入前检查：版本 v5、PPID 0x008 的通道归 EE 0、外设类型 PON 且为第 2 代；写后读回核对，
  再 PSCI `SYSTEM_RESET`。由 BootLog 的 `sheng-autoreset=<秒>` 触发。
- 只读探测（`tb128fu-004`）结果：`psci v10000 reset=0 reset2=-1 | spmi v50010000 apids=256 apid=141 owner=0 pon=01/04
  spare8f=00 ok=1`，全部符合。
- `tb128fu-005` 打开 `sheng-autoreset=180`：**已验证**。刷入后 208 秒自己回到 fastboot，`slot-retry-count:a` 只少 1，
  日志 180 秒 87 轮无错误。每一轮现在不用人碰设备：
  `probe-round.sh boot-005.img 900 && python3 scripts/read-tb128fu-log.py`（约 4 分钟）。内核卡死或 panic 停住时仍需手动进 fastboot。

### 12.5 本节的改动

- `UniXNU-tb128fu/package/`：新增 `BootLog.c`（来自 sheng，加 `[crashes]`）、`BootReset.c`、`BootResetCore.h`、
  `linux-log/init.c`、`linux-log/build-log-reader.py`；`BootProgress.c` 新增 `boot-crash-panic=<进程名>`（该进程崩溃即
  panic，用于没有日志时）和 `boot_progress_crash_text/count`；`BootProgressStubs.c` 见 12.2。
- `UniXNU-tb128fu/scripts/`：`build-boot-progress.py` 编译 BootLog/BootReset；新增 `read-tb128fu-log.py`。
- `UniXNU-tb128fu/board.toml`：`owned_memory_size = "0x100000000"`；`kernel_args` 加 `sheng-disable-watchdogd=1`、
  `sheng-log=1`、`sheng-autoreset=180`，标签 `tb128fu-005`。

## 13. 2026-10-22 中午：显示栈（QEMU 路线）、多核、CPU 频率、SpringBoard 的等待链

### 13.1 显示栈

- 按 UniXNU-qemu 的做法移植：`CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`，`scripts/build-native-legacy-display.py`（来自 QEMU，
  去掉 QEMU 专用部分；参考候选内核和观测数据从 `out/qemu/tree` 复制，复制前核对 IOMobileGraphicsFamily 的范围和字节、
  以及 `__DATA_CONST` 中 1406 个不同字都绑定同名内核符号）。`LegacyDisplayProbe.cpp`、`KeyStoreProbe.cpp`、`HidInputProbe.cpp`
  来自 sheng，`TouchState.cpp` 来自 QEMU。启动参数为 `offline-*` 一组加 `sheng-aks=1`、`sheng-touch-hid=1`。
- 与 BootLog 的符号冲突用弱符号解决（`BootProgressStubs.c` 用 `#pragma clang attribute push (__attribute__((weak)), ...)`，
  `LinkStubs.c` 的 `native_legacy_display_probe` 也改成弱符号）。
- 结果：SpringBoard 不再因 `failed to find the main CADisplay` 崩溃；显示 `stage=10`，backboardd 打开了
  IOMobileFramebufferUserClient，AppleKeyStore 替身被大量调用，崩溃 0 次。**但 `SWAP calls=0`，一帧都没画**，屏幕仍是启动页。
- backboardd（pid 57）的 user client 有 12 个选择子返回 `e00002c7`（不支持），尚未评估。

### 13.2 多核与 CPU 频率

- `sheng-smp=1` + `[payload.adt] cpu_regs = "0,1,2,3,0x100,0x101,0x102,0x103"`：8 个核都能启动（`SMP run=7`）。
- 012 发现开机慢的原因：A73 簇（domain1，`0xf523000`）停在 **300 MHz**，A53 簇在 1516 MHz。新增 `package/CpuFreq.c`
  （qcom,cpufreq-hw：0x000 使能、0x110+32×i 频率表、0x920 档位），在日志的 `[cpufreq]` 段报告两个簇；
  `tb128fu-cpufreq=1` 时，簇使能后把档位写到频率表最高一档。
- 013 结果：`d0 perf=6 (1900 MHz)`、`d1 perf=6 (2400 MHz)`，都已生效。开机时间减半：

  | 阶段 | 012 | 013 |
  |---|---|---|
  | 根卷校验完成、launchd 启动 | 141 秒 | 72 秒 |
  | 进程数到约 180 | 240 秒以后 | 约 110 秒 |

- 根卷校验每次都要做（NativeUfs 按块校验所读内容，约 3.5 GiB），频率提上去之后不再是主要耗时。

### 13.3 SpringBoard 的等待链

日志里 `STKx` 行是被追踪进程的线程调用栈（`boot-ui-focus*` 指定进程名），用 `dsc_symbolize.py <缓存目录> <cache+ 后的偏移> <地址...>`
翻译。013 的结果：

1. SpringBoard 主线程：`SpringBoardHome`（图标管理初始化）→ `ChronoServices` → `BoardServices` →
   `xpc_connection_send_message_with_reply_sync`，对端 chronod（pid 99）。与 QEMU README 第 2 环一致。
2. chronod 主线程：`ActivityKit` → `BoardServices` → 同步 XPC 请求，对端在 QEMU 上是 liveactivitiesd。
3. liveactivitiesd 没有崩溃（AppleKeyStore 替身起作用了），但主线程卡在 `SessionCore` → `SessionPushNotifications` →
   `CoreDuetContext` → Foundation 的同步 XPC 请求；另一条线程卡在 IDS 的 `_IDSCopyLocalDeviceUniqueID`。
   CoreDuetContext 的服务 `com.apple.coreduetd.context` 由 **contextstored** 提供（`/System/Library/LaunchDaemons/com.apple.contextstored.plist`，
   按需启动，用户 mobile）；IDS 由 identityservicesd 提供。
4. 014 追踪 `contextstored`、`liveactivitiesd`、`identityservicesd`：identityservicesd 主线程也卡在同一个 CoreDuetContext
   调用上（经 `DuetActivityScheduler`）；contextstored 主线程卡在 `CoreDuet` → `KnowledgeMonitor` → `CoreMotion` →
   `LocationSupport` 的 `CLConnection::sendMessageSync`，即在等 **locationd**。完整的链：

   ```
   SpringBoard → chronod → liveactivitiesd ─┐
                          identityservicesd ─┴→ contextstored → locationd
   ```

   sheng 在真机上遇到过同一个循环等待（`package/BootWatchdogPolicy.c` 的注释），用
   `sheng-disable-jobs=com.apple.identityservicesd,com.apple.locationd,com.apple.CommCenter,com.apple.bluetoothd` 让 launchd 不启动
   这些任务，服务查找就会失败返回而不是一直等。tb128fu 的 `BootWatchdogPolicy.c` 与 sheng 相同，015 加上了同样的参数。
5. 注意：`STKx` 行里的 `peer=` 只在线程正在发送时可信。线程处于只接收状态（`x1` 第 0 位为 0，`peer=.../e5`）时，推算出的
   端口可能不是对端（014 里 contextstored 显示 `peer=56`，即它自己）。以调用栈的含义为准。

另外：`fairplayd.A2` 和 `bluetoothd` 在反复重启（日志里几十次 exec）；bluetoothd 在 015 起被停用。

### 13.4 USB 取日志的一次故障（014 之后）

014 跑完后，读取镜像连续 4 次失败：Mac 上出现 "tb128fu log reader" USB 设备，但没有出现磁盘，约 1 秒后设备消失、平板回到
fastboot。按时间推断是读取程序给 U 盘挂日志文件这一步失败，随后它把"没有介质"当作"已被推出"而重启。槽位重试次数一直是 6，
即期间 XNU 没有再启动，日志没被覆盖。用户没有动设备，当时 Mac 熄屏了；Mac 亮屏后再试一次就成功了。原因很可能是 macOS
锁屏时不允许新接入的 USB 配件连接（隐私与安全性 →"允许配件连接"）：Mac 只看到了 USB 设备，没有把它作为磁盘接入。
做法：测试轮次期间用系统自带的 `caffeinate -d -i` 让 Mac 保持亮屏。

### 13.5 本节的改动

- `UniXNU-tb128fu/package/`：新增 `CpuFreq.c`；`BootLog.c` 加 `[cpufreq]` 段；`LegacyDisplayProbe.cpp`、`KeyStoreProbe.cpp`、
  `HidInputProbe.cpp`、`TouchState.cpp`、`BootUiTraceProbe.c`、`BootUiTraceCore.h`、`BootUiTraceDetails.h`、`BootProcessProbe.c`
  （来源见上）。
- `UniXNU-tb128fu/scripts/`：`build-boot-progress.py` 编译 CpuFreq、BootProcessProbe、BootUiTraceProbe；新增
  `build-native-legacy-display.py`。
- `UniXNU-tb128fu/configs/tb128fu_defconfig`：`CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`。
- `UniXNU-tb128fu/board.toml`：`[kernel] archives` 加 `build-native-legacy-display.py`；`[payload]` 加
  `skip_setup_args = ["--no-auto-dim"]`（`seed-data-skip-setup.py --no-auto-dim` 写入 SpringBoard 的不变暗、不睡眠设置）；
  `cpu_regs` 8 个核；`kernel_args` 加显示一组、`sheng-smp=1`、`tb128fu-cpufreq=1`、追踪参数、`sheng-disable-jobs=...`（015 起），
  `sheng-autoreset=300`。

## 14. 2026-10-22 下午：屏幕出画面，开始做触控

### 14.1 第一帧

015（`sheng-disable-jobs=...`）：第 132 秒画出第一帧，到 300 秒共 6 次刷新，崩溃 0 次。用户确认屏幕上出现了系统页面并显示时间，
触控还不能用。

### 14.2 触控硬件

- 面板是 BOE HX83102P 2K（原厂命令行 `msm_drm.dsi_display0=qcom,mdss_dsi_hx83102p_boe_2k_video`），触控是同一颗 TDDI 芯片里的
  **Himax HX83102E**（原厂日志 `Read driver IC ID = 83,10,2E`）。DT 里还有一个 `novatek@1` 节点，那是另一种面板配的芯片，这台没有。
- SPI：QUP 唯一的 wrapper（`0x4ac0000`）的 SE0（`0x4a80000`），片选 0，10 MHz，**SPI 模式 3**（原厂驱动覆盖了 DT 的 `spi-cpha`）。
  中断 GPIO 80（低有效，固件用下降沿），复位 GPIO 86。时钟、引脚、地址的出处见 `UniXNU-tb128fu/package/TouchBoard.h`
  （主要来自这台平板的 msm-4.19 内核源码 github.com/Roynas-Android-Playground/kernel_lenovo_tb128fu）。
- 芯片不带固件（zero flash）：每次复位后要由主机把 `Himax_firmware.bin`（128 KiB）写进芯片的 SRAM。

### 14.3 从平板取固件（`build-log-reader.py --vendor-file`）

- 读取镜像新增可选功能：`--vendor-file firmware/Himax_firmware.bin`（可多次）时，`init` 解析 super 分区的 liblp 元数据，找到
  `vendor_a`（单个连续区段，第 11554816 扇区起，1098544 扇区），用只读 loop 设备 + 只读挂载 ext4，把文件拷进 U 盘里的 `FIRMWARE.BIN`；
  `read-tb128fu-log.py` 自动拆到 `<日志目录>/vendor/`。全程只读。
- 结果：`Himax_firmware.bin`、`Himax_fifth_firmware.bin`、`Himax_mpfw.bin` 各 131072 字节，存放在 `UniXNU-tb128fu/.cache/firmware/touch/`
  （不进 Git）。日常读取镜像 `reader.img` 用同一份源码重编，旧镜像备份为 `reader-0948.img`。
- 命令：`python3 package/linux-log/build-log-reader.py --boot <原厂 boot_a.img> --out .../reader-vendor.img --vendor-file firmware/Himax_firmware.bin`，
  然后 `read-tb128fu-log.py --reader .../reader-vendor.img`。

### 14.4 触控驱动（`tb128fu-touch=<阶段>`）

- `package/HimaxProtocol.c/.h`：Himax 的 SPI 帧格式、AHB 寄存器读写、识别（复位、安全模式、读 `0x900000D0`）、固件下载
  （系统复位、主代码 64 KiB、配置区 17632 字节、握手、启动、按固件请求写 alg overlay 段）、读触摸报告（命令 0x30，68 字节）。
  协议依据：原厂内核里的 hxchipset 2.0.0.81（已反汇编核对）和两份公开的 GPL 源码。
- `package/HimaxProtocolTests.c`：在 Mac 上测试。用真实固件核对解析结果（配置表 `0x10400`、12 个分区、配置区 CRC `0xD269F185`，
  与原厂日志一致），并用模拟芯片走通识别、下载、读报告的完整流程：
  `cc -std=c11 -Wall -Wextra -Werror -I package package/HimaxProtocolTests.c package/HimaxProtocol.c -o /tmp/hx && /tmp/hx .cache/firmware/touch/Himax_firmware.bin`
- `package/TouchProbe.cpp`：仿照 sheng 的分阶段驱动。1 只读快照，2 打开 QUP 时钟并配置引脚，3 GENI SPI（FIFO 模式）+ 回环自测 +
  识别芯片，4 下载固件，5 读报告并交给 NativeHidInput。触摸中断不接 GIC：TLMM 的 GPIO 80 设为下降沿锁存但不使能中断，线程每 2 ms
  查询并清除锁存位。状态写进页面和日志的 `[touch probe]` 段。
- 构建：`scripts/build-native-legacy-display.py` 编译上述文件和 UniXNU 的 `drivers/NativeGeniSpi/GeniSpi.c`，并把固件生成为
  `native_touch_firmware` 数组；`LegacyDisplayProbe.cpp` 的 late probe 调用 `native_touch_probe()`。
- 坐标：面板像素 1200×2000（竖屏，与帧缓冲同向），换算成 NativeHidInput 的 0..30480 × 0..20320。

### 14.5 结果（016、017）

- 016：SPI 回环正确，芯片 ID `83 10 2E`，固件下载全部通过（主代码 CRC 0、配置 `D269F185`、overlay 0、握手 `22222222`、
  `0x90000048` 读回 `01EC`、`0x100072C0` 读回 `72C0`），固件版本 `D403`、面板版本 `42`、10 点，芯片状态 05。
- 017：**iOS 里触控可用，位置正确**（用户确认）。2148 次中断沿、2147 帧有效报告全部送进 NativeHidInput（HID 报告 2147 次，
  全部成功），57 次笔画覆盖整个屏幕。诊断页右下的小框是按 sheng 横屏比例缩小的整块触摸面，不是一比一位置。
- 问题一：**很卡**。整轮只换了 47 帧。采样显示 pid 43 的时间全在内核的 `vm_map_corpse_footprint_collect` /
  `vm_map_footprint_query_page_info`（崩溃进程生成 corpse 时逐页统计内存，开发版内核带锁断言，很慢），约占一半；launchd 反复重启
  崩溃的服务占了约四成。018 加了 XNU 自带的 `-no_corpses`（`osfmk/corpses/corpse.c`）。
- 问题二：**设置白屏**。iconservicesagent 反复崩溃（017 中 16 次，SIGSEGV，PC=0），调用栈 IconServices → IconFoundation → CoreUI →
  CoreImage → 空函数指针，与 QEMU 不带 `--metal` 时相同：没有 Metal 设备，CoreImage 退回 OpenGL ES 时拿到空上下文。
- 用户决定开始做 Adreno 610 GPU（Metal）。

### 14.6 后台进程反复重启（018、019）

- 018：`-no_corpses` 生效，但 launchd 仍占大部分 CPU 采样（015 起一直如此：015 为 80%，016 为 70%）。原因：420 秒内 373 次进程退出，
  多数是 `ns=1 sub=3`，即 jetsam 的 `JETSAM_REASON_VNODE`。tb128fu 的设备树没有 `kern.maxvnodes`，内核用了 BCM2837 配置的 1024
  （QEMU 遇到过同一问题）。`scripts/make-tb128fu-adt.py` 在 `/defaults` 加了 `kern.maxvnodes` = 管理内存 / 64 KiB + 1024（4 GiB 时为 66560）。
- 019：vnode 杀进程消失；剩下的循环是 PerfPowerServices（KeepAlive，退出码 1，143 次）、mount_hfs（94 次，调用者未查明）和
  iomfb_fdr_loader（49 次）。前两个之外的两项加进了 `sheng-disable-jobs`（020 起）。
- 注意：CPU 采样只保留最近 65536 个样本，日志在第 421 秒取，反映的是最后一段时间，不能代表用户操作时的情况。
- 启动参数总长 844 字符，XNU 上限 1024（`BOOT_LINE_LENGTH`），GPU 再加参数时要注意。

## 15. Adreno 610 GPU（2026-10-22 下午开始）

### 15.1 盘点结论

- sheng 的 GPU 栈：QuartzCore / CoreImage → NativeMetal 插件 → metal2vulkan（AIR → SPIR-V）→ Turnip（Mesa，带 Darwin 后端）→
  `AGXDeviceUserClient` → 内核 NativeAdreno → Adreno 740。板级上电在 `UniXNU-sheng/package/GpuPowerProbe.cpp`（`sheng-gpu-power`）。
  上层（IOKit 接口、Turnip 后端、metal2vulkan、前端、签名和 Data 种子工具）与 GPU 代数无关；GMU/HFI/RPMh 电源管理、a7xx 命令处理器
  初始化和环格式、SM8550 平台常量要换掉。Mesa 的设备表有 `GPUId(610)`（a6xx gen1 low）。
- A610 资料（调研文件在会话临时目录 `a610-facts.md`，主要出处为这台平板的 msm-4.19 内核与主线 drm/msm）：无 GMU，CPU 负责 CX/GX GDSC、
  Zonda PLL0、时钟；投票走 RPM（CX 与 GPU 共轨）；GPU 自带 SMMU（`0x59A0000`，SID 0 掩码 1，页表遍历非一致）；不支持 IO 一致性，
  没有硬件 APRIV；需要通过 TrustZone（PAS id 13）加载 `a610_zap` 才能退出安全模式；不要用 RBBM 软复位。
- 风险：zap 的 SCM 调用能否从 XNU 成功；CoreImage 即使有 GPU 也可能需要 QEMU 那样补 Metal 库支持；首次着色器编译慢。

### 15.2 固件（`.cache/firmware/gpu/`，不进 Git）

用 `build-log-reader.py --vendor-file` 从 vendor 分区取出：`a630_sqe.fw`（32264 字节，微码版本 0x197 ≥ 0x190）、`a610_zap.mdt` 和
`.b00`～`.b02`、合并版 `a610_zap.elf`（14384 字节，32 位 ELF，3 个程序段）。vendor 里还有 a620/a650/a702 的固件，与本机无关。

### 15.3 GPU 探针（`package/GpuProbe.cpp`，`tb128fu-gpu=<阶段>`）

1 只读快照（GCC 里 GPU 用的时钟、速度档位熔丝；GPUCC 只在其配置总线时钟已开时读）；2 以 320 MHz 上电（GCC 时钟 → GPUCC 时钟 → CX GDSC →
GX GDSC（复位脉冲、解钳位）→ PLL0 640 MHz → gfx3d RCG → 核心时钟），读 `RBBM_STATUS` 和常开计数器；3 只读查看 GPU SMMU；
4 TrustZone 查询；5 第一条 GPU 命令（GPU SMMU 上下文银行 0 + 三级页表、a6xx 基本初始化、SQE、命令环、`CP_ME_INIT` + `CP_MEM_WRITE`）；
6 加载 zap（PAS 13，放进 DT 预留的 `gpu_region@55615000`）+ `CP_SET_SECURE_MODE(0)` + 第二个特征值。状态写进日志的 `[gpu probe]` 段。

### 15.4 结果（020～023）

- 020：上电成功。速度档位 `0xEB`；PLL0 锁定（`MODE=e0000007`）；CX/GX GDSC 通电；`RBBM_STATUS=1`，常开计数器在走。GPU SMMU：
  4 组 SMR、**5 个**上下文银行，全部空闲（SMR 无效、S2CR 为 fault），`sCR0=0x00200406`、IDR2 输出 36 位。
  （同一轮因 `sheng-disable-jobs` 里的下划线导致 init 停住，已在 `BootWatchdogPolicy.c` 允许 `_`。）
- 021：TrustZone 接口可用（`IS_CALL_AVAIL(init_image)=1`，`PAS_IS_SUPPORTED(13)=0` 但不影响实际加载）；XNU 管理的物理内存为
  `0x8487c000`～`0x18487c000`，zap 预留区和帧缓冲都在范围外。
- 022：**GPU 执行了第一条命令**：读回 `0xC0FFEE01`，RPTR = WPTR = 13，无硬件错误、无 SMMU 页错误。zap 的 PAS init 连续一万次
  返回 INTERRUPTED。
- 023：TrustZone 调用期间屏蔽本核中断后，**zap 认证成功**（init 22 ms、auth 2 ms，无重试），`CP_SET_SECURE_MODE(0)` 后读回
  `0xC0FFEE02`，**GPU 已退出安全模式**。阶段 A/B 的硬件验证全部通过。

### 15.5 中断风暴（023 起调查）

- 每秒约 150 万～210 万次 IRQ 异常（单核时期每秒约 45 万次），几乎全部是 **27 号虚拟定时器**（ArmCpu 新增计数：023 中 8.8 亿次；
  空中断只有 77 次）。正常应为每核每秒几百次。会占用大量 CPU，也让 TrustZone 调用一直被打断。
- 同时 XNU **从未发出过核间中断**（`ipi=0 sent=0`）：ArmCpu 注释指出 XNU 在核收到第一个 IPI 之前会把它标为 SIGPdisabled，
  `cpu_signal()` 静默跳过；需要 `armcpu-self-ipi=1`（sheng 用的 `armcpu-spi-ipi` 依赖 1008 号以上的 SPI，本机 GIC 只有 544 个中断号）。
- 024：加 `armcpu-self-ipi=1`，ArmCpu 增加按核的定时器统计（处理后下一次到期还剩多少计数、少于 1000 计数的次数），日志 `[irq]`、`[irq core]`。
  结果：每核 1.2 亿～1.5 亿次"定时器"中断，但 XNU 处理后定时器都设在约 111 秒之后（从未设得过近），说明中断源不是 XNU 的定时器。
- **根因**：DT 的 timer 节点是 `<1 1>, <1 2>, <1 3>, <1 0>`（PPI 1/2/3/0），本机的 EL1 虚拟定时器是 **INTID 19**；ArmCpu 写死了 27，
  而 INTID 27 在 SM6225 上是另一个一直有效的信号。ArmCpu 新增 `armcpu-timer-intid`（默认 27，sheng/QEMU 不变），tb128fu 设为 19。
- 025：定时器中断从每秒约 210 万次降到约 2600 次，第一帧从第 126 秒提前到第 73 秒，TrustZone 调用不再被打断。
- **IPI**：SGI（`ICC_SGI1R_EL1`）在本机也收不到（与 sheng 的 SM8550 相同），XNU 因此从未发出 IPI（核一直是 SIGPdisabled）。
  ArmCpu 新增 `armcpu-spi-ipi-base`（默认 1008）；本机 GIC 只有 544 个 INTID，DT 用到 SPI 429，选 SPI 504～511（INTID 536～543），
  `armcpu-spi-ipi=1 armcpu-spi-ipi-base=536`。026：发出 953871 次、收到 952547 次，8 个核都能收到。
- 仍在的小问题：`sysstatuscheck` 每约 10 秒失败一次（找不到看门狗服务，因为停用了 watchdogd）；MobileStorageMounter 每 3～4 秒
  调一次 `mount_hfs` 并失败（目标未查明）。两者由 launchd 反复拉起，开销不大，暂未处理。
- 改动在共享的 `UniXNU/drivers/ArmCpu/ArmCpu.cpp`：计数（`armcpu_spurious*`、`armcpu_spi_handled*`、`armcpu_timer_ahead/short`、
  `armcpu_spi_ipi_check`）和两个新启动参数，默认行为不变。启动参数总长已到 925 字符（上限 1024）。

### 15.6 NativeAdreno 的 a6xx 分支（进行中）

- `NativeAdrenoRing`：`setGeneration(6)`：`CP_RB_CNTL` 置 NO_UPDATE；围栏为 `CP_EVENT_WRITE(CACHE_FLUSH_TS | IRQ)`；用户提交的前导为
  `CACHE_INVALIDATE` + CCU 深度/颜色失效，没有 BR/BV 框架和 7xx 上下文入口。
- `NativeAdrenoCpInit`：配置新增 `generation`（默认 0 = 7xx）；6 = `a6xx_cp_init` 的 8 字 `CP_ME_INIT`，不需要上电寄存器表。
- `NativeAdrenoIrq`：`setGeneration(6)` 使用 `A6XX_INT_MASK` = `0x03d0e242`。
- 主机端测试 `tests/test_ring.cpp`、`test_cpinit.cpp`、`test_irq.cpp` 各加了 a6xx 用例，7xx 原有用例照常通过。编译方式照
  `UniXNU-sheng/scripts/check-native-adreno-memory.py`（用一个把 IOKit 头文件指向 `XnuMemoryFixture.h` 的目录）。
- `NativeAdrenoSmmuContext`：页表遍历的缓存属性原来写死为"一致"（SM8550 的 GPU SMMU 节点有 dma-coherent），改为全局变量
  `native_adreno_smmu_coherent_walk`（默认 1，sheng 不变）。本机 GPU SMMU 没有 dma-coherent，tb128fu 设为 0；页表每次更新都会
  清到一致点（`publishForDevice`），所以非缓存遍历能看到更新。`test_smmuctx` 照常通过。

### 15.7 用 NativeAdreno 接管 GPU（`tb128fu-gpu=7`，027 起）

- 编译：新脚本 `scripts/build-native-adreno-a6xx.py` 生成 `build/native-adreno/libNativeAdreno.a`，只含 GPU 部分（上下文、内存、
  VM、命令环、提交、计时、用户客户端、发布、CP 初始化、中断解码）和 ArmSmmu4K、AdrenoPM4 两个工具包；sheng 的 GMU、HFI、
  `NativeAdrenoService.cpp` 不编。之后 `build-native-adreno-accelerator.py` 把延迟初始化的加速器类加进同一个归档。
  `tb128fu_defconfig` 打开 `CONFIG_XNU_NATIVE_ADRENO`。AdrenoPM4 改由这个归档提供，显示驱动归档不再单独编它。
  沙盒放行补丁 `zzw-native-adreno-sandbox-grant.patch` 和 GPU 附加卷挂载补丁本来就对所有板子生效。
- 第 7 阶段（替代 5、6，前 4 个阶段照旧）：
  1. 把 S2CR 0/1 改成纯 fault（`0x20000`，引导程序留下的值带多余属性位，工具包不接手），然后 `NativeAdrenoGpuContextBringup`
     （上下文银行 0、ASID 2、SMR 槽 0/1 对应 SID 0/1、2 GiB 地址空间，BO 池默认 384 MiB，`tb128fu-gpu-pool` 可改）。
     之后按 `arm_mmu500_reset` 关掉银行 0 的 ACTLR.CPRE 并刷新 TLB。
  2. 内核缓冲区（SQE 0x100000、环 0x200000、memptrs 0x300000、scratch 0x302000）用写合并映射给 CPU；拷 SQE（去掉第一个字）。
  3. A610 的 hw_init（主线 `a6xx_gpu.c` 的 a610 分支，取值和原厂 kgsl 一致）：GBIF、64 位地址、QSB、UCHE 窗口、ROQ、内存池、
     UBWC（1.0、最小访问 64 字节、最高 bank 位 14，`RB/TPL1/SP_NC_MODE_CNTL = 0xb`、`UCHE_MODE_CNTL = 0xa00000`）、挂起检测、
     中断掩码。**GMEM 窗口挪到 16 MiB**（Linux 用 1 MiB，那里正好是 NativeAdreno 的 SQE 缓冲区）。暂不做时钟门控和 CP 寄存器保护。
  4. 命令环（generation 6）→ `CP_SQE_CNTL=1` → `CP_ME_INIT`（等围栏）→ `CP_MEM_WRITE` 写 scratch 并读回。
  5. zap（沿用第 6 阶段的 PAS 加载）→ 经命令环发 `CP_SET_SECURE_MODE(0)`。
  6. GPU 中断：`armcpu_spi_attach(209)`（DT 的 SPI 177，电平触发）；处理函数先清 `RBBM_INT_0_STATUS` 再唤醒等围栏的线程。
  7. 提交对象、CP 常开计数器做 GPU 时钟、`native_adreno_publish`（设备信息：chip id `0x06010001`、GMEM 132 KiB @ 16 MiB、
     客户端 VA `0x20000000`～`0x80000000`、HBB 14、swizzle 7、UBWC 1）。
- 第 8 阶段 = 第 7 阶段 + 发布 Metal 加速器（照搬 sheng 的 `publishMetal`）。要等用户空间的 Turnip / metal2vulkan / 插件做好、
  能经附加卷装上之后再开，否则 Metal 会找到加速器却没有插件。
- 日志 `[gpu probe]` 扩到 16 行：第 10～13 行是第 7、8 阶段各步的结果，第 14 行每 2 秒刷新一次（中断数、围栏等待、提交数、
  用户客户端、BO 池），第 15 行是数据流映射槽位的读回值。
- 注意：`unixnu build` 用的是 `out/tb128fu/.config`（`unixnu defconfig` 复制出来的副本），改了 defconfig 要重新执行
  `./unixnu defconfig tb128fu_defconfig -D ../UniXNU-tb128fu`。027 第一次构建就是因此没链接 NativeAdreno。
- 027：系统照常运行 420 秒后自动回到 fastboot。BO 池 384 MiB 预留成功；上下文银行编程成功（`k0`，`p5e1`），但接数据流时
  工具包在第 1 阶段返回 Busy（`k0/10`、`r1v0`，整体 IOError），说明槽位 0/1 的 S2CR 写成 `0x20000` 后读回不是纯 fault。
  主线 `qcom_smmu_cfg_probe` 记录过某些高通固件会忽略 S2CR 的 fault 写入（adreno SMMU-500 的实现没有套用这个处理）。
  028 记录写入后的读回值，并在空闲的槽位 3 上照主线的方法先写 bypass 再写 fault 各读回一次。
- 028：S2CR 0/1 写 fault 后读回仍是原值（`0x001200ff`、`0x000209ff`）；槽位 3 写 bypass 读回 `0x000200ff`，再写 fault 不变。
  即本机固件代管 S2CR：**忽略 fault 写入，把 bypass 改成 fault**；translate 写入有效（022 写 0 读回 0）。
  修改共享工具包 `kits/ArmSmmu4K/ArmSmmu4KStreams.c`：接手槽位的前提从"S2CR 恰好等于 `0x20000`"放宽为"S2CR 类型为 fault"
  （其余字段不影响路由，接手过程中这个槽位依然不转发任何流量）。`test_streams.c` 加了正反两个用例；
  `check-smmu4k-streams.py`（含 Linux 源码对照，需在有 `.cache/adreno-source` 的 sheng 构建树里跑）通过。
  注意：工具包出错回退时会把槽位写回 fault，在本机固件上这一写入无效，回退只会把状态标为不可用。
- 029：**第 7 阶段全部通过**。SMMU 上下文就绪（两路数据流接到银行 0，5 个内核缓冲区已映射，ACTLR 3→1）；`CP_ME_INIT` 的围栏
  完成、`CP_MEM_WRITE` 读回正确（RPTR = WPTR = 27，无硬件错误，FSR 只有格式字段 0x400）；zap 认证 24 ms + 3 ms，经命令环退出
  安全模式；GPU 中断挂上后收到 2 次（`CACHE_FLUSH_TS`）；提交对象、GPU 时钟、设备发布都成功。显示和触控与 026 相同。
  新出现一次 audiomxd 崩溃（SIGTRAP，日志第 48 秒），以前各轮没有，是否与 GPU 有关待下一轮确认。
- 030：加用户客户端自检（照搬 sheng 的 `wrapperRound`，`tb128fu-gpu-selftest=0` 关闭）：新建客户端、第二个连接被拒、查询、
  设备信息核对、两个 BO 分配并映射（GPU 地址 `0x20004000`/`0x20005000`，CPU 写合并映射）、提交写 3 个值的 IB、等围栏、读回，
  关闭后再来一轮确认资源回收。结果在第 13 行 `uc w<通过>/<总数> e<首个错误>@<步骤> v<第 1 轮>/<第 2 轮>`。
  030 结果：第 7 阶段其余部分与 029 相同；audiomxd 没有再崩溃（029 那次应是偶发）。自检第一步新建客户端返回
  `0xe00002c7`（Unsupported）：发布的服务只接受 Adreno 插件的打开类型 `0x4147`，sheng 的测试写在这条限制之前，用的是 0。
  031 改用 `0x4147`。
- 031：**用户客户端自检全部通过**（两轮共 36 步全过，两轮读回都是 4/4），`tb128fu_gpu_stage_done = 7`。提交 2 次、完成 2 次；
  `[fence wait]` 里的 1 次超时是内层短等待（锁内等不到就放开锁再等，总共 86 µs），不是故障。GPU 中断 4 次。
  阶段 C 的主体（内核驱动接管 GPU、发布、用户空间的调用路径）到此完成。
- 待查：GPU 发布成功的三轮里有两轮出现后台进程崩溃（029 audiomxd SIGTRAP，031 geod SIGBUS，取指令时出错），019～028 都是 0。
  空闲内存与 026 相近（`free` 1287～2726），暂不能判断是否相关；要确认需要多跑几轮对照（例如 `tb128fu-gpu=4` 或
  `tb128fu-gpu-pool=0`）。
- 阶段 C 尚未做的部分：硬件时钟门控（省电）、CP 寄存器保护、GPU 频率仍固定 320 MHz、导入的普通（可缓存）内存的
  缓存维护路径（IOSurface 会走这条路，GPU 不是缓存一致的）还没有自检。
- 032（对照）：`tb128fu-gpu=4`（GPU 上电，不建 BO 池、不发布），同一镜像连跑两轮，看后台进程崩溃是否还出现。
- 033 起加入：
  - 导入内存自检（第 12 行 `imp w<通过>/<总数> e<错误>@<步骤> d<脏行>/64 r<正确字数>/1024`）：两页内核内存经 `BoImport`
    导入并映射；S 写新值且只在 CPU 缓存里（经非缓存映射读物理内存确认，`d` 越接近 64 测试越有意义），D 的旧行留在缓存；
    GPU `CP_MEMCPY` 把 S 复制到 D，提交后、等待前再读一遍 D；等完成后普通缓存读 D 必须 1024 个字全是新值。
    检验 `publishMapped`（提交前清缓存）和 `acquireImported`（完成后失效）在缓存不一致的 GPU 上是否有效。
  - 硬件时钟门控：原厂 `a612_hwcg_regs`（46 个 RBBM 寄存器，和主线一致）+ `RBBM_CLOCK_CNTL=0xAAA8AA82`；没有 GMU，
    只写 RBBM 这一组（与原厂 kgsl 相同）。`tb128fu-gpu-hwcg=0` 关闭。
  - CP 寄存器保护：主线 `a630_protect`（原厂 `a630_protected_regs`，32 段）+ `CP_PROTECT_CNTL=0xb`。
    `tb128fu-gpu-protect=0` 关闭。第 11 行末尾 `cg<RBBM_CLOCK_CNTL> prot<CP_PROTECT_CNTL>` 是读回值。
- 032（对照，两轮）：不发布 GPU，两轮都没有后台进程崩溃。合计：不发布的 12 轮 0 次，发布的 3 轮 2 次，样本还少。
- 033：panic `kfree: addr 0xfffffff5100f9000 found in kalloc type zone 'kalloc.type.var5.16384' but being freed to data`
  （类型堆分配的 16 KiB 块被当作数据堆释放）。崩溃发生在日志线程开始写 userdata 之前，日志里没有 033 的内容，只有屏幕照片。
  唯一新增的分配/释放是导入内存自检；自检自己的缓冲区（`IOMallocAligned` 0x2000 走 kmem，不经过 zone）和
  NativeAdreno 的 `IONewData`/`IODeleteData` 都配对一致，具体是哪个对象还未查明。导入自检改为
  `tb128fu-gpu-import=1` 才运行，发布 60 秒后在心跳线程里执行，每步记录步骤号（第 12 行 `imp p<步骤>`）并等 2.5 秒让日志写出。
  时钟门控和寄存器保护只写寄存器，与这次 panic 无关，但还没在平板上单独验证过。

### 15.8 同步上游（2026-10-22 傍晚，36 个提交）

- 主仓库快进到 `5709813`；`scripts/unixnu/payload.py` 一处冲突（我们的 `skip_setup_args` 与上游的 `--gpu-addon`），两边都保留。
  NativeAdreno、NativeMetal、NativeHidInput、NativeMobileDisplay、NativeRamBlock、DarwinBoot 更新到上游指定版本，
  NativeAdreno 的 a6xx 改动与上游改的文件不重叠，原样保留。同步前的全部未提交改动备份在会话临时目录，`git stash` 里也留了一份。
- 同步后 tb128fu 构建遇到的问题及处理：
  1. 构建树的 `.cache/xnu-work` 残留旧版补丁，新版补丁打不上：恢复被改文件、删掉补丁新建的两个文件，重新打全部 55 个补丁。
  2. 上游 `c4af0f0` 改写 `zzy-native-gpu-addon-mount.patch` 时漏掉了 `bindfs.h`（定义 `BINDM_FORCE_ROOT`）和 `bind_vnops.c`
     两段，而保留的 `bind_vfsops.c` 仍用这个宏，干净的源码编不过（上游的构建树大概还带着旧补丁的残留）。本地把这两段补回补丁。
     **需要报告给上游。**
  3. 上游 `5e782ac`：板级归档只有配置里有 `CONFIG_XNU_BUILD_ARCHIVES=y` 才会构建；已加进 `tb128fu_defconfig`。
  4. NativeMobileDisplay `c031d88`（面板刷新率，`sheng-vsync`）需要板级脚本声明第 301 个虚函数位并绑定两个原版函数；
     照 UniXNU-sheng 上游的 `build-native-legacy-display.py` 移植，`LegacyDisplayProbe.cpp` 加 `native_legacy_vsync_observed`。
  5. NativeHidInput `28f9b46`（电源和音量键）需要板级的 `native_buttons_set_sink`；本机还没有按键驱动，在
     `BootProgressStubs.c` 加了空的默认实现。
- sheng 那套 NativeAdreno 全量主机测试在上游新版 `NativeAdrenoMemory.cpp` 上编不过（测试用的模拟头文件缺 `IODirection`、
  `kIOMemoryPersistent`），是上游测试的问题；我们改过的 ring、cpinit、irq 测试和 SMMU 工具包检查都通过。

### 15.9 公开仓库

- `xVanTuring/uefi-tb128fu`（公开，默认分支 `tb128fu`，带 `unixnu` 标签）：tb128fuPkg、启动项、SEC 诊断三个提交。
- `UniXNU-tb128fu` 已在本地 `git init`，51 个文件；`.cache`（平板取出的固件、平台种子）和 `build` 不提交。读日志脚本的
  序列号改为环境变量 `TB128FU_SERIAL`，两个安装脚本的路径改为相对仓库位置。
- 上游文档 PR（`docs/tb128fu-port` 分支，会话临时目录的工作区）：中英文设备页、设备列表和侧边栏。
- `xVanTuring/UniXNU-tb128fu` 已公开（`main`，带 `unixnu` 标签）。

### 15.11 iconservicesagent 崩溃（034）与阶段 D（2026-10-22 夜）

- 034 的 iconservicesagent 崩溃与 017 完全相同（CoreImage +0x1b0ac4，经 IconServices → IconFoundation → CoreUI）。反汇编：
  `glDisable(GL_DEPTH_TEST / SCISSOR_TEST / DITHER / CULL_FACE)` 经空函数指针调用。没有 Metal 设备时 CoreImage 走 OpenGL ES，
  本机没有 GL 驱动。019～032 只是没有图标要渲染。已加入 `sheng-disable-jobs`（035：崩溃 0，缓存里没有的图标是空白）。
  符号化方法：SystemOS cryptex（`044-76034-038.dmg`，已挂载在 `/Volumes/DawnUpdate21H461...SystemCryptex`）里的
  dyld_shared_cache，崩溃记录里 `+xxxx` 是共享缓存的滑动量；脚本在会话临时目录 `dsc-images.py`、`dsc-read.py`。
- 阶段 D 进度：
  - 同步上游到 `98d4842`（含 `turnip-host-import-images.patch`），Mesa 源码 9 个补丁已打。
  - Turnip iOS（17.9 MB）和主机版构建通过；主机检查到 sheng 专用的 `check-quartzcore-turnip-cache-host.py` 才停，`plugin_smoke` 已生成。
  - NativeAdreno 测试桩 `tests/XnuMemoryFixture.h` 补 `IODirection`、`kIOMemoryPersistent`、弱定义 `proc_selfpid`（上游 cedcaa2/4fed9a1 后
    测试编不过，修好后 21 个测试阶段通过）——**可提 PR 给上游**。另：测试桩对 `kernel_task` 的导入直接 abort，即 NativeAdreno
    不支持导入内核内存，033 的导入自检用法本身不对，应删掉。
  - Vulkan 1.0 问题：插件 `applyBoardTurnipDebug` 读加速器属性 `NativeTurnipDebug` 写入 `TU_DEBUG`；tb128fu 第 8 阶段设
    `nocb,noconform`（Mesa 源码确认：noconform 时 a6xx 报 1.3）。
  - bridge（308 MB 静态库）、插件 bundle（37 MB）、adrenoprobed 构建通过；owner 信任包 61 KB；GPU addon 12.1 MB（9 个文件）。
  - 固件重编（含支持 GpuAddon 的 DarwinBoot `d099354`）；`make-tb128fu-adt.py` 加 `--gpu-addon`；种子工具加 `--plain-framebuffer`
    （backboardd 与全局域的三个 CA 偏好，mobile 和 root）；payload 72 MiB；启动参数 `tb128fu-gpu=8 sheng-gpu-addon=1`。
  - 插件用发布模式（`--publish`）：测试模式的回放是 A740 命令流（主机模拟按 A740），在 A610 上会卡住 CP。

### 15.12 启动速度、GPU addon 与 Metal 显示（036～040，2026-10-22 夜）

- 两台平板并行：TB128FU 序列号 `HA1MTVNE`（产品代号 `P89990JA1`），pipa 是 `68b7676d`，由另一个会话负责。所有 fastboot 命令都要带
  `-s`；`read-tb128fu-log.py` 只认 STATUS.TXT 第一行为 `tb128fu log reader` 的盘。039 曾因两边读取镜像同名（UNIXNULOG）互相拿错盘，
  039 的真实数据在 `out/tb128fu/device-logs/20261022-215800-from-pipa/` 和 `20261022-220930/`（含导出数据）。
- 启动慢：launchd 一启动就把 3 GB dyld 共享缓存整份读一遍、算 16K 和 4K 两层哈希（上游 `native-4k-signature-hash-cache.patch`，
  每次开机都做）。已改为每次读 1 MiB、32 MiB 以上的文件用 `IO_NOCACHE`，并加计时计数器（BootLog `[cs native4k]`）：
  读取 28.9 s、哈希 11.0 s，共 3141 MiB。首帧 96 s → 86 s（037，后半段不再缺内存）；前 42 s 没变，瓶颈在 NativeUfs：
  单请求槽、轮询、中转缓冲，每个约 112 KiB 的请求在驱动计时之外还多 0.7 ms。补丁改动未提交（共用 UniXNU 目录，pipa 也受影响）。
- GPU addon：上游 `zzy-native-gpu-addon-mount.patch` 建父目录时跳过了 `/private/var/ngpu` 本身，没跑过 add-gpu-plugin-data.py
  的种子全部写入失败（038：`files=9 failed=9 error=2`）。改一个字符修复（039：`written=9`，挂载 5.0/5.0）。待提 issue + PR。
- 039：插件在 backboardd 里加载并用 GPU 渲染（8 个命令缓冲各 27～186 ms 完成，133 个 BO、195 MiB）。**不是卡死**：
  WaitFence 的 2369 次"超时"是轮询（总等待 0.5 s）；界面空闲时只有状态栏时钟每分钟换一帧。问题在显示：驱动用 CPU 复制
  （76 ms），前 32 帧还要做两遍逐像素诊断扫描，源是不缓存的 GPU 内存 → 每帧 1.2～1.4 s。040 打开 `sheng-fb-gpu=1`（GPU 交接）。
- launchd 不按 LaunchDaemons 覆盖目录启动 addon 的任务：原版 IOMFB_FDR_Loader 照常运行，adrenoprobed 从没运行，已重新停用。
- 读取镜像（`reader-export.img`）现在能取回日志窗口下方的导出区（EXPORT.BIN，最多 32 MiB），脚本自动解开：039 的导出是
  backboardd 的 m2v 管线缓存（457 个文件），另有 `pipelines.log`、`prime.log`（预编译 8 个着色器库用了 52 s）。
- GpuProbe 第 8、9 行在第 7 阶段以后用来记录 CP 停住时的现场（RPTR、IB1/IB2 及剩余长度、SQE PC、SMMU FSR/FAR），
  只在环形缓冲区没走完且两次心跳间 CP 位置不变时写入。
- 启动参数上限是 **1000 字节**（不是 1024），040 用到 999。

### 15.13 GPU 提频：RPM 投票与 785 MHz（041～042，2026-10-22 深夜）

- 资料：原厂设备树（`android-devinfo/devinfo-tb128fu-20261021-2244/dt.txt`）有 GPU 频率表、总线向量、RPM/GLINK 节点；
  `gpucc-khaje.c` 等源码在会话临时目录 `scratchpad/src/`、`linux/`、`ds/`。要点：gfx3d RCG 固定 CFG 0x101，频率全靠把
  Zonda PLL0 的 VCO 设为 2 倍；alpha 按有符号 16 位（785 MHz：L 0x52、α 0xC556）。CX 档位按 gfx3d：320→0x40、465→0x80、
  600→0xC0、785→0x100、1025→0x180、1114.8→0x1A0；MX 按 VCO：≤1.8 GHz 0x40、≤2.4 GHz 0x80。GPU 总线档 → BIMC KHz = ib/8。
- `package/RpmGlink.cpp`：消息 RAM `0x45f0000`（末 256 字节 `grt0` 目录表，`ap2r`/`r2ap` FIFO），门铃 `0x0F111008` 写 1，
  轮询不用中断。042：目录 10 项，tx@0x200、rx@0x900 各 0x6f0 字节，指针全为 0，一次握手成功（RPM 的通道号 2）。
- 第 9 阶段（`tb128fu-gpu=9`，`dvfsPrepare`）：CX `swen`=1，CX `vlvl`=TURBO（活动和睡眠集合），DDR `clk2` KHz=最大
  （两个集合），全部成功后 PLL0 按 785 MHz 启动。**不碰** MX 和 RPM 时钟调节开关（`clk0` 2 `Enab`：Linux 先把所有 RPM 时钟
  投到最高才打开它）。041 没生效是因为探针里把阶段号截到了 8（已改）。
- 结果（040 的 320 MHz → 042 的 785 MHz）：命令缓冲平均 139→68 ms，重帧中位数 317→136 ms，使用中换帧间隔 174→110 ms
  （约 9 帧/秒）。内存复制 10754→11165 MiB/s，DDR 本来就快，带宽不是瓶颈。用户反馈"延迟好一些了"。
- 041 有一次 SpringBoard 卡在 `filt_wlwait_continue`（同步等某个服务），显示没交给系统；042 没复现，原因未查。
- 设置应用打开"辅助功能"闪退（042 两次）：与 iconservicesagent 同一处 CoreImage +0x1b0ac4（经 MobileIcons），空的 GL 函数表。
  Metal 设备只授予 backboardd（`NativeSandboxGrantProcess`），其他进程的 CoreImage 退回 OpenGL ES 后崩溃。需要让普通进程
  也拿到 Metal 设备（`make-gpu-addon.py --clients`，以及内核按进程放行），或给 CoreImage 一条不走 GL 的路径。
- 下一步：按负载调频（空闲回 320 MHz）、试 1025/1114.8 MHz（要再投 MX 0x80）；每次提交 13.8 ms 的 CPU 缓存维护
  （`GPUC`）；整屏复制上屏 92 ms 偏慢；普通应用的 Metal。

### 15.14 同步上游、设备专用子模块、流畅度（043～047，2026-10-23）

- 上游 PR：#85（issue，GPU addon 不建 `/private/var/ngpu`）、#86（修复）、#87（签名哈希 1 MiB 读取 + 不缓存 + 计时）。
- 同步：上游改写了 main 历史（丢掉 #67 的两个文档提交），本地 main 用 `git reset --keep` 对齐到 `261d754`；
  种子工具的 `--plain-framebuffer` 重新加回。备份在会话临时目录 `sync-backup-20261023/`。
- **设备专用子模块**：NativeAdreno、NativeMetal、NativeUfs、NativeMobileDisplay、ArmCpu、ArmSmmu4K 都在 xVanTuring 的 fork 上
  开了 `tb128fu` 分支，作为 UniXNU-tb128fu 的子模块（设备仓库同名模块优先，`feeds.resolve` 第 5 步）。共用 UniXNU 目录里这些
  子模块恢复为干净的上游版本，pipa 不受影响。**坑**：在设备仓库子模块里提交后，必须在设备仓库 `git add <子模块>` 登记新版本，
  否则 `unixnu build` 开头的 `submodule update` 会切回登记的旧版本（047 第一次构建就漏掉了 60 Hz 修改）。
- 调频器（043）：开机投 CX TURBO_NO_CPR、MX TURBO、DDR 最高，PLL 运行中切换（`setLevel`），每 1 ms 采样 RBBM_STATUS，
  50 次一窗：≥35 忙跳最高档，连续 10 窗 <8 忙降一档。切换 55～118 次、失败 0。
- 044：诊断叠加层默认关闭（`sheng-banner=1` 才画，日志行照常生成）；iconservicesagent、Preferences 拿到 Metal 设备
  （`NativeSandboxGrantProcess` 名单 + `make-gpu-addon.py --clients`），图标正常、辅助功能不再闪退；iconservicesagent 不再停用。
- 045：同步上游的 GPU 修复（系统内存渲染、GPU 写入导入不维护、提前结束 GPU 复制的换帧）；Turnip 选项改为板子给了就完全按板子
  （`NativeTurnipDebug`，tb128fu 目前 `sysmem,nocb,noconform`）。瓶颈确认在内核驱动：每次提交清约 89 MB（12 ms），
  分配/映射/释放 3～4 ms。A610 不是 IO 一致（kgsl 无 `ADRENO_IOCOHERENT`），上游的跳过只在一致平台生效。
- 046：`native_adreno_written_imports_owned=1`（NativeAdreno tb128fu 分支）：GPU 写入的导入只在映射时清一次。每次提交的维护
  16.8→0.72 ms，内核开销每帧约 75→14 ms，换帧间隔中位数 79→52 ms。稳定时帧间隔正好 41.7 ms：渲染服务按 24～60 Hz 的可变
  刷新范围降到了 24 Hz。
- 047：`sheng-vsync=60` + `sheng-vsync-fixed=1`（NativeMobileDisplay tb128fu 分支允许 60 Hz 固定），待测。
- 下一步候选：A610 上去掉 `sysmem` 让 Turnip 自选分块渲染（GMEM）；映射 1.7 ms、释放 1.4 ms（页表维护）；分配清零。

### 15.15 渲染服务的 CPU 瓶颈与大小核调度（048～058c，2026-10-23）

- 048：GMEM 分块渲染（`NativeTurnipDebug` 改为 `nocb,noconform`），没有可测的变化，也没有画面错误，保留。
- 049～051：每帧约 41 ms 的"等完成"来自交接线程（`gpuscanout::Client::serveOne` 提交复制后原地等）。但 W 行显示从收到一帧到
  复制完成只要约 13 ms，GPU 每帧大半时间空闲：**瓶颈在渲染服务的 CPU 端**，交接流水线化收益很小，没做。
- 052：映射/导入缓存（NativeMobileDisplay 保留最多 4 个客户端映射，`NGPSwap.reserved` 标出槽位；NativeMetal 每个槽位保留一个
  GPU 导入）：1966 帧里 1963 帧复用。
- 053：NativeMetal 的 U 行（渲染线程每帧 CPU 时间、提交时所在的核）：每帧 CPU 72.6 ms / 间隔 82 ms，57% 在 A53 上。
- 054：NativeMetal 采样器（从第 200 次带渲染的提交起每 1 ms 采渲染线程的栈，空闲 5 s 后写 `profile.txt`，随缓存导出）。
  分析脚本在会话临时目录（`profile-report.py`、`profile-callees.py`，符号用当轮保存的 dSYM 和 Turnip 的 dylib）。
  **30% 的 CPU 在 `supportsFamily:`**：Metal 计算纹理层信息时反复问，翻译层每次都核对整张家族表（`families::audit`）。
- 055：NativeMetal 缓存带参数的设备答案：每帧 CPU 62→53 ms。剩下的分散在翻译层画图路径（`draw_resident` 约 43%）、
  每帧申请 GPU 内存（`darwin_bo_init` 约 9%，其中生成缩略图时每次新建描述符池约 3%，`tu_cs_reserve_space` 约 6%）。
- 056（实验）：只启动 0 号 A53 + 4 个 A73：每帧 CPU 35 ms、间隔 54 ms，73% 在 A73 上。
- **大小核调度**：内核原来只有一个 CPU 簇（`MAX_CPU_CLUSTERS 1`，没有 `__ARM_AMP__`），调度器选空闲核时取编号最小的，
  渲染线程多半落在 A53。设备树给 CPU 节点加 `cluster-type`（A53 为 E、A73 为 P），内核改成两个簇 + Edge 调度器
  （`UniXNU-tb128fu/package/xnu-patches/native-smp-type-clusters.patch`）。没有 CLPC 时 Edge 让所有线程组默认留在启动簇，
  而 E→P 不允许迁移也不允许偷任务，所以补丁在所有簇就绪后（`sched_edge_cpu_init_completed`）按 CLPC 的正规路径
  （`sched_edge_tg_preferred_cluster_change`）给每个线程组设优先簇：固定优先级和实用工具以上的分时等级用 P，实用工具和
  后台用 E；之后新建的线程组直接用这张表。ArmCpu（fork `tb128fu` 分支 `55be7fd`）按节点的类型注册每个核，所有核注册完
  调用 `ml_cpu_init_completed()`（苹果的 AppleARMSMP 才调它，不调的话簇之间的迁移规则全是 0）。
- 058：8 个核、两个簇：**渲染线程的提交 100% 在 A73**，每帧 CPU 36 ms、间隔 53 ms，跟手变好，没有崩溃。
- **构建坑**：`build-xnu-4k-baseline.sh` 在 `make` **之前**编译开机诊断（BootProgress、BootLog 等），用的是**上一次**构建导出的
  内核头文件。改了影响结构体布局的配置（`board_config.h`）后，诊断代码会按旧布局读 `thread->machine.CpuDatap`
  （开大小核是 `0x1d8`，不开是 `0x1d0`）：057 卡死、057b 和 059 在 `boot_progress_irq` panic，都是这个原因，不是改动本身。
  **改了内核配置要连续构建两次**，并核对 `_cpu_number` 和 `_boot_progress_irq` 读同一个偏移。
- 059/059b（放弃）：不开大小核，只把对称调度下为空的 `sched_perfcontrol_edge_cpu_rotation_bitmasks_set` 补上（优先 A73、
  避开 A53）：开机停在 SpringBoard 早期（193 个进程后不再前进）。补丁和 ArmCpu 提交保存在会话临时目录 `smp-parked/`。
- 058c：同一个大小核内核、设备树不声明核类型（相当于 pipa、sheng）：同样停在 SpringBoard 早期（127 个进程）。所以这个补丁
  **只对 tb128fu 生效**：UniXNU 的 `build-xnu-4k-baseline.sh` 新增规则，应用 `kits/*/xnu-patches/*.patch`（设备包在构建树里
  是 `kits/tb128fu`），共用的 `patches/xnu` 不放它。这个脚本改动在主仓库未提交，以后给上游提 PR；pipa、qemu、sheng 的
  源码树检查过，没有残留临时补丁。
- 下一步：翻译层画图路径（每帧约 60 次绘制、每次约 0.37 ms，正常应在 10～20 µs）；在大核配置下重新采样后逐项处理。

### 15.16 帧节拍、帧率显示、自动划动、USB 实时日志（060～065，2026-10-23 下午）

- 060（大核配置下的采样，混合操作）：每帧 CPU 36.9 ms、间隔 53 ms。画图调用 47%，结束渲染过程 9%，提交 6%，换帧 5%，
  生成缩略图 5%（其中每次新建描述符池 4%），QuartzCore 自身约 12%。在 A73 上每次绘制约 0.24 ms。
- 061（等待采样：采样器也记录渲染线程在帧与帧之间的等待栈，`W` 行）：87.5% 的等待是渲染线程停在自己的 CFRunLoop 里等下一个
  触发。**渲染服务按 60 Hz 节拍出帧**，一帧 CPU 37 ms 赶不上第 2 个节拍，只能等第 3 个（50 ms）：帧率按 60/N 跳变，每帧 CPU
  降到 33 ms 以下就会从 20 帧跳到 30 帧。
- 062：帧率显示：交接复制器在每帧复制之后，用同一个命令缓冲把 320×32 的小框（`FPS n CPU n ms`，5×7 点阵放大 3 倍）复制到
  屏幕左上角，不闪烁；交接线程每 0.5 秒更新一次（前端 `native_bridge_metal_frame_cost`）。自动划动：`tb128fu-touch` 的第 8 位
  起是划动次数（`10245` = 阶段 5 + 40 次），画面出来 30 秒后在主屏幕中间来回横划，每次 300 ms、间隔 1.5 s。启动参数已用到
  997/1000 字节。
- 主屏幕翻页（062～065 自动划动）是最重的场景：每帧 CPU 约 48.5 ms、间隔约 76 ms。画图 39%，QuartzCore 18%，结束渲染过程
  13%，内核调用 11%，malloc 7%；新建 Vulkan 对象只占约 5%。
- USB 实时日志（`UniXNU-tb128fu/package/UsbLog.cpp`，阶段常量 `kStage`，启动参数没有空间放开关）：
  - 063（第 1 步，只读）：USB30_PRIM GDSC 已上电，主时钟 133.33 MHz（GPLL0÷4.5）、UTMI 19.2 MHz，7 个分支时钟都在运行；
    DWC3 `GSNPSID 0x5533330a`（3.30a）；femto PHY 是 8 位寄存器（32 位读出时字节重复）；**SMMU `sCR0.USFCFG=1`，没有表项匹配
    USB 的流 `0x120`**。
  - 064/065：PHY 上电顺序（含设备树的 3 个调校参数）、外围逻辑（UTMI 作时钟、强制 VBUS 有效）、核心软复位；SMMU：像 NativeUfs
    一样取一个空闲的上下文（从最高编号往下，避开 UFS 和 GPU 用的）设成直通 + 64 位地址，空闲表项把 `0x120` 指过去（这次是
    表项 0、上下文 46）；设备模式、1 ms 轮询事件、控制端点状态机（按 Linux `ep0.c`）、CDC-ACM 描述符（VID/PID 1209:0001）。
  - 结果：Mac 以高速枚举到 "TB128FU board log"，读完全部描述符；平板这边每个控制传输都正常走完（事件序列和传输描述符都对），
    **Mac 停在"新配件需要用户允许"**（`!registered`、`UsbEnumerationState = 2`）。
  - 065（重刷，用户在 Mac 上允许后）：出现 `/dev/cu.usbmodemtb128fu1`，**实时日志可用**：每 2 秒一份快照 + 内核消息新增内容，
    开机以来积压的部分先发完。接收：`python3 ../UniXNU-tb128fu/scripts/usb-log.py --quiet`（后台常驻，每次连接一个文件，
    最新完整快照在 `out/tb128fu/usb-log/latest.txt`）。
- 帧率显示在真机上确认可见，主屏幕翻页时在 20～30 帧之间跳。
- 翻译层补丁：UniXNU 的 `build-native-metal-bridge.py` 改为先应用 7 个既定补丁，再按文件名应用模块目录里多出来的补丁（pipa 用
  上游 NativeMetal，行为不变；补丁之间不能改同一个文件，`render.rs` 已被 0006 占用）。NativeMetal tb128fu 分支的 0008：
  描述符池复用（066：主屏幕翻页每帧 CPU 48.8→47.1 ms，生成缩略图从 4.4% 降到 1% 以下）。
- **LTO**：同一脚本新增 `NMB_FAT_LTO=1`（`lto = "fat"`、`codegen-units = 1`，只在设置时生效）。tb128fu 编插件时要这样调用：
  `NMB_FAT_LTO=1 python3 scripts/build-adreno-metal-plugin.py`（约 4 分钟；静态库 308→189 MB）。067：每帧 CPU 47.1→44.8 ms。
- **主屏幕翻页是 GPU 瓶颈**：一帧 11～17 个渲染过程、约 1200 万像素（多层模糊），1114 MHz 下提交到完成 80～130 ms，帧间隔
  一直 76 ms，CPU 降了 8% 也没变；交接复制排在后面，`done` 86～288 ms，换帧最长等 228 ms。列表滚动（设置）仍是 CPU 瓶颈
  （约 37 ms）。用户一直开着"减少透明度"，所以不是模糊造成的。

### 15.17 GPU 时间去哪了（068～075，2026-10-23 傍晚）

- 068：前端读每个命令缓冲的 GPU 时间（翻译层的时间戳查询；K 行 `gpu=`，V 行合计）。划动时 GPU 满频（1114 MHz）且忙碌度
  到 100%，5 个以上渲染过程的命令缓冲 50～130 ms。对比脚本：会话临时目录 `k-stats.py`（按类型统计 K 行）、
  `stroke-window.py`（第 1～40 次划动之间的换帧数、每帧 CPU/GPU）。
- 069 强制 GMEM、070 只给读帧缓冲的管线开光栅化顺序（补丁 0009）、071 强制系统内存：每百万像素 8.1 / 8.0 / 7.1 ms，
  都不是主因。tb128fu 保留 `sysmem`。翻译层原来给每个管线都开光栅化顺序，Turnip 因此在系统内存方式下进入单图元模式、
  自动选择只能选 GMEM（0009 保留）。
- 072/073：Turnip 追踪（`NativeTurnipTrace`，`TU_GPU_TRACEPOINT`；复制类事件的开关名是 `clear_blit`）。**一半 GPU 时间在
  渲染过程之外**：渲染服务的 1200×2000 IOSurface 每行 4864 字节（4800 字节像素），翻译层因为"图像会覆盖行间空隙"不肯直接
  建在缓冲区上，改用影子图像，每个渲染过程前从缓冲区复制进来（3 ms）、之后复制回去（2.1 ms）。追踪文件大了会让导出超过
  读取镜像的 32 MB 上限（导出被截断时可按 `SHEXR001` 格式手动取出前面的文件）；缓存目录跨开机保留，追踪关闭时前端会删掉。
- 074：桥接层把翻译层每条不重复的消息写进缓存目录的 `messages.log`（降级通知会写明原因），前端把每种 IOSurface 纹理记成
  pipelines.log 的 T 行。
- **075（补丁 0010）**：写入的纹理也允许覆盖行间空隙（Turnip 线性图像的布局转换不写内存，渲染只写到每行的对齐处）。
  075 那轮用户中途操作了两下，产生大量轻量帧，数据（GPU 30.9 ms/换帧、帧间隔中位数 33 ms）偏好，提交说明里写的就是它，
  **以下面的干净对照为准**。只有自动划动的整轮汇总（设备日志最后快照）：071（补丁前，系统内存）每次换帧 GPU 64.0 ms、
  每帧 CPU 41.7 ms、提交到完成 81.4 ms；077（0010，系统内存）47.4 / 41.1 / 54.3 ms；076（0010，GMEM）45.3 / 38.9 / 60.6 ms，
  划动期间帧间隔中位数 41 ms（071 是 50 ms）。两种渲染方式现在差不多，保留系统内存。比较时只用无人工操作的轮次。
- USB 实时日志偶尔会在开机后断开（077 在 58 秒断开，下一次开机自动恢复），这时只能用设备日志的整轮汇总。
- 还剩的：CoreAnimation 的渲染过程都带两个 BGRA8 颜色附件，大多数管线只写第一个，第二个每次全屏清空（系统内存方式下
  1.87 ms）；计算着色器约占 10%；交接给显示的复制每帧 3.2～3.5 ms。

### 15.18 逐次绘制追踪与 CoreAnimation 的帧缓冲读取（078～082，2026-10-23 晚）

- **测量方法改了**：自动脚本先从底部边缘上划解锁（`TouchProbe.cpp`），再在首页左右翻页。之前没人解锁时划动发生在锁屏上，
  各轮不可比；078 起只比较自动解锁后的轮次。079 是首页基准：帧间隔中位数 28 ms，每次换帧 GPU 30.0 ms，每帧 CPU 28.0 ms。
- 逐次绘制追踪（`NativeTurnipTrace "-all,+render_pass,+draw"`，现已关闭）加前端的绘制统计（追踪打开时写 `draws.txt`：管线标签、
  片段函数、顶点数）：两边每个着色器的绘制次数一致，可以把 Turnip 的着色器哈希对应到 CoreAnimation 的管线。分析脚本在会话
  临时目录 `gputrace-draws.py`。导出时缓存目录自己的文件排在着色器缓存前面，读取脚本会把截断的导出里完整的文件解出来。
- **CoreAnimation 的全部管线（约 2000 个）都用帧缓冲读取做混合**（`fixed_frag_lph_cph`，`fc_framebuffer_fetch:01`，固定功能
  混合关闭），一个全屏纯色填充要 6～21 ms。尝试让它改用固定功能混合都没成功：隐藏 `supportsProgrammableBlending` 等方法
  （080）、`supportsFamily:` 对 Apple1 回答不支持（081）、对全部苹果家族回答不支持（082），管线里仍然全是 `01`。
  QuartzCore（iPadOS 17.7.11）的 Metal 环境在 0x189562720 一带按 `supportsFamily:` 1001/1002/1003/1005/1007/2002、
  光栅化顺序组、分块着色器设置能力位（环境对象 +0xa64）；常量值取自着色器键第 0xd 字节第 5 位（0x18954efc8），写这一位的地方
  没找到。dyld 缓存在 IPSW 的 SystemOS 加密卷 `044-76034-038.dmg` 里（只读挂载），分析脚本 `qc-refs.py`。
- **082 保留**：`NativeMetalWithhold "apple-families"`（GpuProbe），前端对所有苹果家族的 `supportsFamily:` 回答不支持。
  CoreAnimation 因此不再走扩展亮度范围的合成（b3a8 目标上每次 63 ms 的绘制，以及带抖动噪声的转换，每次 31 ms），这块屏幕
  本来就显示不了。首页翻页：每次换帧 GPU 30.4→21.7 ms，提交到完成 33.5→20.0 ms，帧间隔中位数 27→25 ms、慢的 10%
  67→56 ms，画面和图标正常（用户确认）。叠加层显示最高 42 帧、最低 13 帧。**瓶颈回到渲染服务的 CPU（每帧 26.7 ms）**。
- 按用户的决定，帧率优化到此告一段落，转去做兼容性问题。帧缓冲读取这一项如果以后要继续，候选做法：在 Turnip 里对没有
  压缩的渲染目标把单图元模式从"重叠就冲刷并覆盖"改成"重叠就冲刷"，或继续找 QuartzCore 设置着色器键这一位的条件。
- 用户把设备文档的状态标为"iPadOS 17 可用性差（约 25～40 帧）"（fork 分支 `docs/tb128fu-usability`，未开上游 PR）。

### 15.19 设置图标与降低分辨率（083～087，2026-10-23 夜）

- **设置左侧图标只有底色**（照片：飞行模式、通知等只有彩色方块，App Store、通讯录、日历的 App 图标整个缺失，选中的"通用"
  和位图的"墙纸"正常）。根因大概率：这些图标经 iconservicesagent → CoreUI → Core Image 合成，Core Image 运行时生成 MSL
  并调用 `newLibraryWithSource:options:error:`（还有 `newDynamicLibraryWithURL:error:`），前端没有实现，翻译层也明确不支持
  （`NotImplemented(LibrarySource)`，只接受 AIR）。没有直接拿到 iconservicesagent 的报错：
  - 系统日志导出依赖探测守护进程 adrenoprobed，它借 `com.apple.bluetoothd` / `com.apple.iomfb_fdr_loader` 的名义启动，而这两个
    任务在 tb128fu 的 `sheng-disable-jobs` 里被禁用；launchd 读的是系统的任务缓存，附加包里的替换定义不生效（038/039 已见）。
  - 内核的 `[metal report2]` 只有一行，所有进程共用，客户端的报错会被 backboardd 的状态行覆盖。
  - 修复思路：预编译缓存（记录 Core Image 的源码 → Mac 上 `xcrun metal` 编成库 → 随附加包装上设备 → 前端按源码哈希加载；
    Mac 上没装 Metal 工具链，需要用户执行 `xcodebuild -downloadComponent MetalToolchain`），或调用设备上苹果的编译服务只做
    源码到 AIR。两者都需要先有"客户端进程把数据交给内核"的通道。
  - 导出顺序改为着色器缓存 `v1/` 之外的一切在前（系统日志目录 `zz-oslog/` 以后能导出时不会被截掉）。
- **降低分辨率（084～086）**：显示驱动报告的尺寸改为面板的 3/4（900×1500，`NativeLegacyDisplay.cpp` 的
  `kRenderNumerator/kRenderDenominator`，`startHardware` 安装渲染尺寸、面板尺寸单独保存），CPU 路径最近邻放大，GPU 路径
  用手写 AIR 的双线性放大内核（NativeMetal `shaders/scale_bgra.ll`，`tools/embed-air.py` 生成 `ScaleKernelAir.h`，
  `GpuScanoutClient` 用 `native_bridge_metal_library_with_air` 建管线，`PF … k1/N` 表示 N 帧经 GPU 放大）。触摸是归一化的，
  不用改。功能正常（画面铺满、触摸准确），每帧 CPU 26.7→22.5 ms，但放大内核每帧约 30 ms（直接写扫描缓冲区和先写 GPU
  内存再复制都一样，所以慢在读 IOSurface 导入内存，不在写），帧间隔中位数 25→33～36 ms，而且水平撕裂明显（单扫描缓冲、
  不与面板刷新同步；全分辨率时同样存在，只是不明显）。**087 起改回 1/1**，代码保留。以后可试：先把源图整块复制到 GPU 自己的
  内存再放大；按显示控制器的扫描行（INTF 行计数）对齐复制，消除撕裂。
- **087（全分辨率，当前状态）**：帧间隔中位数 23 ms（约 43 帧），慢的 10% 50 ms，66% 的换帧在 34 ms 内，每次换帧 GPU 20.5 ms，
  每帧 CPU 24.4 ms。比 082 略好，应主要来自合并的上游提交（显示交接线程提高到交互级优先级）。提交：设备仓库 0d55473，
  NativeMobileDisplay 530fd4c，NativeMetal fba9c3c。
- 用户决定 tb128fu 到此收尾，转去做 pipa（性能好得多）。

### 15.10 提给上游的 PR 和 issue（2026-10-22 晚）

| 仓库 | 编号 | 内容 |
| --- | --- | --- |
| UniXNU/UniXNU | PR #56 | 文档：收录 tb128fu 移植 |
| UniXNU/UniXNU | issue #57 / PR #58 | `zzy-native-gpu-addon-mount.patch` 漏掉 bindfs 两段。维护者用自己的 PR #63 修复并合并，#57 随之关闭；我们的 #58 已关闭。本地补丁文件已换成上游合并后的版本 |
| UniXNU/UniXNU | PR #59 | `build-native-metal-bridge.py --manifest-only` |
| UniXNU/UniXNU | PR #60 | `CONFIG_XNU_OWNER_CACHE_VOLUME_UUID`（SystemOS 卷 UUID 可配） |
| UniXNU/UniXNU | PR #62 | `[payload] skip_setup_args` 和 `seed-data-skip-setup.py --no-auto-dim` |
| UniXNU/UniXNU | PR #66 | `[payload] entry_probe_reset_*` |
| UniXNU/ArmSmmu4K | PR #1 | 接手 S2CR 为 fault 类型的槽位 |
| UniXNU/ArmCpu | PR #1 | `armcpu-timer-intid`、`armcpu-spi-ipi-base` 和中断诊断计数 |
| UniXNU/NativeAdreno | PR #4 | a6xx 代际（环、CP 初始化、中断）和非一致页表遍历 |
| UniXNU/NativeUfs | issue #1 | SM6225 常量、运行时根策略目录、仅探测构建、两条诊断路径：先问上游希望的结构 |

- 根策略目录的主仓库部分（`--root-policy`、`build-verified-root-key.py`、userdata 文档）依赖 NativeUfs 的读取部分，等 NativeUfs issue 有回复再一起提。
- **注意**：`unixnu build ...` 一开始会把子模块检出到主仓库记录的提交（`feeds.update_submodules`）。子模块如果停在自己的
  提交上（例如 PR 分支），会被切回去，工作区里的改动随之消失（2026-10-22 晚发生过一次，提交没丢）。做法：子模块的 HEAD
  保持在记录的提交，改动以未提交的形式留在工作区；PR 用单独的分支保存提交，需要时 `git diff HEAD <分支> | git apply` 恢复。
  现在 ArmCpu、ArmSmmu4K、NativeAdreno 就是这样：HEAD 在记录的提交，改动未提交，PR 分支另存。
  各 PR 的工作区在会话临时目录（`wt-docs`、`wt-fix`、`wt-manifest`、`wt-uuid`、`wt-skip`、`wt-probe`），审阅要改时直接在里面改。
- 034（同步后）：第 7 阶段、时钟门控、寄存器保护、用户客户端自检全部正常；但 iconservicesagent 崩溃 15 次（SIGSEGV，PC=0），
  019～033 基本没有，可能与同步进来的用户空间相关改动有关，待查。

- GPU 调频：每档频率要求 CX（以及 1 GHz 以上的 MX）电压先升到对应档位（`gpucc-khaje.c`：320 MHz 只要 CX 最低档）。
  本机经 GLINK（RPM 消息 RAM `0x45f0000`，邮箱 `0xF111000` 位 0，SPI 194）向 RPM 投票，资源 `rwcx`/`rwmx`，键 `vlvl`。
  要做的有：GLINK 传输和握手、RPM 请求、CX/MX（和内存带宽）投票、运行中切换 PLL、按负载调频。注意引导程序的投票也来自
  APPS，第一次投票就会覆盖它，投低了可能影响显示。速度档位 235 的频率表：320/465/600/785/1025/1114.8 MHz。
