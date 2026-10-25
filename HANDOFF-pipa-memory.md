# 交接：pipa 第三段 8 GiB 与 XNU 的多段物理内存

> 只读调研，2026-10-25，设备未参与。所有结论都能离线复核；代码没改、没上机。
> 主仓库路径相对 `/Users/xvan/agents-home/UniXNU`，设备仓库相对 `/Users/xvan/agents-home/UniXNU-pipa`，
> XNU 源码树是 `out/pipa/tree/.cache/xnu-work`（xnu-10063.141.1，BCM2837 机器配置，4 KiB 颗粒）。

## 0. 结论

1. **2026-10-24 那次 7.75 GiB @ 32 GiB 不是停在 `arm_vm_init`，也不是 pmap，而是停在
   `osfmk/arm64/start.s` 建引导页表的地方**：`create_bootstrap_mapping` 的 L2 页池用完，走进
   `Lbootstrap_tables_exhausted` 的 `b .` 死循环。那时 **MMU 还没开**，所以一个字节日志都没有。
   - 日志证据：`out/pipa/device-logs/20261024-112351/pstore/console-ramoops-0` 里 DarwinBoot 打出
     `kernel va 0xFFFFFFF00427C000 pa 0x80007C000` / `entry pa 0x80324C488` /
     `args pa 0x8042AC000 window 0x1F0000000 bytes`，之后**再也没有任何输出**（连 `[xnu]` 横幅都没有）。
   - 屏幕证据：只剩**蓝色 stage-1 条**（内核入口），**没有品红 stage-2 条**（`msr SCTLR_EL1` 之前那一刻）。
     `scripts/patch-xnu-entry-probe.py:8-14` 的 stage 定义：蓝=入口、品红=开 MMU 前。
     两个探头的位置：`START_FIRST_CPU+0x228` 是死循环，`START_FIRST_CPU+0x2D4` 才是品红条
     （`patch-xnu-entry-probe.py:41-44` 与 `start.o` 反汇编一致）。品红没出现 = 死在两者之间。
   - 算账：7.75 GiB 从 32 GiB 起，1 GiB 一张 L2 → 每张映射 8 张 L2；V=P 与 KVA 两张映射 = 16 张，
     加 2 张 L1 = **18 页**；池子只有 16 页，可用 L2 只有 **14 张**（见 §1.3）。
2. **和"基址在 32 GiB"无关**：同样 7.75 GiB 放在 4 GiB 基址（0x10007C000）也要 18 页，一样会死。
   `board.toml` 里 "a physical base at 32 GiB is not handled yet" 这句会误导人，第一道门是页表池预算。
   现有 5.75 GiB 配置用掉 14 张里的 12 张（**只剩 2 张余量**），所以再加 2 GiB 就爆。
3. **多段的底子比预想好**：`phystokv()` / `phystokv_range()` / `ml_static_vtop()` 本来就是查表实现，
   表长 `PTOV_TABLE_SIZE = 8`（现有配置只用 2 项）。真正假设"一段连续"的只有 pmap 的
   pv_head/pp_attr/`pa_index`、`pmap_next_page`、`pmap_free_pages_span`，以及 `vm_pages[]` 的
   index↔ppnum 线性关系。位宽问题（32 位截断）已经不是主要矛盾，见 §1.5。

## 1. 事实

### 1.1 内存布局与 XNU 现在拿到的窗口

| 项 | 值 | 来源 |
|---|---|---|
| bank 1 | `0x80000000 + 0x37700000` | `UniXNU-pipa/board.toml:157-161` |
| bank 2 | `0xc0000000 + 0x1c0000000`（7 GB），其中 `0xffc20000..0x27fffffff` 约 6 GiB 空闲 | 同上、`:272-276` |
| bank 3 | `0x800000000 + 0x200000000`（32 GiB 起，8 GiB），顶端 `0x9ff...` 是固件镜像 | 同上 |
| XNU 现用窗口 | `0x1042B8000`，`0x170000000`（5.75 GiB），取在 bank 2 | 日志 `20261025-111715` |
| 失败那次 | `0x8042AC000`，`0x1F0000000`（7.75 GiB），取在 bank 3 | 日志 `20261024-112351` |
| `dram-base` / `dram-size` | `0x80000000` / `0x980000000`（覆盖到 40 GiB），只喂 `is_dram_addr()` | `make-pipa-adt.py:27,152-153`；`arm_init.c:636-657`、`osfmk/mach/arm/vm_param.h:302` |

### 1.2 ADT / payload / DarwinBoot 一侧：只有一个窗口

| 位置 | 作用 |
|---|---|
| `UniXNU-pipa/scripts/make-pipa-adt.py:27` | `DRAM_BASE = 0x80000000` 硬编码；`dram-size` 由 board.toml 传入 |
| 同上 `:74-77` | `--dram-size`、`--owned-memory-size` 两个参数 |
| 同上 `:102-105` | owned 必须是 256 MiB..8 GiB、2 MiB 对齐（所以**单段上限就是 8 GiB**） |
| 同上 `:150-151` | `/chosen/darwinboot-memory-size`（u64）——DarwinBoot 唯一的窗口请求 |
| 同上 `:165-167` | `/chosen/memory-map` 下的 16 字节占位（RAMDisk/PackedRAMDisk、TrustCache、GpuAddon），loader 填 `{addr,size}` |
| `UniXNU/scripts/unixnu/payload.py:144-150` | 把 board.toml `[payload.adt]` 翻译成上面那条命令行（配置入口在这里） |
| `drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:239-242,263-267` | 解析 `darwinboot-memory-size`；**重复出现即 `EFI_COMPROMISED_DATA`** |
| 同上 `:401-453` | `AllocateWindow`：遍历 UEFI map，取**第一个**装得下的 `EfiConventionalMemory` run（`:329 RunEnd` 会把物理相邻的 descriptor 合并），`:434` `AllocatePages`；失败时在屏上打 `NO WINDOW`/`FREE` |
| 同上 `:602` | 只调用一次 `AllocateWindow` |
| 同上 `:621-631` | boot_args 只填一组 `PhysicalBase`/`MemorySize`/`MemorySizeActual`/`TopOfKernelData` |
| 同上 `:21,432` | `BLOCK_SIZE = SIZE_2MB`；`Candidate = ALIGN_UP(run,2MiB) + (Kernel->Base & (2MiB-1))` —— 故意让窗口基址与内核 VA 在 2 MiB 内同余，`start.s` 的 2 MiB block descriptor 才能当恒等映射用 |
| `drivers/DarwinBoot/DarwinBootPkg/Driver/BootArgs.h:8-31` | `DARWIN_BOOT_ARGS`（= 内核 `boot_args`，`STATIC_ASSERT` 校验到 0x480） |
| `.../Driver/Entry.S:10-52` | `DarwinEnter`：画绿条 → 刷 cache → 关 MMU → `br` 到内核入口 |

结论：**DarwinBoot 就是"一个连续窗口"**。要传多段，ADT 侧的扩展点有两个，都不用动二进制格式：
(a) `/chosen` 再放一个 `darwinboot-memory-size-N`（每段一个）；
(b) `/chosen/memory-map` 放 `DramWindowN`（16 字节占位），让 DarwinBoot 像 TrustCache 那样把
`{addr,size}` 写回树里（`DarwinBoot.c:637-638` 的现成写法）。
内核侧读 `memory-map` 的通道也是现成的（`arm_vm_init.c:1280-1320` 已经在读 `TrustCache`，
结构是 `pexpert/pexpert/device_tree.h:294-298` 的 `DTMemoryMapRange { vm_offset_t paddr; size_t length; }`）。

### 1.3 内核启动链（file:line）

| 阶段 | 位置 | 读/算什么 |
|---|---|---|
| 建引导页表 | `osfmk/arm64/start.s:479-618` | x22=virtBase, x23=physBase, x24=memSize；只覆盖**内核所在窗口** |
| 池子边界 | `start.s:420-456`（`create_bootstrap_mapping`）；`:612-618` 两次调用；`:621-624` 死循环 | 每张 L2 一页，超出 `BOOTSTRAP_TABLE_BYTES` 就 `b .` |
| 池子大小 | `osfmk/arm/pmap/pmap.h:117-125` | ARM_LARGE_MEMORY=34 页；**native 4K=16 页**；Apple=8 页 |
| 平台初始化 | `osfmk/arm/arm_init.c:372-373` | `PE_init_platform(FALSE)` + `boot_progress_init()`；此时 SecureDT、`PE_state.video` 已可用 |
| 页大小策略 | `arm_init.c:407-431` | native 4K 分支只看 `ARM_PGSHIFT`，与 RAM 大小无关 |
| CPU 数据结构物理地址 | `arm_init.c:457` | `cpu_data_paddr = physBase + (VA - virtBase)` |
| 进 VM | `arm_init.c:534-536` | `arm_vm_init(xmaxmem, args)` |
| VM 基址/大小 | `arm_vm_init.c:1836-1854` | `gVirtBase/gPhysBase`；`mem_size = ((gPhysBase+args->memSize)&~PAGE_MASK) - gPhysBase`；`mem_actual` |
| 内存过大检查 | `:1862-1866` | 只有 ≥ (VM_MAX-VM_MIN)/2 = 56 GiB 才 panic（7.75 GiB 不会） |
| physmap 基址 | `:1872-1879` | `physmap_base = phystokv(topOfKernelData)` + 随机 slide ≤ 1 GiB |
| static/physmap 末尾 | `:1895-1896` | `static_memory_end = physmap_base + mem_size + PTOV_TABLE_SIZE*TWIG` |
| 第一页 | `:1947` | `first_avail_phys = avail_start = args->topOfKernelData` |
| 托管末尾 | `:1964-1970` | `avail_end = gPhysBase + mem_size`（**一段**） |
| physmap 映射 | `:2150` → `:1490-1519` `arm_vm_physmap_slide`；非 XNU_MONITOR 只有两段：`:1601-1602`（内核以下）、`:1604-1605`（DT/ramdisk/args）、`:1607-1608`（其余 physmem） | 每段写一条 ptov 项，`pa = orig_va - gVirtBase + gPhysBase` |
| ptov 表 | `:369` `PTOV_TABLE_SIZE 8`；`:375-389` `phystokv()`；`:391-407` `phystokv_range()`；`:408-427` `ml_static_vtop()` | **查表实现**，未命中才回落到线性算式并 panic("illegal PA") |
| 进 pmap | `:2256` | `pmap_bootstrap(dynamic_memory_begin)` |
| L3 预算 | `:2266-2271` | `mem_segments = (mem_size + 0xFFF FFFF) >> 28` |
| lowglo | `:2381` | `patch_low_glo_static_region(topOfKernelData, ...)` |
| pmap 的"一段" | `osfmk/arm/pmap/pmap.c:2301-2302` | `vm_first_phys = gPhysBase; vm_last_phys = trunc_page(avail_end)` |
| pmap 表大小 | `osfmk/arm/pmap/pmap_data.c:618,634` | `npages = atop(mem_size)`；pv_head/pp_attr 各一页一项 |
| 索引 | `osfmk/arm/pmap/pmap_data.h:77-101` | `pa_valid()`、`pa_index() = atop(pa - vm_first_phys)` |
| 早期页分配 | `pmap.c:2655-2660` 注释"no gaps"；`:2703-2720` `pmap_free_pages/_span`；`:2733-2755` `pmap_next_page` | 线性游标 `first_avail → avail_end` |
| vm_page 数组 | `osfmk/vm/vm_resident.c:1449,1454,1554-1585` | 按 span 算 npages，按 ppnum 递增顺序 init；`VM_PAGE_GET_PHYS_PAGE` = 下标 + `vm_first_phys_ppnum`（`vm_page.h:423-440`） |
| static free | `osfmk/arm64/machine_routines.c:1893` `ml_static_mfree` | ppn → `ptoa()`，64 位安全 |
| booter 余量 | `osfmk/arm/machine_routines_common.c:1168-1186` | `memSizeActual - memSize`（这里是 0） |
| DRAM 属性 | `arm_init.c:636-657` | `/chosen` 的 `dram-base`/`dram-size` → `gDramBase/gDramSize`，只给 `is_dram_addr` |
| memory-map 读取范例 | `arm_vm_init.c:1280-1320`（`arm_vm_prot_init`） | 读 `chosen/memory-map` 的 `TrustCache`，`DTMemoryMapRange` |

`BOOTSTRAP_TABLE_SIZE` 的三个分支（`pmap.h:113-125`）与本板实测：

| 场景 | 每张映射的 L2 页 | 两张映射 + 2 L1 | 池 16 页 | 结果 |
|---|---|---|---|---|
| 4 GiB @ `0x10007C000` | 4 | 10 | ✓ 余 6 | 正常（历史配置） |
| 6 GiB @ `0x10007C000` | 6 | 14 | ✓ 余 2 | 未上过机，理论可行 |
| **5.75 GiB @ `0x1042B8000`（现在）** | **6** | **14** | **✓ 余 2** | 正常 |
| **7.75 GiB @ `0x80007C000`（失败那次）** | **8** | **18** | **✗ 差 2** | **死在 `b .`** |
| 7.75 GiB @ `0x10007C000` | 8 | 18 | ✗ | 同样会死（与基址无关） |
| 8 GiB @ 任何 2 MiB 对齐基址 | 8~9 | 18~20 | ✗ | 需要更大池 |

（页数由 `start.s` 的循环逐条复算：先按 `(base & ARM_TT_L2_INDEX_MASK)>>21` 定第一张表还能装几条，
之后每条 1 GiB 一张。反汇编 `start.o` 确认常量：池尾 `x25+0x10000`（16 页）、
第一张 L2 从 `x25+0x2000` 起 → **可用 L2 恰好 14 张**、`0x2284: b 0x2284` 就是死循环。）

### 1.4 "停在蓝条"精确定位

- 该轮 = `20261024-112351`（`git log` 里对应 `6bcadcd feat(memory): 5.75 GiB for XNU` 的说明：
  "the kernel stopped after the blue entry bar without writing a log"）。
- 蓝条是 `patch-xnu-entry-probe.py` 的 **stage 1**（`_start`，MMU off）。品红 stage 2 的补丁点在
  `START_FIRST_CPU+0x2D4`（`msr SCTLR_EL1, x0` 之前），死循环在 `+0x228`。
  **只有蓝条 ⇒ 内核入口跑过、引导页表没建完 ⇒ 没有到 arm_vm_init、没有到 pmap_bootstrap、bootloader 也没失败。**
- 这也解释了"没有日志"：`boot_progress_stage()` 在 `kits/pipa/BootRamoops.c` 里先写内存缓冲，
  直到 `boot_progress_start()`（`clock_service_create` 之后）才把缓冲区拷进 ramoops 区
  （`BootRamoops.c:1129-1140,1172`）。死在 start.s 时缓冲区还在内存里、永远不会被导出。
- 判定：**stop 点 = `start.s:621-624`**。这是该区间内唯一的停机路径，且页数算下来正好超 2 页。

### 1.5 32 位假设的现状

- 已经是 64 位、不用管：`pmap_paddr_t = uint64_t`（`osfmk/arm/pmap_public.h:40`）、
  `tt_entry_t/pt_entry_t = uint64_t`（`pmap.h:125-126`）、`TCR_IPS = 42 位`（`proc_reg.h:977`，
  4 KiB 颗粒下输出地址 40 位）、`PE_Video` 全 `unsigned long`（`pexpert/pexpert/pexpert.h:285-303`）、
  lowglo 全 u64（`osfmk/arm64/lowglobals.h:56-80`）、`memSize/memSizeActual/topOfKernelData` 全 u64
  （`pexpert/pexpert/arm64/boot.h:57-71`，arm 的 32 位版本 `boot.h:50-59` 不参与本板）。
- `ppnum_t = uint32_t`（`osfmk/mach/vm_types.h:71`）：4 KiB 页最多表示 16 TiB，32 GiB 没问题。
- 真出过的一类 bug 是 `ppnum << PAGE_SHIFT`（32 位左移）——已经修过一处：
  `patches/xnu/zzx-pmap-sync-64bit-pa.patch`（`pmap_sync_page_attributes_phys`）。树里其余
  `<< PAGE_SHIFT` 都先转了 `pmap_paddr_t`/`vm_offset_t`，或在 x86 专属分支里。
- 所以剩下的不是"32 位"，而是**"一段连续"这个结构性假设**，就在 §1.3 最后几行那几个函数里。

## 2. 未知项

1. 池子加大之后，32 GiB 基址上还有没有别的坎。没有任何一轮走过，只能上机验证；
   按现有代码判断不应有（PA 40 位、paddr 64 位、TCR 42 位、ptov 表驱动）。
2. 第三 bank 那段 free run 是否稳定。只在一次 UEFI map 里见过（`0x8042AC000` 起、`0x1F0000000` 长，
   顶端 `0x9ff...` 是固件镜像）。每次看 DarwinBoot 的 `pa=` 行确认。
3. 显示/GPU 侧对"分配的页都在高处"的容忍度。`pipa-flip` 的 `SRC0_ADDR` 是 32 位、4 GiB 以下已无空闲
   （见 `HANDOFF-pipa.md` 末尾）；更高基址会不会触发某些客户端的 32 位 DMA 限制，没验证过。
4. 阶段 2 里 `vm_pages[]` 的 index↔ppnum 线性关系怎么保（空洞槽占不占 vm_page），
   以及由此带来的元数据开销能不能接受（见阶段 2 的代价估算）。

## 3. 分阶段计划

### 阶段 0：把页表池加大 + 让"池子不够"变得可见（零风险，先做）

改动：
1. `osfmk/arm/pmap/pmap.h:119-122`：native 4K 分支的 `BOOTSTRAP_TABLE_SIZE` 由 `ARM_PGBYTES*16`
   改为 `ARM_PGBYTES*34`（与 ARM_LARGE_MEMORY 同值；4 KiB 颗粒下够 16 GiB/张映射），注释同步改。
   落在 `patches/xnu/native-4k-bootstrap-capacity.patch`。
2. （可选，强烈建议）`start.s` 的 `Lbootstrap_tables_exhausted`（`:621-624`）现在只有 `b .`。
   用 boot_args 里已有的 Video 画一条颜色不同的条就够定位：`x20+0x28` 是帧缓冲基址、`+0x38` rowbytes、
   `+0x48` height（等价于 `DarwinEnter` 的写法，`Entry.S:12-27`）。选一个探头没用过的 band。
3. （顺手修的单位 bug）`arm_vm_init.c:1925-1933` 的 DEVELOPMENT 断言里 `pages_used` 是页数，
   却与字节数 `BOOTSTRAP_TABLE_SIZE` 比较，永远不成立。应比 `BOOTSTRAP_TABLE_SIZE / ARM_PGBYTES`。
   它发生在 `start.s` 之后，救不了本 bug，但能防下一次。

判据：`owned_memory_size` 不动（`0x170000000`），日志仍是 `pa 0x1042B8000 window 0x170000000`，
`stage: arm_vm_init returned` → `vm_mem_bootstrap` → … → SpringBoard，与 `20261025-111715` 一致；
内核镜像约 +72 KiB（`bootstrap_pagetables[]` 变大，无其它消费者）。

### 阶段 1：单一区间、基址 32 GiB（+2 GiB）

改动：只改 `UniXNU-pipa/board.toml` 的 `owned_memory_size = "0x1F0000000"`（单段上限 8 GiB，
`make-pipa-adt.py:102-105` 已允许）。DarwinBoot 会自动挑到第三 bank（第二 bank 没有 7.75 GiB 的连续空闲）。

判据：
- DarwinBoot：`pa 0x8042AC000 window 0x1F0000000`。
- 内核：出现 `[xnu] ---- kernel: ramoops console zone mapped`、`stage: arm_vm_init enter/returned`、
  `vm_page_bootstrap: N free pages, M wired pages`，且 **N+M ≈ 2,031,616 页**（= 0x1F0000000/4096；
  5.75 GiB 时是 1,507,328）。
- 失败信号：仍只有蓝条 → 还在 start.s（看阶段 0 加的标记 / 说明还有第二处限制）；
  有 panic 屏文字 → 按文字定位（此时已到 C，第一嫌疑是 §1.3 里 pmap/VM 那几行）；
  有品红条但没下文 → 死在开 MMU 到 arm_vm_init 之间。

风险：中低。所有 ">4 GiB" 的路径今天已经在 4 GiB+ 基址上跑通；32 GiB 只多压"PA 位宽"一维。

### 阶段 2：多段不连续（+8 GiB；**内核留在 4 GiB 那一 bank**）

目标：range0 = 第二 bank 的 6 GiB（内核仍在这里，与今天同址），range1 = 第三 bank 的 7.75 GiB，
合计约 13.75 GiB。这样 `start.s` 的引导页表只需覆盖 range0（6 GiB → 12 张 L2 ≤ 14），
**完全绕开阶段 1 那个"内核在 32 GiB"的未知量**；阶段 2 不依赖阶段 1。

改动清单（自底向上）：
1. `make-pipa-adt.py`：`/chosen/darwinboot-memory-size` 保持"第一段"语义，新增
   `darwinboot-memory-size-1`（u64，可选，旧配置不变）+ `/chosen/memory-map/DramWindow1`
   （16 字节占位，写法同 `:165-167`；或 `DramWindow0/1` 两个都占位，让 loader 全填）。
2. `DarwinBoot.c`：`AdtNode`（`:196-273`）出参收进一个结构体，新增 `Window2Size`/`Window2Offset`；
   `:602` 之后再调一次 `AllocateWindow`（第一段分配后已是 `EfiLoaderCode`，第二次 `GetMap` 自然跳过，
   搜索逻辑无需改）；像 TrustCache（`:637-638`）那样把 `{addr,size}` 写回树。第二段按 2 MiB 对齐即可
   （不含内核，不需要 2 MiB 同余；`phystokv` 的 slide 会自己处理同余）。
3. 内核读段表：在 `arm_init.c:372` 之后、`:534` 之前，或在 `arm_vm_prot_init`
   （`arm_vm_init.c:1242`，已在读 `memory-map/TrustCache`）里读 `DramWindowN`，
   存进 `arm_vm_phys_ranges[]`（上限 4）；`gPhysBase/gPhysSize` 仍指 range0。
4. `arm_vm_init`：`avail_end` 仍是 range0 末尾；新增 `phys_span_end = max(各段末尾)`，
   `physmap_end` 按 span+slack 预留；`arm_vm_physmap_init`（`:1594-1617`）对每段各调一次 slide
   ——需要一个"直接给 PA"的变体（现实现用 `orig_va - gVirtBase + gPhysBase` 反推 PA）。
   ptov 表 8 项够（今天 2 项，加 2 段 → 4~5 项）。
5. `pmap`：`vm_first_phys = 最低段基址`、`vm_last_phys = 最高段末尾`（空洞计入 span），
   `pa_index/pa_valid` 不动；`pmap_data.c:618,634` 的表大小改用 span（新增一个全局）；
   `pmap.c:2733 pmap_next_page` 改成跨段游标（`pmap_steal_memory` 走同一个函数，自动受益）。
6. `vm_resident.c`：`vm_pages[]` 必须保持 index↔ppnum 线性（`VM_PAGE_GET_PHYS_PAGE` 就是
   `下标 + vm_first_phys_ppnum`），所以 `:1554-1585` 的循环要么给空洞也建槽并标成不可回收
   （参考 `VMP_ERROR_GET` 跳过的 retired 路径），要么改成按段填、下标按 ppnum 算。
   **这是阶段 2 唯一"脏"的地方**，建议先做最小版本（span+空洞占槽）。

代价（先接受再动手）：span `0x10007C000..0x9F42AC000` ≈ 35.8 GiB → `pv_head` ≈ 72 MB、
`pp_attr` ≈ 18 MB、`vm_page` 结构 ≈ 600 MB；相对 12.75 GiB 可用内存约 4.6%。

判据：DarwinBoot 打出两组窗口；`vm_page_bootstrap` 合计 ≈ 13.75 GiB 的页数（≈3.6M 页）；
`stage: vm_mem_bootstrap` 之后继续往下走；`sysctl hw.memsize` 与之一致。

风险：中高（pmap/VM 改动面），但每一步都有日志判据；可以先把 range1 关掉（`darwinboot-memory-size-1` 不写）
回到今天的状态。

## 4. 判据速查

| 日志/屏幕 | 含义 |
|---|---|
| `DARWINBOOT: ARM64 va=… pa=… owned=…`、`args pa … window … bytes` | 用了哪一段、多大 |
| 屏幕绿条（`DarwinEnter`）/ 蓝条（stage 1 = `_start`）/ 品红条（stage 2 = 开 MMU 前） | 走到哪一步 |
| `[xnu] ---- kernel: ramoops console zone mapped` | 内核活到 `clock_service_create`（之前所有 stage 一起导出） |
| `[xnu] stage: arm_vm_init enter` / `returned` | 引导页表 + `arm_vm_init` 过了 |
| `pmap_startup() init/release time`、`vm_page_bootstrap: N free pages, M wired pages` | pmap/VM 起来了，N+M 对得上窗口大小 |
| panic 屏上的 `TCR=`/`SCTLR=`、`pages=`/`free=` | 早 panic 时唯一能拍照的现场 |

## 5. 风险与回退

- 每个阶段都能靠配置或一个补丁回退：阶段 0 的池子和阶段 1 的 `owned_memory_size` 互不影响；
  阶段 2 只要不写 `darwinboot-memory-size-1` 就回到今天。
- 阶段 1 若卡在未知位置，阶段 2 不受影响（它把内核留在 4 GiB）。
- 阶段 2 之前**不要动** `dram-base`/`dram-size`：现在的 `0x80000000 + 0x980000000` 已覆盖三个 bank，
  `is_dram_addr()` 不需要改。
- 设备断电风险见 `HANDOFF-pipa.md` 10.13：本次调研不涉及 DMA/SMMU，阶段 0/1 都是"改内核常量 + 改配置"，
  没有新增总线访问路径。
