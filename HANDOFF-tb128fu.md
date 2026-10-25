# 交接：联想小新 Pad 2022（TB128FU）的 UniXNU 移植

> 本文件是给接手 agent 的工作说明，不属于 UniXNU 仓库，**不要提交、不要推送**。
> 写于 2026-10-21。
>
> **2026-10-22 真机首测后：先读 `HANDOFF-device-test.md`**，那里是当前设备状态、实验记录、
> fastboot 大镜像 workaround 和临时改动清单。本文件第 10–12 节是移植背景。

## 1. 任务

用户已决定：参照小米平板 6（pipa）的做法，**在不连接设备的情况下**为 TB128FU 准备 UniXNU 移植，之后可能先在这台设备上做真机测试。

第一阶段的目标与 pipa 相同：UEFI 固件 → UniXNU 菜单 → 载荷卷 → DarwinBoot 交给 XNU → 内核进入 → panic 画面显示 `Failed to mount root device`（此时还没有存储驱动，这是预期的终点）。存储（UFS）、显示、触控、GPU 驱动都**不在**本阶段范围内。

需要产出两部分：

1. **UEFI 设备包**：在 Mu-Silicium（uefi-sheng 分支）里为 TB128FU 新建平台包，并加入 UniXNU 集成。
2. **设备仓库 `UniXNU-tb128fu`**：布局同 `UniXNU-pipa`。

## 2. 用户的工作规则（必须遵守）

- 与用户交流只用中文（或英文），**所有给用户看的文字都用中文**，包括工具调用之间的简短说明。不要用黑话或直译的英文俚语。
- 修改代码只能用 Edit 工具；不要用 Python、sed 等改代码；大范围批量替换必须先问用户。
- 不要自行安装依赖（pip、cargo、npm、brew 等），需要时写好命令或脚本交给用户执行。
- 遇到与预期不同的情况：**停下来向用户报告，不要自己换方案**。需要选择方向时，先汇报调查结果和可选项，由用户决定。
- 不要对设备做任何写入（`fastboot flash`、`fastboot erase`、改分区）；连 `fastboot boot`（不写入设备）也要先问用户，并确认用户在设备旁边。
- 提交可以在本地做；**推送到 GitHub、创建仓库、开 PR 都要先问用户**。
- 包名前缀（如需要）用 `tech.xvanturing.`。
- 设备的序列号、PCBA 序列号等个人标识只留在本地采集目录，不要写进任何仓库。平台种子（`--unique-chip-id`）同 pipa：存在 `.cache/platform-seed`，不提交。

## 3. 相关仓库和目录

| 位置 | 说明 |
| --- | --- |
| `/Users/xvan/agents-home/UniXNU` | 上游基础仓库，`main` 在 `b6ae99c`（与上游一致）。远端：`origin` = UniXNU/UniXNU，`fork` = xVanTuring/UniXNU |
| `/Users/xvan/agents-home/UniXNU-pipa` | **主要模板**。pipa 的设备仓库（已推送到 xVanTuring/UniXNU-pipa，标记 WIP）。README 有完整的构建步骤和「从设备读取的值」表 |
| `/Users/xvan/agents-home/uefi-pipa` | Mu-Silicium（UniXNU/uefi-sheng 的 fork），分支 `pipa`。UniXNU 集成只有 3 个提交，改了 `pipa.dsc`、`pipa.fdf`、`Include/DXE.inc`、`Resources/Configs/pipa.toml`，说明在 `Platforms/Xiaomi/pipaPkg/PORTING.md`。可以 `git log sheng..pipa` 查看 |
| `/Users/xvan/agents-home/UniXNU-sheng` | 上游的参考设备（SM8550），pipa 的很多脚本从这里复制 |
| `/Users/xvan/agents-home/UniXNU-qemu` | QEMU 板，与 `pipa_defconfig` 内核配置相同，在 QEMU 里已验证能到 iPadOS 主屏幕 |
| `/Users/xvan/agents-home/android-devinfo` | 设备信息采集脚本（`devinfo.py`） |
| `/Users/xvan/agents-home/android-devinfo/devinfo-tb128fu-20261021-2244/` | **本机采集结果**：`summary.md`（摘要）、`summary.json`、`dt.txt`（运行中的完整设备树文本）、`raw/`（原始文件，含 `raw/storage/partitions.tsv`）、`firmware/`（17 个分区备份 + `manifest.json`） |
| `out/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw` | 目标系统镜像（iPad7,1 iPadOS 17.7.11），设为 `UNIXNU_IPSW`；不设的话构建工具可能会去网上下载 |

Mu-Silicium 里**没有** TB128FU 的平台包，但 SoC 层的 `Silicon/Qualcomm/DivarPkg`（SM6225）已有，`Platforms/Xiaomi/` 下的 `fogPkg`、`spesPkg`、`tapasPkg` 都基于它，可作为新平台包的参照（含 `Library/MemoryMapLib`、`ConfigurationMapLib`、`Resources/Configs/*.toml`、`Resources/DTBs/*.dtb`）。Mu-Silicium 已有的联想平台包命名为 `j706fPkg`、`9707fPkg`。

## 4. 设备信息（已采集，细节见 `summary.md` 和 `dt.txt`）

| 项目 | 值 |
| --- | --- |
| 型号 | 联想 TB128FU（厂商代号 `P89990JA1`），SM6225，内部名 KHAJE / `SM_DIVAR`，soc_id 518，平台 `bengal` |
| 当前系统 | **不是原厂系统**：TrebleDroid 通用系统镜像（Android 13），厂商分区为联想 `15.0.619`，内核 4.19。已解锁，Magisk root，vbmeta 关闭了校验（flags 2）。adb 序列号以 `adb devices` 为准 |
| CPU | 4 × Cortex-A53 + 4 × Cortex-A73，**ARMv8.0**（没有 LSE、FP16、RCpc）。MPIDR 为 `0x0..0x3` 和 `0x100..0x103`（**按簇编号**，与 SM8250 每核一个 Aff1 不同） |
| 定时器 | `arm,armv8-timer`，19.2 MHz（`0x124f800`） |
| 中断控制器 | GICv3，分发器 `0xf200000+0x10000`，重分发器 `0xf300000+0x100000` |
| PSCI | 1.0，`smc` |
| 内存 | 共 5.95 GiB，DRAM 起始 `0x40000000`。设备树内存：`0x40000000+0x3cc00000`、`0x80000000+0x40000000`、`0xc0000000+0x100000000`；Linux 看到 `0x80000000–0x1bfffffff` 连续 5 GiB 可用。reserved-memory 全表在 `summary.md` |
| 开机画面内存 | `cont_splash_region` `0x5c000000+0xf00000`（15 MiB） |
| 串口 | 控制台 QUP UART `0x4a90000`（`earlycon=msm_geni_serial,0x4a90000`） |
| 存储 | 三星 UFS 2.2 `KM2L9001CM-B518`，控制器 `0x4804000`，6 个 LUN。userdata 在 `sda18`：LBA `0x222f31`–`0x1c457fa`，4096 字节块，104.5 GiB |
| 启动镜像 | 原厂 `boot` 为 **header v2**，页 4096，base `0x0`，kernel `0x8000`、ramdisk `0x1000000`、tags `0x100`、dtb `0x1f00000`。**没有 `vendor_boot`**，设备树附在 boot 镜像里（`androidboot.dtb_idx=8`），dtbo 用 `androidboot.dtbo_idx=12`。A/B，当前槽位 `_a`。`boot_a` 分区 96 MiB；另有 `recovery_a` |
| 屏幕 | `mdss_dsi_hx83102p_boe_2k_video`，1200×2000，60 Hz，视频模式；背光最大 255 |
| 触控 | Himax（`himax,hxcommon`），SPI `0x4a80000`，10 MHz，中断 GPIO 80，复位 GPIO 86（设备树里另有一个备用 Novatek 节点） |
| GPU | Adreno 610 v2（chip ID `0x6010001`），**没有 GMU** |

固件备份：`firmware/` 下 17 个分区（boot、dtbo、vbmeta、recovery、abl、xbl、xbl_config、uefisecapp、imagefv、tz、hyp、devcfg、qupfw、rpm、keymaster、featenabler、splash），每个都核对了大小和 SHA-256。注意这些是**设备当前状态**的备份（boot 可能是 Magisk 修补过的），不是联想原厂刷机包。

## 5. ARMv8.0 能否运行 iPadOS 17（已检查）

这台设备独有的风险：CPU 没有 v8.1 的 LSE 原子指令（pipa、sheng、QEMU 都有）。已扫描结果：

- iPad7,1 的内核缓存（原厂内核扩展）：约 182 万条指令，**没有** v8.1 及以上的指令。
- 系统共享缓存（3354 个库，只扫描代码段）：只有约 1150 条 LSE 指令。libobjc、Swift 运行库、libc++、CoreFoundation、UIKitCore 都**没有**，说明系统按 v8.0 编译；LSE 集中在 libdispatch、pthread、platform、malloc，它们按 XNU commpage 的 `kHasFeatLSE` 标志选择实现（XNU 按 `ID_AA64ISAR0_EL1` 设置该标志，在 A53/A73 上不会设置）。dyld、SpringBoard 等少数库各有一两处匹配，很可能是代码段里的跳转表数据，但只有真机或模拟才能完全确认。
- 内核：UniXNU 的 BCM2837 内核配置本来就面向 Cortex-A53。**但 `tb128fu_defconfig` 照抄了 pipa/sheng 的
  `CONFIG_XNU_NATIVE_PAN=y`**，使内核带上 PAN 指令（v8.1），真机在 `_arm_init` 的 `msr PAN, #1` 处停住；
  2026-10-22 已改为 `n`，见 `HANDOFF-device-test.md` 第 11 节。

可以考虑（需用户同意）：在 UniXNU-qemu 里用 TCG 模拟 `cortex-a53` 跑一次 iPadOS 用户态，在上真机前确认没有非法指令。`run-qemu.py` 目前固定使用 Cortex-A710（TCG）或主机 CPU（HVF），需要加一个选择 CPU 型号的参数。

## 6. 要做的事（参照 pipa，具体实现由接手 agent 决定）

1. **UEFI 平台包**：参照 `fogPkg`/`spesPkg`/`tapasPkg` 为 TB128FU 新建平台包（内存映射按本机的 reserved-memory 和 DRAM 布局写；`Resources/Configs/` 里的启动镜像参数；本机的 DTB 可从 `boot_a.img` 附带的设备树中取第 8 个，或参考运行中的 `dt.txt`）。先确认不加 UniXNU 时能构建，再按 pipa 的 3 个提交加入 UniXNU、`ShengBootMemoryDxe` 和 DarwinBoot。
2. **设备仓库 `UniXNU-tb128fu`**：逐个对照 `UniXNU-pipa` 的文件。要特别处理的差异：DRAM 起始地址 `0x40000000`；GIC 地址；CPU 按簇编号（`make-pipa-adt.py` 目前要求 `reg & 0xff == 0`，并按 Aff1 区分大小核）；`dram_size` 的含义见 pipa `board.toml` 的注释（XNU 把 `dram-base`/`dram-size` 当作一个地址范围，`is_dram_addr`）；`[userdata]` 用上面的 LBA（`windows` 同 pipa/sheng）；`kernel_args` 里的 `sheng-pmgr=2` 等是否适用要核对。
3. **离线验证**：`unixnu fetch / build package / build firmware / bootimg / defconfig / build kernel / build payload` 全部通过，检查脚本（同 `check-pipa.py`）通过。命令顺序见 UniXNU-pipa 的 README。
4. **文档**：设备仓库 README 写清状态（标明 WIP、没有在真机上运行过），列出「从设备读取的值」和已知风险。
5. **真机首测**：只在用户同意并在场时，用 `fastboot boot`（不写入设备）启动。

## 7. 需要用户决定的问题（遇到时先问）

- **UEFI 代码放在哪个仓库**：GitHub 同一账号对同一个上游只能有一个 fork，`xVanTuring` 已经把 `UniXNU/uefi-sheng` fork 成了 `uefi-pipa`。可选：在 `uefi-pipa` 里加一个 `tb128fu` 分支；新建一个非 fork 的仓库；或把现有 fork 改成通用的名字。本地可先在 `/Users/xvan/agents-home/uefi-pipa` 新建分支，或另克隆一份。
- 平台包和设备仓库的命名（例如 `tb128fuPkg`、`UniXNU-tb128fu`）。
- 启动镜像用 header v1（Mu-Silicium 的默认做法）还是与原厂一致的 v2。
- 是否先做第 5 节的 Cortex-A53 模拟验证。
- 何时上真机。

## 8. 已知风险

- 第 5 节的 v8.0 残余风险。
- ABL 能否接受 192 MiB ramdisk 的启动镜像（pipa 也未验证）。`boot_a` 分区只有 96 MiB，`fastboot boot` 不受分区大小限制，但以后若要刷入分区就要考虑。
- 固件卷大小：加入 UniXNU 后 FD 是否还放得下（pipa 有同样的问题，见其 README）。
- 厂商时钟和电源驱动可能关掉屏幕（sheng 上出现过）。
- 设备当前跑的是 GSI，Android 侧信息（如 `ro.product.*`）不能代表原厂；硬件信息以设备树、`ro.product.vendor.*` 和 `/sys` 为准。

## 9. 恢复手段

- `fastboot boot` 不写入设备，重启即回到 Android。
- 若以后要刷写分区，先确认 `firmware/` 里对应分区的备份和 `manifest.json` 的 SHA-256；userdata 刷写会清除 Android 数据，必须先问用户。

## 10. 2026-10-21 深夜的实际进展

这一节记录真机首测后的状态，后续接手从这里开始。

### 仓库

- `/Users/xvan/agents-home/uefi-tb128fu`：本地新建的 Mu-Silicium 检出，分支 `tb128fu`，平台包 `Platforms/Lenovo/tb128fuPkg`。
- `/Users/xvan/agents-home/UniXNU-tb128fu`：设备仓库（尚未 `git init`），`board.toml`、`tb128fu_defconfig`、ADT/检查脚本都在。
- `/Users/xvan/agents-home/UniXNU`：改过 `drivers/NativeUfs/GccUfs.h`、`NativeUfs.cpp`、`SmmuTakeover.h`（SM6225 + probe-only + probe 15）。未提交、未推送。

### 固件（已真机验证到 BDS）

- SEC、内存映射、MMU、DXE、`SimpleFbDxe`、`GraphicsConsoleDxe` 都能在 TB128FU 上跑，屏幕能显示 UEFI 调试文字。
- 第一次停在 BDS，屏幕出现 `GetPlatformBootGraphic Status: NotFound`：Mu-only FV 没有 `BootLogo.bmp`，该错误本身不致命；真正的问题是默认启动项（MsBootPolicy/USB/FFU/MassStorage）都不在 FV 里，UniXNU 不在 BootOrder。
- 已修：FDF 加入 `QcomPkg/Extra.fdf.inc`（logo/BGRT/BootManagerMenuApp/MsBootPolicy/UFP/MassStorage），`SiliciumPkg/Library/MsBootOptionsLib` 把 UniXNU（`9B665204-A133-47A2-9396-9E2B370A4E25`）注册为第一个启动项。
- `Sec.c` 加了绿色 `cont_splash_region` 自检和错误 `CpuDeadLoop`；FD 仍是 3 MiB，FVMAIN 约 7.43 MB，FD 占 46%。

### 内核与 UFS

- 内核配置：`CONFIG_XNU_NATIVE_UFS=y`、`CONFIG_XNU_NATIVE_VIRTIO_BLOCK=y`；`NativeUfs` 用 `--sm6225 --no-verified-block` 编成完整块设备类。
- `NativeVirtioBlock` 的 `build-native-virtio-bindings.py` 把 11 个 `IOBlockStorageDevice` 符号绑定到原厂 kernelcache 地址，解决了块类的链接。
- 最新内核 sha256 `5bb95abd1ff432bd0dc04ee8b03113b31730aa9e05ed74403c4cc1fa573db6ce`（50 MiB，noverify 块设备路径；含 `native-ufs-block-report` 支持）。
- UniXNU 菜单超时已设为 10 秒（`board.toml [payload] timeout = 10`），首测有足够时间看到菜单并按音量上键确认。
- `native-ufs-probe=10`：只读 LUN 0 userdata GPT，panic 显示 LBA/magic/manifest。
- `native-ufs-probe=15`：不经过 VerifiedRoot，把 userdata（`0x222f31..0x1c457fa`）直接发布成块设备，8 秒后 panic 显示读块数/失败数。
- `native-ufs-root-noverify=1`：读 SHNGROOT manifest，把 OS/SystemOS 两个窗口发布成块设备，跳过 VerifiedRoot；内核的 APFS/Image4 仍会校验它挂载的内容。这是根卷 noverify 测试路径。
- 目前没有 Data seed（`--data-ramdisk`）：root-noverify 即使挂上 OS，也可能因为没有 `/private/var` 停在 Setup/panic；Data 镜像/首次启动数据是后续要补的输入。
- VerifiedRoot 正式路径已经离线做通：owner RSA3072 key、OS/SystemOS 的 `manifest.bin`、`verification.json`（逐块验证 1,483,785 / 806,400 块通过）和 `build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a`（70 MB）都在树里。但把 bundle 链进内核后 `kernel.macho` 变成约 119 MiB，超过 96 MiB 的 `boot_a` 和 ABL 的 fastboot 缓冲，无法启动；因此 `CONFIG_XNU_VERIFIED_ROOT_PROBE=n`，正式路径留待把 manifest/策略移到 userdata 运行时读取，或换更大的 boot 目标。
- userdata 镜像已离线生成：`out/tb128fu/userdata/userdata-root.img`，9,382,658,048 bytes（8.74 GiB），OS SHA-256 `e5634d04…`、SystemOS `b4908cde…`，manifest `SHNGROOT` 在偏移 0。

### 启动镜像（都是 57 MiB，刷 `boot_a`）

| 镜像 | 用途 | sha256 |
| --- | --- | --- |
| `out/tb128fu/boot/boot.img` | 正常启动 | `fa258a019ff65614565ac5506f693ee7a92f90c99f6e2d70e2c346be9f0bab7d` |
| `out/tb128fu/boot/boot-ufs.img` | probe 10 | `354fe8e94c9533c6ef46801d34f6b502f69e039b409eb489cc8f103dd26b4eab` |
| `out/tb128fu/boot/boot-ufsblock.img` | probe 15 | `4a8d9431a340972082a4713d8399e3190f3e4ae452e93d0febe3cbc6912dc659` |
| `out/tb128fu/boot/boot-root.img` | root-noverify | `6ea66eb6dcc4569831261576cbf31f94b86d2287655797bce69a653a86d6dc3f` |
| `out/tb128fu/boot/boot-root-report.img` | root-noverify + 20 秒块读报告 | `7937594a2b915afd54c97066e1337f6268ba84d9ceabb2c067ab644e68e0c068` |

- 五个镜像现在都用内核 `5bb95abd…`；`boot-root-report.img` 额外带 `native-ufs-block-report=20`。如果 `boot-root.img` 静默失败，用报告版看 `published/reads/failures`。

- 新增 `tb128fuPkg/Drivers/Tb128fuKeyInputDxe`：音量上键（TLMM GPIO 5，低有效，north tile `0x405004`，设备树 pinctrl 基址 0x400000）映射为 UniXNU 菜单的 Enter；PM6125 电源/音量下仍在 SPMI v5 arbiter 上，暂未读取。FD 仍 3 MiB，FVMAIN 约 7.51 MB，MemoryMapLib 54 个描述符。
- `fastboot boot` 对 192 MiB、89 MiB 都不启动；57 MiB 报 `Failed to load/authenticate boot image: Load Error`。ABL 的 fastboot 缓冲小于 96 MiB 的 `boot_a`。可行路径是 `fastboot flash boot <img>` 后重启。
- 设备当前状态：`boot_a` 被刷成了上面的 57 MiB 镜像，`boot_b` 保留可启动备份；用户已把设备拿走。屏幕最后一次日志的图片是 0 字节，内容未知。

### 恢复

- 进 fastboot 后：`fastboot --set-active=a`，`fastboot flash boot_a android-devinfo/devinfo-tb128fu-20261021-2244/firmware/boot_a.img`，必要时清 `misc` 前 64 字节，再 `fastboot reboot`。
- `misc` 里的 `bootonce-bootloader` 会因 `fastboot boot` 失败而反复触发 fastboot/切槽；这是之前“无限重启”的直接原因。

### 下一步

1. 设备回来后先刷 `boot.img`，确认 BDS 能进 UniXNU 菜单；再刷 `boot-ufsblock.img` 看块设备读块数。
2. 刷 userdata 镜像：`fastboot flash userdata out/tb128fu/userdata/userdata-root.img`（8.74 GiB，恢复到 userdata 分区；数据本来就不保留）。
3. 刷 `boot-root.img`（`native-ufs-root-noverify=1`），看内核能否通过 UFS 块设备挂载 OS/SystemOS 并继续启动。
4. noverify 路径能进 userspace 后，再解决正式 VerifiedRoot：bundle 已做通，但 70 MB 策略让内核到 119 MiB，放不进 96 MiB 的 boot_a；下一步把 manifest/策略移到 userdata、由 NativeUfs 运行时读取，或换更大的 boot 目标。
5. 显示（MDSS/DSI hx83102p）、触控（Himax SPI）、GPU（Adreno 610）、SPMI v5/PM6125 按键。

## 11. 2026-10-22：正式 VerifiedRoot 改成运行时策略

设备被拿走后的离线工作。目标是去掉“内核 119 MiB 放不进 boot_a”这个卡点，正式路径不再链接 70 MB 静态清单。

### 做法

- `userdata` 镜像新增策略目录：第 2 个 4 KiB 块（`0x1000`）放 `SHNGPOL1`，记录每个窗口对应清单的偏移/大小/SHA-256；清单本体（OS 47,481,664 字节、SystemOS 25,805,344 字节）追加在窗口之后，按 4 KiB 对齐。
- 目录只是定位信息，不是信任输入；内核用编译进去的 395 字节所有者公钥验证清单签名和哈希表。
- `NativeUfs` 的 verified 路径先找静态 bundle（其它板/QEMU），没有静态 bundle 时按窗口名从 userdata 读清单，`kmem_alloc` 一份只读副本后走原来的 `vr_open`/逐块校验。
- 内核只需 `kits/VerifiedRoot/src/verified_root.c`、`adapters/xnu_crypto.c` 和公钥，新脚本 `scripts/build-verified-root-key.py` 生成 `build/verified-root-key/libVerifiedRootKey.a`（公钥 SHA-256 固定为 `70a5ea34…`，作为 `--pin` 写进 `[kernel] archives`）。
- 新 CLI：`./unixnu build userdata --root-policy OS=<manifest> --root-policy SystemOS=<manifest>`。
- 诊断输出新增 `POLICY dir=<status> envelopes=<n>` 一行；`native-ufs-root=1`、`native-ufs-diagnostics=1` 已写进 `board.toml` 的 `kernel_args`。


### owner root 链与 Data seed（同一轮）

- 启用与 sheng 相同的整条链：`NATIVE_RAM_BLOCK`、`VERIFIED_ROOT_PROBE`、`OWNER_ROOT_AUDIT`、`OWNER_ROOT_TRUST`、`OWNER_CACHE_MOUNT`、`OWNER_CACHE_STARTUP`、`OWNER_BOOT_MANIFEST`、`OWNER_DATA_MOUNT`、`OWNER_UNION_SYSTEMOS`。
- `NativeVirtioBlock` 改成 verified 变体（只为提供 `native_verified_block_root_authorized` / `native_verified_block_volume_device`；本板没有 virtio 设备，块传输不会被配置）。`NativeRamBlock` 链入，Data seed 作为 RAM 块设备发布。
- `offline-owner-systemos-mount.patch` 里写死的 sheng 缓存卷 UUID 改成可配置宏 `XNU_OWNER_CACHE_VOLUME_UUID`（默认值不变）；`build-xnu-4k-baseline.sh` 从 defconfig 生成到 `build/xnu/owner-data-volume.h`。
- defconfig 里的两个卷 UUID：SystemOS `8846FE78-2910-4297-AF97-D59BB6F8FC16`，Data seed `73F1FA24-4189-446D-96D4-FB6324EBE955`。
  - 之前用的 `04769D27-…` 是编造的，与真实 SystemOS 镜像的 APFS 卷 UUID 不符（真实值正好等于 sheng 补丁默认值）。已用真实 UUID 重新签名 `build/verified-root-systemos/manifest.bin`（镜像哈希仍为 `b4908cde…`，块表不变，只有头部 UUID 和签名变了）。用 host C 验证库复验：`vr_host_open` 返回 0，UUID `8846fe78…`，block 0 / 403200 / 806399 全部通过。
- Data seed 由 `prepare-owner-data-fixture.py` 从 OS 镜像的初始 `/private/var` 树生成（32 MiB，sha256 `52d711aa010249f18d7045e367d9699666e32a6eb27c1cf45a14d3cb761ac78a`，UUID `73F1FA24-4189-446D-96D4-FB6324EBE955`），打包成 `owner-data-32.dbr`（995,408 字节），再扩到 8 GiB 的 `owner-data-8g.dbr`（1,028,224 字节，sha256 `2441dc72f052da43c25191b962ea84947026f9492fab5366caab15e18a41e255`）。payload 的 `--skip-setup` 编辑副本后 `data_ramdisk_sha256` 为 `56100a1d3010232dd5f9bee400fa0e1e62df890180cb0e9e8ffd6cc5ba2f9226`。

### 已验证（离线）

- `build-arm-driver.py NativeUfs --sm6225`、`NativeVirtioBlock --verified-root`、`NativeRamBlock` 以及两个 bindings 脚本全部通过。
- `build-verified-root-key.py` 通过；归档含 `_verified_root_key`、`_verified_root_policies`（count 0）、`_verified_root_policy_count`、`_vr_open`、`_vr_verify_block`、`_vr_xnu_crypto`、零长度 `_verified_root_manifest` 和 `_native_verified_root_probe` 桩。
- `./unixnu build kernel` 链接通过，`out/tb128fu/kernel/kernel.macho` 52,056,064 字节（49.6 MiB），sha256 `a696551b4f7186b43f560b5f7a1fa715639aff5a4b1dfb0a6dbabfb98f9c5adc`。`nm` 确认 `_native_verified_block_root_authorized`、`_native_ram_block_volume_seed`、`_native_owner_data_*`、`_native_owner_cache_prepare`、`_boot_progress_*` 都在。
- userdata 镜像：9,455,951,872 字节（8.81 GiB），sha256 `6bfd1258c29aed02d56f3e5e882445aaacbc9f6dcd0da5b6cca2404304f36f0b`；策略在 `0x22f400000`（OS，sha `5a052dc1…`）和 `0x232149000`（SystemOS，sha `bd455478…`）。
- 启动镜像 `out/tb128fu/boot/boot.img`：60,203,008 字节，sha256 `8d7be1f0ca2cf4fc3c5e94e0cdba27b15681fc0f79c98d28c4cc0f38f7c47480`；payload 56 MiB，含 `kernel.macho`（52,056,064）、`kernel.macho.adt`（11,520）、`kernel.macho.trustcache`（61,356）、`scratch-data.img`（1,261,728），`initrd` 指向 Data seed。
- 内核参数新增 `sheng-data-persist=2`：8 GiB Data seed 属于“大磁盘”，没有易失写路径，改动页必须走 userdata 里根镜像之后的 store 窗口（`NativeUfs::native_ufs_store_open`，模式 2）。没有这个参数 Data 卷会以只读方式挂载失败。
- trust cache 是原始 IPSW 的 static + System 两个模块（bundle sha256 `7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`，与 `[payload] trust_cache_sha256` 第一项一致）；`build-original-system-trust.py` 需要 `refer/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw` 和 `.cache/firmware/target-volumes/static-trustcache.bin`（本地符号链接/拷贝，不提交）。
- 启动进度屏：从 sheng 移植了 MIT 许可的 `BootProgress.c` / `BootFaultProbe.c` / `BootWatchdogPolicy.c` 和四个头文件到 `UniXNU-tb128fu/package/`，新增 `scripts/build-boot-progress.py`（字体头从 `$UNIXNU_UEFI/.../FrameBufferSerialPortLib` 取）。本板还没有触控/调频/显示/GPU 组件，所以 `BootProgressStubs.c` 用与 `BootProgress.c` 完全一致的类型提供了 83 个诊断符号的零值存储；原来的 `package/LinkStubs.c` 里 `boot_progress_poll` 桩已删除（弱引用，真实定义由该归档提供）。参数 `boot-progress=2`、`sheng-build-tag=tb128fu-001`。若 `vinfo.v_baseaddr` 不是内核映射，进度屏不会绑定，只保留 panic 屏。
- 新增启动输入自检 `UniXNU-tb128fu/scripts/check-boot-inputs.py`：纯 Python 验证 SHNGROOT 窗口、SHNGPOL1 目录、两份清单的 RSA-3072 签名/哈希表/块数/签名镜像大小与哈希、OS/SystemOS 的真实卷 UUID、Data seed 的 UUID、内核 owner 链与进度屏符号、96 MiB 启动上限和 trust cache pin。当前镜像跑通：`PASS: boot inputs are internally consistent`。
- 进度屏渲染在主机上验证过：用同一份 `BootProgressDisplay.h` + `Font.h` 往 1200×2000 缓冲区画 6 行示例，转 PNG 目检文字清晰、绿色“accepted”行和计数行都正常；临时 harness 放在 `/tmp/bp-render`，没有进仓库。
- 固件重新构建通过（`check-tb128fu.py` 预检通过），FD 3 MiB，sha256 `01d70013876441f23d7f638b8c59ed600814501175e162bd2b3653807f698a0a`。
- QEMU 根路径测试通过，新增的 `policy_directory` 项为 pass；旧的 `boot-root.img`（noverify）已删除——owner 链会拒绝未认证的根，这个对照没有意义了。

### 未验证 / 风险

- 真机上 `configureVerification` 的运行时读取（每个清单几百次 128 KiB 的 UFS 顺序读）没有跑过。
- 73 MB 清单常驻内核内存（`kmem_alloc`，只读保护）；6 GB 设备应该够，但未实测。
- owner root 链（root 授权、SystemOS 缓存挂载、boot manifest、Data 挂载）全部只在 QEMU/sheng 上验证过，TB128FU 上没跑过。
- Data seed 是 OS 初始 var 树的 8 GiB RAM 卷；`NativeRamBlock` 的持久化路径（userdata store）已接上但未实测。
- 没有显示/触控/GPU 驱动：即使 launchd 起来，SpringBoard 也可能无可见输出或无法渲染。

### 设备回来后

0. 先跑 `python3 scripts/check-boot-inputs.py --trust-cache ../UniXNU/build/original-static-system-trust/original-static-system.bundle`，必须看到 `PASS: boot inputs are internally consistent`；不通过就不要刷。
1. `fastboot --set-active=a`，刷 `out/tb128fu/boot/boot.img`，重启；看 BDS 是否进 UniXNU 菜单（10 秒超时，音量上键=Enter）。
2. `fastboot flash userdata out/tb128fu/userdata/userdata-root.img`（9,455,951,872 字节）。
3. 预期顺序：`SHNGROOT` → `SHNGPOL1` → 读 OS/SystemOS 清单 → 发布两个块设备 → root 授权 → SystemOS 缓存挂载 → boot manifest → Data 挂载 → launchd。`boot-progress=2` 会把阶段画在屏幕上；失败时 `native-ufs-diagnostics=1` 把 stage 打到 panic 屏幕。如果进度页没有出现而只有 panic 屏，说明 `vinfo.v_baseaddr` 不是内核映射（`boot_progress_bind` 的绑定条件没满足），不影响启动本身。
4. 若卡在 `rootvp not authenticated`，先看 `native_owner_root_*` / `native_verified_block_*` 诊断；这是 owner 链授权没通过，不是块读失败。
5. 之后补显示（MDSS/DSI hx83102p）、触控（Himax SPI）、GPU（Adreno 610）、SPMI v5/PM6125 按键。

## 12. 显示移植的前置调查（2026-10-22）

显示可以走固件帧缓冲（`NativeLegacyDisplay`，不依赖 MDSS），但需要绑到原始的 `IOMobileFramebufferLegacy` 基类。调查结论：

- 联想 TB128FU 的内核源码官方门户 `support.lenovo.com/.../ht511330` 对本网络返回 403；GitHub 上只有预编译内核（`lenovo-sm6225-devs/device_lenovo_tb128fu-kernel`）和分区转储，没有源码。
- sheng 的 `build-native-legacy-display.py` / `build-native-mobile-display.py` 依赖 `build/native-prelink/native-owner-mobile-graphics-candidate.macho`，但生成这个候选的脚本不在 `UniXNU-sheng` 的当前树或 git 历史里，产物也已清理。
- 本板 kernelcache 里 `com.apple.iokit.IOMobileGraphicsFamily` 是内嵌的 `MH_KEXT_BUNDLE`（`_PrelinkExecutableLoadAddr` 0xfffffff0055f5780，38,664 字节），**符号表已剥离**：`__TEXT_EXEC` 在 0xfffffff00676c000，`__mod_init_func` 有 10 个初始化函数（0xfffffff00676ee74 起）。`IOMobileFramebufferLegacy` 的类名串在 0xfffffff0055fbc47，但 `gMetaClass` / C2 / D2 需要从初始化函数的 `OSMetaClass` 构造调用和 alloc 函数里结构化反推，不能再靠符号名。
- 因此显示移植的第一步是重建“候选提取”：从 kernelcache 抽出该 kext 的段，定位 `gMetaClass`、构造/析构和 262 项 vtable，再用 `NativeLegacyDisplay.cpp` / `LegacyDisplayProbe.cpp` 的框架接上。这是一个独立的多轮任务；在设备回来前，当前镜像刻意不带显示驱动，以免干扰首启诊断。

已拿到的中间结果（继续这一任务时从这里开始）：

- 原始类名串 `IOMobileFramebufferLegacy` 在 VA `0xfffffff0055fbc47`（`site.IOMobileFramebufferLegacy` 在 `bc61`，后者是 IOTRACKING 的站点名）。
- 在 `__TEXT_EXEC`（`0xfffffff00676c000`，222,908 字节）里唯一引用该类名的是 `0xfffffff0067866a4` 的 `adrp x1/add x1`，所在函数起始于 `0xfffffff006786680`。它就是 `IOMobileFramebufferLegacy::MetaClass::MetaClass()` 的候选：保存 vtable、以 `w3 = 0x18a0`（该类实例大小 6304）调用 `OSMetaClass` 构造。
- 该函数不在任何直接 `bl`、`b`、数据指针或 GOT 指针里被引用，说明调用点经过 prelink 的指针重定位表，而不是普通调用；下一步是解析 `__PRELINK_LINKEDIT`/`_PrelinkLinkKASLROffsets` 里的重定位站点，找到把 `&gMetaClass` 作为 x0 传入的初始化器。
- `gMetaClass` 对象预期在 `__DATA`/`__bss`（构造前为零）；拿到它后，`MetaClass::alloc` 指向的函数里能找到该类的 C2，vtable（`__ZTV25IOMobileFramebufferLegacy`）的 262 项也能从 `__DATA_CONST` 按布局读出。

进度屏渲染已在主机验证（`/tmp/bp-render`，1200×2000，6 行示例文字清晰），但没进仓库。
