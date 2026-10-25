# 交接：小米平板 6（pipa）的 GPU / Metal 现状

> 本文件是给用户和接手 agent 的状况记录，不属于 UniXNU 仓库，**不要提交、不要推送**。
> 写于 2026-10-24 晚，工作暂停时。代码和英文说明都在 `UniXNU-pipa`（最新提交 c28476e），
> 设备仓库的 `README.md` 是权威说明，这里是中文摘要加上进行中的排查。

## 1. 当前状态一句话

iPadOS 17.7.11 在 pipa 上跑到主屏幕，8 个核分成大小核两个调度集群，触控和按键可用；
Adreno 650 在 GMU 下运行，**界面由 GPU 通过 Metal 渲染**（插件把 Metal 翻译成 Vulkan 交给 Turnip，
每一帧再由 GPU 复制到 UEFI 帧缓冲）。主要遗留问题：GMEM 模式下圆角画坏、动画卡顿。

## 2. 这次（2026-10-24 下午到晚上）做成的事

| 提交（UniXNU-pipa） | 内容 |
|---|---|
| f5f8412 | **修好 Metal 黑屏**：hw_init 设置 `CP_APRIV_CNTL`（Linux 在 A650 上就这样做）。之前着色器和纹理单元的所有读取都报 UCHE_OOB_ACCESS，帧全黑。SQE 默认改用 linux-firmware 的 `a650_sqe.fw`（0x112）；厂商分区那份是 0x093，Linux 会拒绝加载 |
| ac8bf0f | GMEM 渲染设为默认（`pipa-turnip-debug=nocb`）；自动滑动；GPU 忙碌率；插件遥测表写进日志 |
| c0bc406 | **大小核调度**：A55 为 E 集群、A77 为 P 集群（UniXNU-tb128fu 的补丁、ADT 的 `cluster-type`、ArmCpu 按类型登记，ArmCpu pipa 分支 c0668c7）。渲染线程每帧 CPU 时间从 17.8 毫秒降到 10–11 毫秒 |
| f7a608b | 动画期间帧率统计（`display: pacing`） |
| fa1eb1c / 6bd27c8 | GMEM 和系统内存的对比；启动时的内存带宽测试；读日志程序超时改为 60 次 |
| c28476e | 帧缓冲字符画和圆角截图；进程和线程栈诊断工具；BO 池统计；`pipa-metal-withhold` 开关 |

## 3. 帧率

- 测量方法：`--arg pipa-touch=10245` 会在锁屏出现 30 秒后自动上滑解锁，10 秒后左右翻页 40 次。
  日志里的 `display: pacing` 行只统计间隔小于 200 毫秒的帧，也就是动画进行时的帧。
  **不要用每 10 秒的交换帧数算帧率**，那里面混进了静止的时间。
- 实测（GMEM、大小核）：主屏幕第 1 页（小组件）和第 2 页（4 个文件夹）之间翻页，约 **40 帧/秒**；
  有一轮翻到的是另一对页面，54 帧/秒。系统内存模式约 38 帧/秒。
- 用户肉眼：多数页面接近 60 帧；**滑向 App 资源库时只有约 35 帧**（GMEM），系统内存模式下非常卡。
- 帧间隔大约一半是 1 个刷新周期（16.7 毫秒），四成是 2 个周期（33 毫秒），看起来就是一顿一顿的。
- GPU 不是瓶颈：动画期间 GPU 忙碌率 25–40%，最重的一帧（8 遍渲染、141 次绘制、1690 万像素）GPU 时间约 15 毫秒，
  复制到屏幕约 4 毫秒。但命令从提交到完成平均 13–27 毫秒，主要是排队：GPU 只有一个队列，
  复制到屏幕的命令经常排在下一帧的渲染后面。
- 内存带宽（单核）：memset 18–19 GB/s，memcpy 约 8 GB/s。DDR 不在低档，带宽投票暂时不是瓶颈。

## 4. 进行中的排查：GMEM 模式下圆角被切

- 现象：小组件底板的四个角被斜着切掉，切口有杂点（用户照片）。截图（`fbcrop:`）放大看，圆弧部分由
  4×4、8×4 像素的方块组成，还有方块状空洞。应用图标的圆角正常，因为图标是自带遮罩的图片。
- 已确认：**系统内存模式（`sysmem,nocb`）下圆角正常**（用户亲眼确认），所以问题只在 GMEM 渲染遍里。
- 已排除：屏蔽 Apple GPU 家族（`pipa-metal-withhold=apple-families`）没有改善，所以和"读帧缓冲混合"无关。
- 也已排除：同时关闭 LRZ、UBWC、binning（`pipa-turnip-debug=nocb,nolrz,noubwc,nobin`）圆角还是坏的（第 8 节）。
- 下一步候选：
  1. （已做，无效）关闭 LRZ、UBWC、binning；
  2. 怀疑 GMEM 存回路径：渲染区域没对齐 GMEM 分块（A650 宽 96、高 16）或存回 S8 模板时，
     Turnip 用纹理单元直接读 GMEM（地址 = gmem_base 0 + 偏移，`tu_clear_blit.cc` 的 `store_cp_blit` /
     `store_3d_blit`）。可以试 `unaligned_store`（强制所有存回都走这条路，如果所有画面都坏了就是它），
     以及 GMEM 窗口放到 16 MiB（`pipa-gpu-gmem16m=1`，同时向 Turnip 报告 gmem_base）。

## 5. 其它已知问题

- **渲染服务器偶发停住**：约 20 轮里有 2 轮，在最初几帧之后不再提交，锁屏不动，滑动也没反应；
  没有崩溃，GPU 上也没有积压。和冷缓存无关（全新 Data 的一轮正常）。诊断工具已经就位：
  `process:` 每 10 秒一次的进程行，`stacks:` 在第 200 秒和重启前输出 backboardd、SpringBoard 的线程栈。
  下次碰到时，用 scratchpad 里的 `dsc_symbolize.py` 对照系统库解析地址。
- 小组件没有内容，只有底板（可能是小组件扩展进程的问题，还没查）。
- 复制到屏幕在一轮自测里失败过一次（`PF 1/1 r3`：第二帧的表面取不到资源）。
- 读日志程序偶尔要 8–9 分钟才回到 fastboot（Mac 弹出磁盘没被识别，只能等超时），已把超时改为 60 次，
  新镜像 `out/pipa/log-reader/reader.img`，旧的是 `reader-300.img`。

## 6. 常用开关

| 开关 | 作用 |
|---|---|
| `PIPA_GPU_USERSPACE=…/out/pipa/gpu-metal` | Metal 模式的插件附加包（带开关文件，界面走 GPU）。`…/out/pipa/gpu-selftest` 是自测版（界面走 CPU，只跑检查） |
| `--arg pipa-turnip-debug=…` | Turnip 选项，默认 `nocb`（GMEM）；`sysmem,nocb` 为系统内存 |
| `--arg pipa-touch=10245` | 自动解锁 + 翻页 40 次（低 8 位是触控阶段 5，高位是次数）；`517` 是 2 次，停在第 1 页 |
| `--arg pipa-gesture=60` | 脚本化触摸（不依赖触控芯片，见 10.9）：上滑解锁 + 两次翻页 + 一次点按，`60` 是晚探测之后第几秒开始（小于 45 按 45 算） |
| `--arg pipa-gpu-pool=768` | BO 池大小（默认 384 MiB；384 时经常用满，768 时峰值 605 MiB） |
| `--arg pipa-metal-withhold=apple-families` | 不报告 Apple GPU 家族（UniXNU-tb128fu 的做法） |
| `--arg pipa-gpu-apriv=0` | 关闭 APRIV（会重现黑屏，只用于对比） |
| `--arg sheng-data-reset=1` | Data 叠加层从种子重来（会清掉着色器缓存） |
| `PIPA_SQE_FIRMWARE=…` | 换 SQE 固件，路径必须在构建树内（`out/pipa/tree/.cache/...`） |

## 7. 注意事项

- **Mac 不能锁屏**：锁屏时登录窗口会拒绝挂载读日志程序的磁盘并立刻弹出，读日志程序随即重启，
  这一轮的日志就丢了。每一轮都用 `caffeinate -d` 包住。
- 改了子模块（ArmCpu、NativeAdreno、NativeMetal）以后，必须先在 UniXNU-pipa 里提交新的子模块指针，
  否则构建会把子模块切回旧版本（这次就因此白跑了一轮）。
- 改内核配置要构建两次（`--twice`）。
- 系统卷镜像在 `out/ipsw/21H461/044-76040-038.dmg`，这次只读挂载在 scratchpad 的 `sysos` 目录，
  用来读 QuartzCore 的 `default.metallib`。

## 8. 暂停时正在跑的那一轮

GMEM，`nocb,nolrz,noubwc,nobin`（日志 `out/pipa/device-logs/20261024-213825`）：**圆角仍然是坏的**，
第 200 秒和重启前两次截图都是 4×4 方块加空洞。所以 LRZ、UBWC、binning 都可以排除，
下一步直接查第 4 节候选 2：GMEM 存回路径（未对齐渲染区域、S8 模板，经纹理单元按 gmem_base 0 读 GMEM）。
这一轮动画帧率 38.3 帧/秒（只翻了 2 次页，样本少）。

---

# 续查（2026-10-24 深夜）：圆角问题已排除的方案

接手后续查了大约 10 轮。结论先说：**圆角的 4 行量化仍在，方向已经收窄到"GMEM 全屏 pass 的覆盖度计算"，
但还没找到根因**。按用户要求，这里把**所有失败的尝试**完整记下来，方便以后接着查或直接跳过。

## 9.1 现象与判据（可复现）

- 现象：小组件底板的圆角是**斜切倒角 + 锯齿杂点**（用户照片），App 图标圆角正常（图标是自带遮罩的位图）。
  系统内存模式（`pipa-turnip-debug=sysmem,nocb`）圆角正常。
- 判据：日志里的 `fbcrop:` 64×64 像素截图，看 `edge` 行（每行圆角左边缘的 x，两位十六进制）。
  - 正常：`27 26 26 25 24 24 23 23 22 22 21 21 … 1e`（每行递减约 1，平滑圆弧）
  - 损坏：`30 30 30 30 | 24 24 23 23 | 22 22 21 21 | 3e 3d 3c 3b | 24 24 24 24 | …`（**4 行一组**，且有别处的切片）
- 逐像素对照（`fbcrop: h` 行，64 行 × 64 像素的十六进制亮度）：GMEM 与系统内存**不是同一批像素换顺序**
  （逐行最小差 30–95），而是**内容本身更粗**：边界按 4 行一组恒定、4 列一组恒定。所以是**覆盖度按 4×4 像素块量化**，
  不是地址/置换错误。

## 9.2 已排除的方案（都上过机，全部无效）

| 尝试 | 参数 / 做法 | 结果 |
|---|---|---|
| 关低分辨率深度 | `pipa-turnip-debug=nocb,nolrz` | 圆角仍坏 |
| 关 UBWC 压缩 | `…,noubwc` | 仍坏 |
| 关分块可见性计算（binning） | `…,nobin` | 仍坏 |
| 三者同时关 | `nocb,nolrz,noubwc,nobin` | 仍坏 |
| 不报告 Apple GPU 家族 | `pipa-metal-withhold=apple-families` | 仍坏 |
| 强制所有存回走非快速路径 | `nocb,unaligned_store` | 仍坏（说明不在存回路径） |
| 强制所有加载走 3D 路径 | `nocb,3d_load` | 仍坏（不在加载路径） |
| 小尺寸离屏 pass 全部走系统内存 | 自加开关 `pipa_small-20000` | 仍坏 |
| 中等尺寸 pass 也走系统内存 | `pipa_small-500000` | 仍坏 |
| 只留全屏 pass 走 GMEM | `pipa_small-2000000` | 仍坏 → **损坏来自全屏 `1800x2880` 合成 pass** |
| 所有图像改线性布局、关 tiling/UBWC | 自加开关 `pipa_linear` | 仍坏（帧率 37.4）→ **不是内存布局** |
| 深度/模板附件假设 | 探针列出每个 pass 的 `ds` | 所有 pass **没有**深度/模板（`ds-1 st0`），排除 |
| 模板缓冲（S8 分离存回）假设 | 同上 | 排除 |
| MSAA 假设 | 探针记录每个 pass 的采样数 | 全部 `s1`（单采样），排除 |
| VRS / 分块内降分辨率着色（foveation） | 内核读 `RB_VRS_CONFIG`(0x88F4)、`RB_BIN_FOVEAT`(0x88F5) | VRS=0；foveat 寄存器在 A650 上**不存在**（读出毒值），排除 |
| 4 行像素置换（地址算错） | `fbcrop: h` 逐像素比对 | 不是置换（见 9.1），排除 |
| 小遮罩 pass 被 1/4 渲染 | 全量 pass 清单（32 条去重） | 没有 32×32 / 85×85 这类遮罩 pass，排除 |

## 9.3 现在确定的事实

1. 只在 **GMEM** 模式下坏；系统内存模式正常。
2. 损坏发生在**全屏 `1800x2880` 合成 pass**（把它切到系统内存就好了——即整个 UI 都用系统内存）。
3. 是**覆盖度/边缘按 4×4 像素量化**，不是像素被搬到别处，也不是内容更模糊的整体降采样。
4. 该 pass：`BGRA8`、单采样、无深度/模板、bin（分块）`384x368`、GMEM 偏移 0、tile divisor 1。
5. 分块大小对应 **每像素 8 字节**（只有一个 BGRA8 颜色附件却按 8 B/px 算），疑似还有第二个渲染目标——**这是最后一条未查的线索**。

## 9.4 最后两轮的计划

- 第 1 轮（已跑，Round O）：探针列出 pass 的**全部颜色附件**（`c44.xx.xx.xx`），确认是否真有第二个渲染目标。
- 第 2 轮：若确实有第二个附件 → 针对它查（很可能与覆盖度/遮罩有关）；若没有 → 在 Turnip 里加**逐 draw 的
  光栅状态记录**（scissor/viewport/`GRAS_` 覆盖度相关寄存器），定位最后一次机会。
- **两轮之后若仍无结论，按用户决定跳过圆角问题**，转去做 App 资源库卡顿（见 9.5）。

## 9.5 诊断代码的现状（都是未提交的临时改动）

- **内核**（`UniXNU-pipa/package/`）：
  - `BootRamoops.c`：`fbcrop:` 每 50 秒两组 64×64 截图（圆角 + 程序坞文字区），附 `edge`/`diff`/`reread`/`h`（十六进制转储）行；
    `native_gpu_rows` 行数 30→31。
  - `GpuProbe.cpp`：新增 `kRowTiling`（每 2 秒读 `RB_CNTL`/`GRAS_SC_BIN_CNTL`/`VSC_BIN_SIZE`/`RB_VRS_CONFIG`/`MRT0`），
    在 `gpu_stack_status` 的循环里调用。
- **Turnip**（`out/pipa/tree/.cache/mesa-work`，直接 `ninja -C build/mesa-tu-ios` 重建，**没有改仓库里钉住的补丁清单**）：
  - `tu_util.cc/.h`：`tu_pipa_note()` / `tu_pipa_note_dump()`（32 条去重字符串）、`tu_pipa_small_threshold()`（`TU_DEBUG` 里的
    `pipa_small-<像素数>`：小于该面积的 pass 强制走系统内存）、`tu_pipa_linear()`（`pipa_linear`：所有图像线性布局）。
  - `tu_cmd_buffer.cc`：`tu_cmd_render()` 里记录每个不同 pass 的 `rp 宽x高+dx+dy gmem c格式… ds格式 s采样 t分块 g偏移 div… st模板`；
    `use_sysmem_rendering()` 里接 `pipa_small`。
  - `tu_clear_blit.cc`：记录存回/加载路径（`st fast/a2d u未对齐`、`ld a2d/3d`）。
  - `tu_image.cc`：接 `pipa_linear`。
- **插件**（`out/pipa/tree/drivers/NativeMetal/NativeBridgeMetalFrontend.mm`）：
  - `Y pass 宽x高 f格式 s采样 n次数`：每种渲染 pass 的形状统计；
  - `Z …`：用 `dlsym` 读 Turnip 的 `tu_pipa_note_dump` 并打印（Turnip 在渲染服务器里没有日志出口，这是唯一通道）。
- 重建顺序：`ninja -C build/mesa-tu-ios` → `scripts/build-adreno-metal-plugin.py` → `scripts/make-owner-trust-bundle.py`
  → `make-gpu-addon.py --publish --clients iconservicesagent,Preferences ../gpu-metal/gpu-addon-clients.bin`
  （输出目录里旧的 `gpu-addon-clients.bin` 要先挪走，脚本拒绝覆盖）。

## 9.6 顺带发现的两个问题（未处理）

1. **`iconservicesagent` 被杀时内核 panic**：进程超 6 MB 内存上限被系统杀掉后，`pmap_assert_free`（`pmap.c:2907`）
   断言失败——进程退出时仍有用户态 CPU 映射没被回收。出现在 `unaligned_store` 那一轮，会提前终止该轮。
2. **帧率的测量口径**：`display: pacing` 只统计间隔 <200 ms 的帧；默认 GMEM 40.8 帧/秒，线性布局 37.4，系统内存 37–38。

---

# 2026-10-25 凌晨：卡顿修复（已完成）+ 圆角（转交子代理）

## 10.1 卡顿：两条根因，都已修好并上机验证

**测量方法**：`display: pacing`（动画期间帧率、帧间隔分布、提交到完成），以及插件遥测的
`T pipeline`/`V vkpipe`（管线创建次数与耗时）、`G commit-to-complete`、`K` 行（逐命令缓冲的
`rp/dr/kpx/ctc/gpu`，能直接看到"某个全屏 draw 的 GPU 时间"）。

**根因 1：Turnip 的磁盘着色器缓存被构建选项关掉**（`build-turnip-darwin.py` 里的 `-Dshader-cache=disabled`）。
每次启动、每个新管线都从 NIR 重编到 IR3：97 次管线创建累计 3.9 秒、最长 529 毫秒，渲染服务器整帧被拖住。

修法：
- `scripts/build-turnip-darwin.py`：`-Dshader-cache=enabled`；
- `drivers/NativeMetal/NativeAdrenoMetalPlugin.mm`：打开设备前 `setenv("MESA_SHADER_CACHE_DIR", <持久化缓存目录>)`，
  该目录就是运行时缓存的同一个目录（Data 卷上的 `.../m2v/`，跨启动保留）；启动时数一下目录里已有的
  Turnip 缓存文件个数，记成插件标记 `0x6D00|count`。

效果（同一套自动翻页脚本，冷→热缓存）：管线创建 3.9 s→0.86 s、最长 529→448 ms；VkPipeline 1.76 s→0.15 s；
动画 40.8→41.1 帧/秒。

**根因 2：Core Animation 走了 Apple 专属的混合路径（framebuffer fetch），App 资源库那类整屏模糊代价极高。**
证据：修改前最重的命令缓冲是 `rp1 dr1 kpx5062 … gpu=95ms ctc=388ms`——**一个全屏 pass、一次 draw，GPU 自己 95 毫秒**。

修法：`UniXNU-pipa/board.toml` 里把冗余的 `pipa-turnip-debug=nocb`（GpuProbe 的默认值本来就是 `nocb`）
换成 `pipa-metal-withhold=apple-families`（内核命令行 1000 字节上限内正好腾出位置）。

效果（同一脚本）：动画 **41.1→48.0 帧/秒**，帧间隔 ≤17 ms 的占比 65%→67%，提交到完成均值 39→18 ms，
GPU 侧 commit-to-complete 均值 25→11 ms，**95 ms 的全屏帧消失**（最重的 GPU 时间降到 2 ms）。
代价：CA 改走固定功能混合，画面观感需要肉眼确认（模糊背景是否仍在、颜色是否正常）——待用户确认。

## 10.2 圆角：仍然未解决，状态与交接

已排除的完整清单见第 9 节；今晚又补了两条：深度/模板附件不存在（`ds-1 st0`）；两个颜色附件都是 `44`(BGRA8)、
尺寸相同、`a2` 的 mip 未变。结论仍是：**GMEM 全屏 pass 里形状边缘按 4×4 像素量化，不是像素置换、不是布局问题**。

用户新提供的关键现象：**进入桌面布局模式拖拽卡牌时圆角正常，松手回到静态就变切角**。也就是说同一个
platter 在"动画/拖拽"时画对了，在"静态缓存"时画错了——这把方向进一步指向静态内容的缓存/重绘路径，值得优先查。

## 10.3 当前工作树状态（未提交，供接手者判断）

- `UniXNU-pipa/board.toml`：`pipa-metal-withhold=apple-families` 已设为默认（原 `pipa-turnip-debug=nocb` 注释掉）。
- `UniXNU-pipa/package/BootRamoops.c`、`package/GpuProbe.cpp`：圆角诊断（`fbcrop` 两组截图 + `edge/diff/reread/h` 行、
  `TILE` 寄存器行、GPU 行数 30→31）。
- `out/pipa/tree/scripts/build-turnip-darwin.py`：着色器缓存已开。
- `out/pipa/tree/drivers/NativeMetal/`：`MESA_SHADER_CACHE_DIR` + 缓存文件计数标记；遥测新增 `Y pass`/`Z`（读 Turnip 的 pass 记录）。
- `out/pipa/tree/.cache/mesa-work/`：**本地临时诊断补丁**（`tu_pipa_note*`、`pipa_small`、`pipa_linear`，见 9.5）。
  注意：这些改动会让 `build-turnip-darwin.py` 的补丁校验失败（"Unrecorded Mesa source changes"）。
  正式提交前要么把它们整理成 `patches/mesa/*.patch` 并登记进 `scripts/data/adreno-compiler-inputs.json`，
  要么 `git -C .cache/mesa-work checkout -- src/freedreno/vulkan/` 还原。**着色器缓存那条不受影响**（它是 meson 选项，不在源码里）。

## 10.4 圆角：已定位到 GMEM 输入附件路径，并用“换到系统内存”修好

**定位过程**（子代理分析 + 2 轮上机）：
- 探针新增每个 pass 的 `rpx sp<子pass数> in<输入附件数> c<颜色附件数> cpp…`。全屏合成 pass 是 `sp1 in2 c2 cpp4`：**两个输入附件**（framebuffer fetch，片元着色器读回颜色附件参与混合）。
- 这条读回路径（`tu_cmd_buffer.cc` 的 `tu_emit_input_attachments`，GMEM 分支把描述符改成 `TILE6_2`、`tex_line_offset = tile0.width*cpp`、`addr = gmem_offset`）**只在 GMEM 存在**，之前的 `unaligned_store`/`3d_load`/`pipa_linear` 都碰不到它。
- 因为它只在“部分透明”的像素上影响结果，所以只有圆角这类抗锯齿边缘显形；拖拽时 CA 换管线，所以看起来正常。

**修法（工作树，未提交）**：`tu_cmd_buffer.cc` 的 `use_sysmem_rendering()` 里默认让**带输入附件的 pass 走系统内存**
（`TU_DEBUG` 里的 `pipa_gmemin` 可以把它放回 GMEM，留给以后查描述符真正的错）。

**验证**（两轮，`pipa-touch=517` 停在第 1 页，圆角截图已改为自动定位）：
- 圆角边缘从 `30 30 30 30 | 24 24 23 23 | …`（4 行一组）变成逐行单调（`29 18 14 11 9 7 5 4 2 1 0`）；
- 帧率 47.9 帧/秒，GPU 侧 commit-to-complete 均值 **7.4 ms**（修复前 11.8，更早 28）；
- 235 次交换，界面正常。

**副产品**：内核探针 `fbcrop` 现在会自动找“第一条 40 像素以上的亮带”来定位小组件左上角（固定坐标在 UI 位置变化后会落空）。

## 10.5 最终配置（截至 2026-10-25 01:40）

1. Turnip：`-Dshader-cache=enabled`（构建脚本已改）+ 插件设 `MESA_SHADER_CACHE_DIR` 到持久化目录；
2. `board.toml`：`pipa-metal-withhold=apple-families`（换掉冗余的 `pipa-turnip-debug=nocb`）；
3. Turnip：带输入附件的 pass 走系统内存（默认）；`pipa_gmemin` 可关闭；
4. 帧率对比：最初 40.8 → 现在 **47.9–49.4 帧/秒**，全屏 95 ms 的帧消失，commit-to-complete 均值 28 → 7–11 ms。

## 10.6 让平板停在苹果系统并息屏（2026-10-25 01:55）

新增开关：`pipa-power-at=<秒>`（`package/Buttons.c`）。按键线程启动这么多秒后，通过同一个 HID sink **合成一次电源键按下**
（按下 150 ms 后释放），iPadOS 因此锁屏并把显示关掉（显示驱动收到全黑交换时把面板刷黑）。

已验证的一轮（`pipa-power-at=150`）：日志出现 `buttons: power press at 150 s`，之后交换次数停在 14、`fbcrop` 全黑。

留在苹果系统的做法（不再自动重启回 fastboot）：
```
PIPA_GPU_USERSPACE=…/out/pipa/gpu-metal scripts/kernel-round.sh --build-only \
  --arg pipa-restart-after=0 --arg pipa-power-at=150
fastboot boot out/pipa/boot/boot-kernel-round.img
```
注意内核命令行 1000 字节上限：上面两个参数加起来需要临时注释掉 `pipa-touch=5`（14 字节）才放得下；
本次已恢复 `pipa-touch=5` 与 `pipa-buttons=1`。

## 10.7 遥测表溢出：U/V/K 行其实早就没再更新（2026-10-25 下午，性能分析第一轮）

**发现**：插件把遥测表整张重发（`NativeBridgeMetalFrontend.mm` 的 `native_bridge_metal_telemetry`），
内核只存**前 48 行**（`NativeAdrenoPublish.cpp` 的 `native_adreno_metal_rows[48]`），`BootRamoops.c` 按行号比对文本、
只打印变化的行。Turnip 的 `Z` 行（`tu_pipa_note_dump`，最多 32 条去重字符串）当时排在最前面，把表尾整段挤出了 48 行窗口：
`J surface cache`/`V gpu`/`U`/`N`/`V vkpipe`/`K`/`E` 停在**启动时**的值不再变化。20261025-075333 那一轮里，
`U n=16 cpu=199ms` 的 16 帧、`V gpu timed 44` 的 44 个命令缓冲，都是交互开始前的静态桌面帧，
不是那 45 秒交互的数字；"每 draw 150 µs" 是拿它除以整轮 82 draw/帧得来的，**不成立**（那些帧每帧只有 1 个 pass、2 个 draw）。

**改法**（只动插件，未动 board/内核/Turnip）：
- 行序按阅读顺序重排：S、U、G、J×5、**A0/A1**、V gpu、**A2**、T×3、N（含 region）、V vkpipe/cache、W、F、SR、P0/P1、C0-3、
  Y×6、Q、K×4、E，`Z` 最后按剩余空间给（现在最多 6 行）；`row()` 加了 48 行硬预算。
- 新行（每类调用：次数、累计 ms、单次均值 µs）：
  - `A0 draw … | bind … | blit …`：`nmb_render_draw*`、各种 `set*`（buffer/texture/sampler/bytes/pipeline）、
    `NBBlitEncoder` 的 copy/fill；
  - `A1 open … | close … | commit …`：`nmb_render_encoder_new`、`nmb_encoder_end`、`nmb_command_buffer_commit`（含 flushSurfaces）；
  - `A2 gpu pass … | copy …`：按命令缓冲种类拆 MTL 的 GPU 时间（带 pass 的 / 只有 blit 的＝每帧一次扫描输出拷贝）。
- `N` 行末尾加 `region n=… …ms/…us …MB`：`replaceRegion:` / `getBytes:` 的 CPU 上下传（全屏回读 20 MB 会被它抓到）。
- 判读：`U` 的 n 应≈帧数（≈1894），否则表又溢出了；`draw+bind+blit+open+close+commit` 与 `U` 的 cpu 总量对得上，
  剩余的就是还没归类的调用（viewport/scissor/raster 状态、ObjC 记账）。
- CDHash 变了（bundle `56cd7612…`），Data seed 与 owner trust 需按 9.5 的流程重新生成。

## 10.8 一帧 26.8 ms 的账目，与扫描输出拷贝（2026-10-25 第二轮）

**20261025-084558 那一轮（19 帧）第一次把 CPU 账算平**（A0/A1 行，每帧）：draw 3.8 + bind 0.16 + blit 5.7 + open 0.6
+ close 2.1 + commit 3.8 = **16.1 ms**，与 `U cpu=129ms/n=8` 的 16.1 ms 完全吻合；GPU 侧 `A2 pass 93ms + copy 69ms = 8.5 ms`
一帧。两者串行（每帧末尾 `J completed wait` 一次）。

**扫描输出拷贝**（`copy` 那 4.3 ms GPU + 5.7 ms CPU）：内核显示驱动把帧交给 GPU 客户端
（`NativeLegacyDisplay.cpp` 的 `native_legacy_gpu_offer` → `NativeGOPPresenter` 的 WAIT_SWAP/COMPLETE_SWAP 交接）→
`gpuscanout::Client::serveOne`（`GpuScanoutClient.h`）→ `gpupresent::Presenter::present()`（`GpuScanoutPresenter.h`），
一次 blit 把合成 surface 写进**固件 GOP framebuffer**。XNU 没有 MDSS/DSI 驱动，面板跟着 bootloader 配的那块固定
framebuffer 走，所以"不拷贝"等于写 MDSS KMS（改 SRC0_ADDR/STRIDE + vblank 提交 + SMMU 映射离散 surface 页），不是一轮的活；
CPU 直接写 device 内存是 143–285 ms/帧，也不能用。能做的是让这一次拷贝真的只发一条命令：

- surface pitch ≠ scanout pitch → presenter 用"线性纹理包住源页"；runtime 见到 buffer-over 纹理就走
  `blit_texture.rs` 的 `rows_copy`，把 1800×2880 拆成 **2880 个 VkBufferCopy**（Turnip 逐 region 发包 = CPU 5.5 ms）。
- **改法**（`bridge/patches/0011-one-copy-for-a-padded-frame.patch`）：纹理的 image 就是 buffer 自己的字节
  （`aliases_buffer()`）且行不连续时，改走**一个** `vkCmdCopyImageToBuffer`（`bufferRowLength` 表达目标 pitch）；
  `plan_copy` 拒绝就回退原路径。
- presenter 另外把线性纹理跟着映射缓存（原来是每帧建一个 MTLTexture），并把 present 拆成 import/texture/encode/
  submit/wait 计时（A4 行）。

**新行**：`A3`（draw 按"每 encoder 第一个 / pipeline 变更后 / 与上次相同"分桶）、`A4`（present 的五段 + 拷贝形态 + 纹理
创建/复用）。判据：`A4 enc` 应从 ~5500 µs 掉到 <300 µs、`A0 blit` 均值 <300 µs、`A2 copy` <3200 µs、`A4 t3/16`；
`A3` 决定"每 draw 264 µs"是 pass 首帧建立还是每 draw 固定开销。

## 10.9 脚本化触摸注入：无人值守的上滑、翻页、点按（2026-10-25 下午）

**为什么要它**：上机轮次里界面的变化全靠人守在上滑解锁、左右翻页，人不在就只能拍到锁屏的静止帧（连续三轮日志只有
12–19 次 swap，是没人碰屏，不是卡死）。已有的 `pipa-touch=10245`（`package/TouchProbe.cpp` 的 `autoTouchThread`）虽然
也做同样的事，但要触控芯片走到阶段 5、还要 GPU 交接出过帧，依赖太多；这次做的是**不依赖任何硬件**的注入。

**注入点**：写 `NativeHidInput` 报给系统的"手指集合"，也就是面板驱动自己用的那个接口，往上全是真的：

- 生产者协议：`package/TouchState.cpp:24-36`（序列锁 + `native_touch_set_sink` / `native_touch_published`），真实生产者是
  `package/TouchProbe.cpp:313-330` 的 `publish()`；
- 消费者：`drivers/NativeHidInput/NativeHidInput.cpp:155-206` 的 `touchSend()`，把一帧手指编成 report 3 交给
  `handleReport`；
- 再往上：原版 IOHIDEventDriver → digitizer 事件 → `NativeHidInput.cpp:93-111` 的 `dispatch` 包装补"来自边缘"的标记
  （`edge::mark`，同文件 `:76-92`；SpringBoard 的主屏手势没有这个位就全废）→ backboardd。

不选另外两层的理由：IOHIDEvent 层要自己造 digitizer 子事件、还要拿到 backboardd 认的 event service（`Built-In` /
`DisplayIntegrated` / `PrimaryUsage` 三个属性，见 `package/HidInputProbe.cpp:118-133`），比复用一条已经上机验证过的链路麻烦
得多；touch 驱动层要 Novatek 芯片和固件在位，而且只能顺着真实报告走。这一层唯一"不真实"的地方，是少了控制器那一帧
SPI 报告。

**用法**（`package/Gestures.c`）

```
scripts/kernel-round.sh --arg pipa-gesture=60
```

（参数进的是 payload 里的命令行，所以必须重新构建，`--no-build` 用不了。）

- 默认**关闭**：不传就什么都不做，命令行 0 字节；
- 值是"晚探测之后第几秒开始"；小于 45 按 45 算（SpringBoard 约 +50 s 才有锁屏），上限 900；
- 开始前还要等"至少 2 帧上屏"（和 `package/HidInputProbe.cpp:37` 同一门槛），所以 `pipa-gesture=1` 就是"能开始就开始"；
- 命令行成本：`pipa-gesture=60` 15 字节 + 1 个分隔空格 = **16 字节**（当前 975 / 上限 1000，剩 25）。

**脚本**（坐标是面板像素 1800×2880，转换和边缘带判定在 `package/GesturesCore.h`）：

1. `unlock` (900,2870)→(900,1300)，300 ms、31 帧：起点必须落在 NativeHidInput 判定的边缘带（底部 2.4% = 像素 2812 以下），
   否则 SpringBoard 不认主屏手势；
2. 停 10 s（5 s 时首次翻页会太早，2026-10-24 的结论）；
3. `page next` (1440,1440)→(360,1440)，300 ms；停 1.5 s；
4. `page back` (360,1440)→(1440,1440)，300 ms；停 3 s；
5. `tap` (900,2000)，80 ms。

每帧间隔 10 ms（与面板自己的报告率一致），全程约 17 s，之后线程结束，不会留一根按下的手指。

**上机判据（一行）**：日志里的 `gesture:` 行，例如

```
gesture: unlock (900,2870)->(900,1300) 300 ms, 31 frames: hid +31, display +7
```

`hid +31` = 这一步的帧都进了 IOHIDEventDriver（帧数或略少：工作环把排队的动作合并时，后一帧会顶掉前一帧），`display +7` =
这一步之后又上屏了 7 帧（>0 就是系统真的动了）。
`hid +0` 表示没人收（`sheng-touch-hid=1` 没开，或 event driver 还没挂上），同时会打 `gesture: error -3`；一行都没有且
`gesture: stopped: no frame presented in 180 s` 表示显示没起来。整轮汇总在最后的
`gesture: done at N s: hid +M, K display frames`。

**注意**

- 与 `pipa-touch=5` 的真实面板同时写序列锁时理论上会偶发一帧撕裂（消费者比对序号会重试，最坏丢一帧）；没人碰屏时面板
  几乎不产帧，风险很低，但**没有上机验证过**；
- 不要和 `pipa-touch` 的高位次数（`pipa-touch=10245` 那种自动解锁翻页）同时开：两条脚本会互相插进去，手势就乱了；
- 全部在晚探测之后的内核线程里跑，失败只写 `native_gesture_error` / 日志行：不碰 IOService、不分配内存、不持锁睡眠，
  不可能 panic 或卡启动；
- 主机测试：`cc -std=c11 -Wall -Wextra -Werror package/GesturesTests.c && ./a.out`（坐标换算、边缘带、描点端点精确落位）。

## 10.9 手势轮的数据与扫描输出拷贝的三层原因（2026-10-25 第三轮）

**20261025-092620**（218 帧、46.4 fps、69% 帧 ≤16.7 ms，无人值守手势）把账算清了：

- CPU（渲染线程，`U`）10.6 ms/帧 = draw 3.2（47 draw × 68 µs；`A3` 证明 264 µs 是启动帧污染：first 202 / state 60 / same 46）
  + close 1.23 + commit 0.65 + open 0.33 + bind 0.16 + **未归类约 5 ms**（还没计时的 encoder 状态调用：viewport/scissor/
  depthStencilState/raster/blend，以及 CA 自己的记账）。
- GPU 9.6 ms/帧 = 渲染 commit 5.25 + 拷贝 commit 4.35。
- 两者串行：帧 21.6 = 10.6 + 7.1（`J completed wait`，每帧一次）。**拷贝的总代价 ≈ 4.35（拉长那次 wait）+ 2.6（encode）≈ 7 ms/帧**。

**一次 `vkCmdCopyImageToBuffer` 为什么还是 2.6 ms CPU / 4.35 ms GPU，三层原因：**
1. presenter 走 form 1（线性纹理）：surface rowBytes ≠ scanout stride（`A4` 的 geo 打两个数）。
2. Turnip 的线性 image pitch 是 **256 字节对齐**（`fd6_layout.c:207`：cpp=4 时 `pitchalign = cpp_shift + 6`），1800 宽 →
   align(7200,256) = 7424；CA/IOSurface 的 rowBytes 通常 64 对齐（7232/7296）→ image 不 alias → runtime 退回
   `blit_texture.rs` 的 `rows_copy`（2880 个 region）。
3. **即使 alias 成功**，Turnip 也只在外层 buffer 的 pitch 64 字节对齐时才发一条 2D blit：
   `tu_clear_blit.cc:3070`（image→buffer，`if ((dst_va & 63) || (pitch & 63))` 就每行一条）与 `:2844`（buffer→image，源 buffer 侧同理）。

**三条路**：① surface stride == scanout stride —— 不可行（surface 由 IOSurface.kext 分配，scanout stride 是 GOP 的，
没有 MDSS 驱动改不了）；② compute 重排 pitch —— GPU 流量翻倍（41 MB → 82 MB，≈8–9 ms），只省 2.6 ms CPU，不划算；
③ 让 Turnip 走 2D 快路径 —— 唯一可行，前提是 runtime 发出的命令是 buffer-image 形式且两侧 pitch 都 64 对齐。
实现方式：给 buffer-texture 的线性 image **显式 pitch**（`VK_EXT_image_drm_format_modifier` + `DRM_FORMAT_MOD_LINEAR` +
`VkSubresourceLayout.row_pitch = Metal 的 rowBytes`；runtime 已有这套机制，见 `dma_buf.rs:736`；Turnip 对显式 layout
只要求 64 对齐，见 `fd6_layout.c:190`）。**本轮未实施**：正确性完全依赖 Turnip 对显式 layout 的处理，无法在设备上验证。

**本轮加的遥测**（`A4` 拆两行 + `A5`/`A6`，都在 `NativeBridgeMetalFrontend.mm`）：
`A4 present <n> <form0/1/2> geo <fW>x<fH>/<fStride> o<oW>x<oH>/<oStride> tx<made>/<reused>`;
`A5 imp/tex/enc/sub/wait µs | copy <n> img<a> rows<b> reg<rowsRegions>/<imageRegions> r<lastRows>`;
`A6 buffer tex alias<a> no<n> pitch <layoutPitch>/<imagePitch> align<alignment>`（runtime 侧计数：
`copy_census()` / `buffer_texture_census()`，补丁 0011 更新 + 0012）。

## 10.10 渲染侧 CPU：状态调用分桶 + 去重（2026-10-25 第四轮）

**几何数字（20261025-094200）定了拷贝的结论**：surface pitch **7424**（=align(7200,256)，正好等于 Turnip 的 layout pitch，
所以 `A6 alias13 no0 pitch 7424/7424` —— runtime 的 image 确实 alias 了），扫描输出 pitch **7200**（7200 % 64 = 32）。
Turnip 的 2D 快路径要求 buffer 侧 pitch 64 对齐（`tu_clear_blit.cc:3070`），过不了 → 每帧仍是 2880 行 blit；
`A5 copy 480 img480 rows0` 证明我们发的**已经是**一条 `vkCmdCopyImageToBuffer`。所以"合并命令"到头了，
除非写 MDSS KMS 做零扫描输出拷贝。本轮不动拷贝。

**改打渲染侧**：`U cpu` 12.2 ms/帧（228 帧 2789 ms），已计时的是 draw 3.6 + close 1.36 + commit 1.27 + open 0.33 + bind
0.16 ≈ 6.7 ms，其余落在 encoder 状态调用、资源创建和 CA 自己的记账上。本轮：

1. **分桶计时**（`NativeBridgeMetalFrontend.mm`）：`ps`（管线）/`vp`（viewport）/`sc`（scissor）/`ds`（深度模板状态）/
   `ra`（cull/winding/fill）/`mix`（blend + stencil ref + depth bias + line width + visibility）/`new`（纹理、buffer、
   surface 导入），各带次数、总量、均值；`A7`、`A8` 两行。
2. **去重**（同一处）：每个 encoder 记住自己最后下发的值，**值相同就跳过 runtime 调用**，并把跳过的次数记进 `sk<计数>`。
   等价性：Metal 的状态是 encoder 自己的，同一 encoder 内重复设同一个值不改变任何状态；runtime 的 setter 只是存值
   （`set_render_pipeline_state` 的重复调用只会用同一 pass 算出同一 routing）；每个 encoder 的"最后值"从零开始
   （新对象 ivar 清零 = Metal 的默认值）。比较用的是**下发的字节**（double 按位、结构体 memcmp），差一位就照发。
3. `raster` 不再用 `objc_setAssociatedObject`/`objc_getAssociatedObject`（每次 setCullMode/setFrontFacing/setTriangleFillMode
   都会分配 NSNumber 并撞 Objective-C 运行时的全局锁），改成 encoder 的普通 ivar；语义不变（未设 = 0 = Metal 默认
   None/Clockwise/Fill）。

**下一轮判据**：`A7/A8` 看哪桶最大、`sk` 有多少；`U cpu` 应从 12.2 掉到 <9 ms（若 CA 大量重复下发同样的 viewport/scissor/
管线）。若所有桶加起来仍 ≪ 5 ms，剩下的就是 CA 自己的 CPU，插件侧无从下手，得从"每帧 3.9 个 pass × (open 90 + close 258) µs"
和 commit 606 µs 上找。

## 10.11 三个关键调用的内部分段（2026-10-25 第五轮）

**上一轮的分桶否掉了"状态调用 5.8 ms"**：`A7 ps 10857 24ms/2us sk0 | vp 1167 3ms/3us | sc 3401 3ms/1us`、
`A8 ds/ra/mix 0 | new 215 61ms` —— 状态调用合计 ~0.03 ms/帧，去重也没命中（`sk0`，CA 本来就不重复下发）。
那 5.8 ms 其实是 `A0 blit`（扫描输出编码），上一轮对账时漏掉了。

`c7`（266 帧 / 47.7 fps）CPU 已算平：draw 3.75（53.3×70µs）+ blit 2.70（2.1×1.36ms）+ close 1.45（5.5×266µs）
+ commit 1.23（2.1×593µs）+ open 0.44 + bind 0.15 = **9.7 ms ≈ U cpu 10.0 ms**；GPU 10.3（render 6.0 + copy 4.3）。

**本轮只在 runtime 里加分段计时**（`encoder.rs` 的 `phases` 模块 + `phase_timings()`，补丁 0013；桥
`nmb_phase_timings`；前端 `D`/`C`/`B` 三行）：

- `D draw us settle… cmd… heap… rec… n…` —— `draw_resident` 四段：进入→`settle_draw` 返回（bind state/变体/sampler maps）、
  →`instantiate_draw` 返回（命令构建）、→`instance` 解析前（`heap_elements`/`texture_table`/bounds/stand-in）、其余（记录 + 尾部 retain）。
- `C close us pass… sh… book… n…` —— `end_pass()`（附件收敛/resolve 重建）、`end_with_replay` 里的 shadow copies +
  `vkEndCommandBuffer`、以及 `encoder_ended`/`keep_replay`/分段组装这些记账。
- `B commit us state… prep… plan… sub… n…` —— `commit_now` 的状态收集（`state.reached`/`live_buffers.reach`/`prepare`）、
  host step 准备、批次规划（`point_root_tables`/sampler listings/heap commit）、`scheduler.commit`（→ Turnip `vkQueueSubmit`）。

都是纯读时钟、不改控制流；`A0/A1` 的前端计时仍在，两者相加可交叉验证（D 四段之和应≈`A0 draw` 均值，差的那部分在 `draw()` 的
`sync_heaps`/coverage 记账里）。

**读代码得到的候选**（等 D/C/B 指认）：每 draw 会 `device().argument_samplers()`（`sampler.rs:466`：锁 `resource_ids` +
把**所有活着的 sampler state** 克隆成 Vec）、`heap_sets::texture_table()`（`heap_sets.rs:1287`：同一把 device 堆锁 + Vec）
和 `heap_sets::bind()`（`heap_sets.rs:1346`：再次取同一把锁 + `vkUpdateDescriptorSets`）；commit 的 `sub` 段落到
`tu_knl_darwin.cc` 的 `darwin_submit_chunk`（内核提交 + BO 退役）。

## 10.12 分段计时的答案与实机 MDSS 布局（2026-10-25 第六轮）

**D/C/B 三行（`20261025-100250`，201 帧 / 4.33 s = 46.4 fps）**：

```
D draw   us settle13 cmd7 heap8 rec35     n9830     ← rec（记录命令本身）35 µs
C close  us pass311 sh2 book18            n752      ← end_pass 311 µs
B commit us state12 prep10 plan17 sub697  n429      ← vkQueueSubmit 697 µs
```

结论：draw 的成本不在锁/查表/sampler 克隆（那三个嫌疑全被否掉），而在**记录命令本身**；close 的成本几乎全在
`end_pass()`（附件收敛 + `vkCmdEndRenderPass`）；commit 的成本几乎全在 Turnip 的 `darwin_submit_chunk`（内核提交 +
BO 退役）。三者都是 Turnip/提交层的固有成本，插件侧能抠的很少 —— **"把 6.4 ms 砍到 3 ms"这个目标不成立**。

**每帧 2.1 次 commit，其中一次就是扫描输出拷贝的 commit。** 所以零拷贝同时省下：拷贝 GPU 4.34 ms + 拷贝编码
2.70 ms（`A0 blit`）+ 那次提交 0.70 ms（`B sub`）≈ **7.7 ms/帧**，比抠 CPU 划算一个数量级。完整计划见
`HANDOFF-pipa-zerocopy.md`（P0 只读探针 → P1 静态翻转 → P2 单缓冲在线 → P3 N 缓冲与生命周期）。

**P0 实机读数（`20261025-101652`，日志被读取器 60 s 超时截断，只拿到第一遍）**：

```
mdss: MDSS video base 9c000000 row 7200 1800x2880 depth 32
mdss: MDSS video fmt BBBBBBBBGGGGGGGGRRRRRRRR disp 1 off 0 len 0 rot 0 scale 1
mdss: VIG0 sz 0b400384 xy 00000000 a 9c000000 st 00001c20 f 000237ff L
mdss: VIG1 sz 0b400384 xy 00000384 a 9c000000 st 00001c20 f 000237ff L
mdss: VIG2/DMA0..3 全 0
mdss: CTL0 layer 01000005 01000028 flush 00000000 intf 00000006 pipe 00000000
```

- 扫描输出确认是固件那块 **0x9c000000**（1800×2880，stride **7200**，`a8r8g8b8`）
- **两个平面在扫**：VIG0 = 900×2880 起点 x=0，VIG1 = 900×2880 起点 **x=900**（左右半屏各一个），
  **两者 `SRC0_ADDR` 相同**，右半屏靠 `SRC_XY` 的 x 偏移
- 其余 6 个 SSPP 全空 ⇒ 翻转时每半屏只需写 `SRC0_ADDR` + `SRC_YSTRIDE0`（7200 → 7424），再 flush 一次 CTL0
- 还没读到：SMMU 判定（旁路/页表，决定工作量 9 轮还是 17 轮）、真实刷新率、合成器轮换的 surface 块数
- 小 bug：presenter 发布的 `gop phys … bytes 0`（驱动侧是 20736000）

## 10.13 零拷贝的第一次使能把设备挂死（2026-10-25 第七轮）

**P1c（SMMU 翻译闸门）上机后平板彻底失去响应**：`fastboot devices` 空、USB 完全没枚举、内核"10 秒后
回 fastboot"的定时器也没跑到，只能长按电源强制断电。这是本次性能工作里唯一一次把设备搞死。

判据链：
- 第 1 次尝试是老的"合成器没画"（swaps=0），自动重试；
- 第 2 次跑到 `flip:` 的 P1b 分配失败之后就没回来，脚本等了 15 分钟；
- SMMU 相关的日志只有 `mdss:` 的只读行（探针每 50 s 照常在跑），**没有任何 `gate step` 行**——说明卡在
  `gatePrepare()` 之前/之中，而不是 enable 之后。

**最可能的原因**：`SRC0_ADDR` 是 32 位、stream 是 bypass；要零拷贝必须把 stream 切到 translation，而 DPU
取帧时只要有一条访问没被我们的恒等映射覆盖（例如 `CTL_LAYER` 里使能但 `SRC0_ADDR = 0` 的未用平面），
就会产生 AXI 事务挂起，把内存通路一起拖住 → 整机失去响应。context 里配的 `continue_after_fault` 挡不住
总线层面的挂死。

**现在的状态与约束**：
- `pipa-flip` **默认改为 0（关闭）**：不 map、不写、不起线程。要跑必须显式 `--arg pipa-flip=1`（只做写回
  测试，已验证安全）或 `2`（P1b+P1c，**可能挂死，必须在有人能断电时跑**）
- 安全镜像已构建：`out/pipa/boot/boot-kernel-round.img`（`--arg pipa-gesture=60`，无 flip）
- 若继续这条线，必须做到以下几点之一才能再上机：
  1. 先把所有可能被 DPU 读到的地址（含未用平面）在表里映射成「指向一块自有黑 buffer」，再用
     `CTL_LAYER` 关掉未用平面（这是寄存器写，需先证明安全）；
  2. 或者用一个硬件看门狗（PMIC/PON 的复位定时器）在上机前武装，挂死时自动复位回 fastboot；
  3. 或者放弃零拷贝：扫描输出拷贝 4.35 ms GPU + 2.7 ms CPU 是当前 49–52 fps 的主要代价，接受它。

**已确认的硬件事实（不必重查）**：framebuffer 0x9c000000 / stride 7200 / 1800×2880 / a8r8g8b8；两个 VIG 平面
各 900×2880，同一基址靠 `SRC_XY.x` 分左右；`SRC0_ADDR` 32 位；XNU 管理的物理内存从 0x10007c000 起，
**4 GiB 以下没有可用空间**（21 MB 连片段都分配不出来）。
## 11. 内存：为什么用不到第三段 8 GiB（2026-10-25，只读调研）

见 **[HANDOFF-pipa-memory.md](HANDOFF-pipa-memory.md)**。一句话：2026-10-24 那次 7.75 GiB @ 32 GiB
其实没走到 `arm_vm_init`，而是死在 `osfmk/arm64/start.s` 的引导页表池（需要 18 张表、池子只有 16 页），
所以一个字节日志都没有、屏上只剩蓝色 stage-1 条。**跟基址在 32 GiB 无关**，同样大小的窗口放在 4 GiB
基址也一样会死；现有 5.75 GiB 配置只剩 2 页余量。要拿到第三段的 8 GiB：先把池子从 16 页加到 34 页
（零风险），再试"单一区间 7.75 GiB"（+2 GiB），最后做"多段不连续"（内核留在 4 GiB bank，+8 GiB）。
