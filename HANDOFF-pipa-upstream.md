# 上游可同步项调研（2026-10-25，只读）

> 目的：回答"上游有没有值得同步的提交能帮到我们"。**本次只读**：没有改任何 pin、没有改代码、
> 没有上机。所有 `git fetch` 都只更新了 `origin/*` 引用。
>
> 对应问题：`HANDOFF-pipa.md` 的 10.7–10.13（遥测溢出、帧账目、扫描输出拷贝、渲染侧 CPU、
> 分段计时、MDSS 布局、零拷贝挂死）与 `HANDOFF-pipa-zerocopy.md`。

## 0. 一句话结论

1. **metal2vulkan**：pin `720ef1c`（10-14）落后 `origin/master` **66 个提交**，其中第一个就是
   **+107,644/−20,468 行、1298 个文件**的大合批 → **无法逐条摘取，只能整体同步**。但这 66 条里
   有一打正中我们的痛点：每 draw/每 pass 的分配与锁（`86b5d6f`）、encoder 绑定状态跨帧保留
   （`5c799f5`）、把一帧的多次 `vkQueueSubmit` 合并成一次（`ca8340b`）、draw 不再等管线编译
   （`0b8edc8`）、记录命令移出编码线程（`c9b0d74`）。**代价**：我们 13 个补丁里 8 个要重做，
   其中 `0008`（padded 行 alias）被上游**删除**（`PaddedReadsPolicy` 整体移除），做错了会让我们
   1200×2000 的 surface 退回"每 pass 影子拷贝"的老路。
2. **Mesa/Turnip**：pin `85fda3b`（06-30）落后 `origin/upstream/main` 7155 个提交（`src/freedreno`
   542 个）。**值得单独摘的只有 4–6 条小补丁**。其中两条**直接可能击中圆角问题**：
   `d8169d64a40`（GMEM 布局不可行时 `gmem_offset` 未初始化，**我们的 pin 确实有这个 bug**）与
   `5000d6644db`（tile divisor 跳跃时中间层 tiling 配置未初始化）。另外 `761d5c8a54` 是 A650 上
   一行寄存器值修正（`GRAS_MODE_CNTL` 0x0→0x2，上游 bug 报告就是"覆盖度/样本掩码画错并挂死"）。
3. **扫描输出拷贝（10.9/10.10 的 7.7 ms）上游没有解**：`tu_clear_blit.cc` 的 `pitch & 63` 快路径
   条件与 `fd6_layout.c` 线性 image pitch 256 字节对齐**两处逐字未变**，上游也没做 image→buffer 的
   新路径。这条只能继续走 MDSS 零拷贝或 `VK_EXT_image_drm_format_modifier`，不要指望同步。
4. **Linux**：mainline **有** `sm8250-xiaomi-pipa.dts`，我们 10.12 的硬件事实（0x9c000000 / 7200 /
   1800×2880 / a8r8g8b8 / carveout 0x2300000）逐条对得上。DPU 6.0 的 flush 语义有更新（SM8250 走
   `trigger_flush_v1`，除 `CTL_FLUSH` 外还可能写 `CTL_INTF_FLUSH` 等子块），近期 dpu1 的修复基本是
   v12/v13（SM8750/Kaanapali），对 SM8250 无直接补丁。**一个新线索**：display 的 `iommus` 是
   `<&apps_smmu 0x820 0x402>`——一个组覆盖多个 stream，P1c 挂死可能不只是"未用平面地址 0"。
5. **QCA6390**：硬件已确认（上次 Android 启动的 dmesg：`cnss2 b0000000.qcom,cnss-qca6390`，PCIe **0**，
   5 路 regulator）。mainline 有一条**比我们设想更简单**的成熟路径：`qcom,qca6390-pmu`（regulator
   binding）+ 9 路固定 LDO + `pwrseq-qcom-wcn`，ath11k 自己带 MHI。**同代同厂**的
   `sm8250-xiaomi-elish`（小米平板 5）在 mainline 里就是完整可抄的例子。

---

## 1. metal2vulkan（GPU 插件的翻译层）

### 1.1 仓库状态

| 项 | 值 |
|---|---|
| 路径 | `out/pipa/tree/.cache/gpu-reference/metal2vulkan` |
| 当前 pin | `720ef1ce9ae2a2068999a8675717418b9416283e`（2026-10-14），`HEAD` detached |
| `origin/master` | `a2c440b`（2026-10-19） |
| `origin/development` | `4d695b7`（2026-10-25），比 master **多 1265 个提交**（不追） |
| 落后 | `720ef1c..origin/master` = **66 个提交** |
| 结构 | 第 1 个提交 `0175289` 是 +107,644/−20,468、1298 文件的大合批，之后 65 个增量 |

**这条决定了同步策略**：因为 `0175289` 是一个巨型 squash，**任何"只挑三条"的方案都不成立**
（后 65 条全部建立在它之上，单摘会大面积冲突）。要么整体同步，要么不动。

构建约束（`scripts/build-native-metal-bridge.py:17-27`）：HEAD 必须等于 pin，且
`git diff HEAD` 必须**恰好等于 pin + `drivers/NativeMetal/bridge/patches/*.patch`**。所以同步 =
改 pin + 重做全部补丁 + 更新 `scripts/data/gpu-metal-vulkan-inputs.json`。

### 1.2 与我们的痛点直接相关的提交

下表按"对我们 10.x 的哪一条有用"排序。"改动"是 `--numstat` 的真实规模。

| commit | 日期 | 内容 | 为什么相关 |
|---|---|---|---|
| `86b5d6f` | 10-19 | **Cut the encoding thread's allocations, locks and copies per draw and per render pass**（+2054/−785，46 文件） | 正中 10.12 的结论：`D rec 35 µs/draw`、`C pass 311 µs/pass` 都是"记录命令本身"。这条把 render pass 按形状缓存（最多 256 个）、draw 的命令存储复用、pass 的形状列表改成就地存放、去掉 per-draw 的锁 |
| `5c799f5` | 10-19 | Keep a render encoder's bind state across draws and encoders, re-reading only what changed（+2268/−329，30） | encoder 的绑定状态（含最多 7 个管线状态各一份）跨 encoder 保留 → `open/close` 与每 draw 的 set* 只做真正变了的。我们 10.10 试过插件侧去重（`sk0` 没命中），这是 runtime 内部做的等价事 |
| `ca8340b` | 10-19 | Group ready batches into one kernel submission on Mesa's drivers（+3614/−346，41） | 我们每帧 **2.1 次 commit**、其中一次就是扫描输出拷贝，`B sub 697 µs`。这条把同一 queue 的多个 ready batch 合成**一次** `vkQueueSubmit`（最多 4 个），只让最后一个 signal → Mesa 合并成一次内核提交 |
| `d485925` | 10-19 | Keep the commit path off the GPU's wake and the scheduler's lock（+1462/−343，33） | commit 路径不再等 GPU 上电/scheduler 锁；上传内存改 host-cached coherent；时间戳改成完成时读；空闲 primary 提前 begin（`primaries_begun_ahead`） |
| `58cea0e` | 10-19 | Time the scheduler's take-up apart from the driver's submit（+203/−28） | 把我们 `B` 行的 `sub` 段再拆成"scheduler 收下"与"driver 提交"——10.11 里 `sub 697 µs` 现在分不清是谁 |
| `0b8edc8` | 10-19 | Keep a queue's head and its draws from waiting on pipeline makings（+1853/−264，35） | 我们的管线创建冷缓存 0.86 s、最长 **448 ms**（10.1）。这条让队头优先、等待者让出预算、**draw 先用"残差形态"管线画**，不再整帧等编译 |
| `c9b0d74` | 10-19 | Record Vulkan commands off the encoding thread through deferred command buffers（+4244/−108，34） | 编码线程只记日志，由设备自己的 recorder 线程回放成 Vulkan 调用。会改变我们 `tu_knl_darwin.cc` 看到的调用线程（见 1.4） |
| `8e4f847` | 10-19 | Wake the scheduler and completion waiters only where a thread sleeps（+1892/−181，23） | 提交/完成路径的跨线程唤醒减少；每个 waiter 一个条件变量 |
| `aa2be70` | 10-19 | Let a new texture register while a batch is replayed or submitted（+378/−35） | 参考实现里这条修掉了 guest 任务线程 **1311 ms / 110 次**的阻塞（约 17 ms 一帧）。我们的 `J completed wait` 每帧一次，值得对照 |
| `6898b52` | 10-19 | Hold freed memory … tear the device down on its own thread（+702/−81） | 释放内存等自己的提交、销毁在自有线程完成。可能与我们 §5"渲染服务器偶发停住"有关（只是候选，无证据） |
| `e35bf78` | 10-19 | **Shadow every padded texture over a buffer and every texture over host pages**（+2096/−5388，55） | **删除了 `PaddedReadsPolicy`**（`linear_plan.rs` 的 `holding()` 不再收 policy，`metal_texture.rs`、`texture/shared_alias.rs` 整文件删除）。我们的 `0008` 就是改这个 policy → 补丁失效，且默认行为变了 |
| `f514cb9` | 10-19 | Cut shadowed textures' copies to what their commands touch … **count copies by cause**（+5859/−815，52） | 影子拷贝按命令触及的矩形裁剪 + `WorkCounts` 里按原因计拷贝数。与我们 `0012`（拷贝普查）重叠，但那是通用计数器，不是扫描输出专项 |
| `3bb4722` | 10-19 | Release idle pointer imports … and **cut host-import work**（+3042/−576，44） | host import（我们 IOSurface/线性纹理那条）的导入开销与内核查询。`host_imports_*` 计数器 |
| `a97f7c2` | 10-19 | Keep Metal's contract for host imports and shadows（+1565/−139，30） | host import 语义收紧（deallocator 时机、不回抄 CPU 字节） |
| `bb4f53d` | 10-19 | Read a surface's own pixels through a snapshot at any area, where its image takes feedback loop usage（+625/−32） | "读自己正在画的 surface"（feedback loop）的处理。我们用 `pipa-metal-withhold=apple-families` 绕开了，但如果以后要放开，这是相关路径 |
| `1ef457d` | 10-19 | **Refuse an unordered framebuffer fetch**; delete `FramebufferFetchPolicy`（+2938/−787，64） | 行为变更：设备没有"有序"实现时，**直接拒绝** framebuffer fetch 的管线（不再退化成无序输入附件）。我们维持 `pipa-metal-withhold=apple-families`，但要预期同步后 apple 家族管线的失败从"画错"变成"创建失败" |
| `affd8a5` | 10-19 | Split an interlocked pass at the fetch on every GPU（+106/−224） | fetch 交错的 pass 一律在 fetch 处拆开（删掉"整 pass 更好"的特例）——窗口服务器的 pass 上实测更快 |
| `9d2a757` | 10-19 | Record more passes inline: memoryless DontCare colors, splits across the interlock, wide clears（+1849/−183，22） | pass 记录本身的开销（我们的 `C pass 311 µs`） |
| `e357ba7` | 10-19 | Record nothing for a render pass that changes no texel（+265/−9） | 空 pass 不记录。CA 的空 pass 不少 |
| `6f3730a` | 10-19 | Keep a queue's memoryless color backings across its passes and command buffers（+369/−10） | memoryless 颜色后端的复用（我们的 MSAA/memoryless 少，但 heap 分配相关） |
| `33e89bd` | 10-19 | Fit mesh, compute and tile pipelines to ANV's compiler word bound（+1386/−485，21） | 管线"适配变体"：管线创建数量/时间（我们的 97 次创建、最长 448 ms） |
| `b660f64` | 10-19 | Copy equal rows between buffers as one dispatch on ANV and the RTX 5080 Laptop（+2200/−75，30） | **注意：只对 buffer→buffer 有效**，与我们 image→buffer 的 2880 行拷贝**无关**（`plan_copy` / `rows_copy` 的结构逐行仍在原位） |
| `e320a1f` | 10-19 | … **retire vk12-bare from the corpus**（+6067/−6606，650 文件） | **删除了 `crates/metal2vulkan-validation/profiles/vk12-bare.json`**。`scripts/data/gpu-metal-vulkan-inputs.json` 记着这个文件的 sha256，`scripts/probe-metal-vulkan-host.py:101` 的默认 `--profile` 就是它 → 同步后主机探针脚本会直接报错（设备构建不受影响，我们 bridge 用 `DeviceOptions::default()` + `MetalLevel::NEWEST`，不读 profile 文件） |
| `62cad6b` | 10-19 | Regenerate the tables and set the versions at the landed tree（+4782/−4660） | `golden_hashes.tsv`、`authored-compile.tsv`、`runtime-sources.sha256` 全部重算 |

### 1.3 我们 13 个补丁的处境（逐条）

| 补丁 | 上游对应文件变化 | 判断 |
|---|---|---|
| `0001` optional-constant-output-members（`air/src/canon/sccp.rs`） | canon 目录被 `e320a1f` 等大改 | 需重做，体积小 |
| `0002` resource-wait-storage-dependencies（`used_resources.rs`） | +9/−4 | 基本可原样 |
| `0003` cache-device-entry-point-priming（`device/mod.rs`、`device/prime.rs`、`shader_cache/mod.rs`） | `prime.rs` +445/−50，`device/mod.rs` +472/−103 | 需重写。**上游 master 仍没有 priming 缓存**（`prime::prime` 每次新建 `vk::PipelineCache`），我们这条仍然有价值 |
| `0004` fold-constant-global-field-loads（canon） | canon 大改 | 需重做 |
| `0005` keep-module-decodes-and-profile-fingerprints（`device/profile.rs`、`pipeline/compile.rs`） | +268/−142、+315/−30 | 需重写（profile 字段变了，注释里说"每个 profile 指纹都变"） |
| `0006` skip-draws-without-a-pipeline（`render.rs`） | `render.rs` +3006/−916 | 需重写；也可能被 `0b8edc8`（draw 不等管线）部分取代 |
| `0007` accept-two-plane-rgb-surfaces（`ycbcr.rs`） | −72/+65，且 `b7da000` 把 YCbCr 过滤默认改成设备采样器 | 需重新判断是否还需要 |
| `0008` **alias-padded-rows-of-written-textures**（`linear_plan.rs`） | `PaddedReadsPolicy` **整体删除** | **失效**。要么按新结构重造（在"全影子"下给 writer 一个 alias 例外），要么接受影子拷贝并实测代价 |
| `0009` reuse-owned-descriptor-pools（`owned_kernel.rs`） | +316/−36；`ed3ca3a` 又加了"设备支持 push 命令时推行 set 0" | 需重做；收益可能被 `ed3ca3a` 吃掉一部分 |
| `0010` order-only-fetching-pipelines（`pipeline/graphics.rs`） | +216/−97 | 需重做；可能被 `0b8edc8` 取代 |
| `0011` one-copy-for-a-padded-frame（`blit_texture.rs`） | +138/−64，但 `plan_copy`/`rows_copy` 结构未变 | 可移植（中等改动）；**仍然需要**，上游没有等价物 |
| `0012` census-of-the-scanout-copy（`texture.rs`） | `texture.rs` −1002（影子重构） | 需重写；`f514cb9` 的 `WorkCounts` 只覆盖通用影子拷贝，不含扫描输出 |
| `0013` phase-timings（`encoder.rs`、`command_buffer/mod.rs`） | +751/−98、+1031/−217，且 `c9b0d74` 把记录搬到 recorder 线程 | 需重写。**建议同步后再重新设计计时点**（`D/C/B` 的分段在新架构里位置变了） |

### 1.4 同步会碰到的三个"非预期"变化

1. **记录线程变了**：`c9b0d74` 之后，Vulkan 命令在设备自己的 recorder 线程上回放。我们的
   `tu_knl_darwin.cc` 里"提交发生在编码线程"的假设（以及任何按线程计的计时）要重新核对。
2. **`PaddedReadsPolicy::Alias` 没了**：我们当初加 `0008` 的理由写在补丁注释里——"1200×2000 的
   surface 之间相距 4864 字节，影子拷贝一个 pass 要 5 ms GPU"。上游改成"全影子 + 按需裁剪"，
   **必须重新测这个 5 ms 是否还在**，否则同步会换来渲染侧省 1–2 ms、扫描输出/合成侧赔回 5 ms。
3. **framebuffer fetch 从"画错"变"创建失败"**：`1ef457d` 之后，需要无序 fetch 的管线直接被拒。
   我们靠 `pipa-metal-withhold=apple-families` 让 CA 走固定功能混合（10.1 的根因 2），这条要保留。

---

## 2. Mesa / Turnip（`src/freedreno`）

### 2.1 仓库状态

| 项 | 值 |
|---|---|
| 路径 | `out/pipa/tree/.cache/mesa-work`（chromium 镜像） |
| 当前 pin | `85fda3b36405bcaea8bffc64aa4f2651dc90770d`（2026-06-30），记录于 `scripts/data/adreno-compiler-inputs.json` |
| `origin/upstream/main` | `02f13375231`（2026-10-24） |
| 落后 | 总计 7155；`src/freedreno` **542**；`src/freedreno/vulkan` **254**；`src/freedreno/ir3` **110** |
| 本地未提交 | `tu_util.cc`/`tu_cmd_buffer.cc`/`tu_clear_blit.cc`/`tu_image.cc` 的诊断补丁（10.3 所述）+ `tu_knl_darwin.cc` 等 |

### 2.2 A650 / DPU 无关、但**与我们硬件直接相关**的两条一行级修复

| commit | 内容 | 我们 pin 的值 | 影响 |
|---|---|---|---|
| `761d5c8a54`（10-21） | `tu_cmd_buffer.cc:2239`：`GRAS_MODE_CNTL(CHIP, CHIP >= A7XX ? 0x2 : 0)` → `GRAS_MODE_CNTL(CHIP, 0x2)` | **0**（A650 走 `: 0` 分支） | 上游 bug 报告写得很具体："至少在 Adreno 650 上导致 misrendering / hang"，最小复现是**一个实例化 draw、大量被剔除的实例、片元着色器写 `SV_Coverage`/sample mask**。我们的圆角症状正是"**覆盖度**按 4×4 量化"。寄存器语义未在 XML 里给出（`reg32 offset="0x8110" ... low="0" high="1" variants="A6XX-A7XX"`，没有字段定义），所以无法从文档推断，但**这是一行改动，上机一轮就能证伪** |
| `0e8f40efb01`（10-22） | `freedreno_devices.py` A650 的 `A6XX_TPL1_DBG_ECO_CNTL`：`0x00108000` → `0x01008000` | **0x00108000（错的）** | 提交说明直接写 "Wrong value broke cubic filtering"。这是 A650 的 chicken bit（bit 24），影响三次卷积采样。**零风险一行** |

> 注意区分：`8aa6218125a`（恢复 a630_sqe.fw 的 predicated binning workaround）**不适用于我们**——
> 它只加在 `a6xx_gen1/gen2` 属性上，而 A650 是 `a6xx_gen3`（`freedreno_devices.py` 的 `GPUId(650)`
> 用 `[a6xx_base, a6xx_gen3]`）。`110600ac469`（`CP_DRAW_AUTO` 陈旧 stride）走的是
> `draw_auto_stale_stride_quirk`，同理 A650 没有。**不要为了"我们换了 linux-firmware 的 a650_sqe.fw"
> 去抄这两条。**

### 2.3 GMEM 规划的两个真 bug——**两个都在我们的 pin 上**

这两条是本轮最有可能解释圆角的发现，因为圆角的已知事实是"**只在 GMEM 全屏 1800×2880 pass 里
把覆盖度按 4×4 量化**"（10.2/10.4）。而那个 pass 需要 1800×2880×8 B = **41.5 MB** 的 GMEM，
A650 只有 ~1 MB——**每一次都在走"布局不可行 / 需要 tile divisor"的分支**。

**`d8169d64a40`（07-02）tu: Fix uninitialized gmem_offset when a GMEM layout is impossible**

```
if (nblocks > gmem_blocks) {
   pass->gmem_pixels[layout] = 0;
   continue;              // ← 跳出内层循环，但 layout 仍被当作有效
}
...
pass->gmem_pixels[layout] = pixels;
for (i = 0; i < pass->attachment_count; i++)   // ← 继续给每个附件算 gmem_offset
```
修法是在失败时置 `layout_impossible` 并 `break`，让这一层 layout 完全不产生 offset。
**我们 pin 的 `tu_pass.cc` 逐字就是这个坏版本**（`grep layout_impossible` 0 命中）。
后果：附件拿到的是**未初始化的 `gmem_offset`**，而 `tu_emit_input_attachments()` 在 GMEM 分支里
正是把 `gmem_offset + gmem_base` 当纹理地址交给纹理单元去读 GMEM（`tu_cmd_buffer.cc`，
`tu_desc_set_addr(dst, gmem_offset + gmem_base)`）——读到的位置取决于栈/内存残留。
这与我们 10.4 定位到的"输入附件路径"**同一处**。

**`5000d6644db`（07-03）tu/util: Fix tile division algorithm**（`tu_util.cc`，11+/8−）

原实现在"需要的 divisor 跳跃"时只用 `divisor - 1` 递归一层，把中间层**标记为已初始化却从未计算**，
tiling 配置里留下未初始化数据：
> "it could mark every intermediate level as initialized without ever actually computing them,
> leaving their tiling configs full of uninitialized data if one of those levels was ever queried
> directly afterwards."
修法是从 `initd_divisor + 1` 到 `divisor` 逐层算。**我们的 pin 有这个问题**，而我们的探针恰好
记录到 `div`（tile divisor）——如果某一帧的 divisor 跳级，用到的就是未初始化的 tile 尺寸。

**这两条都只动 `tu_pass.cc` / `tu_util.cc`**，不碰我们 10.3 的诊断补丁所在的
`tu_cmd_buffer.cc`/`tu_clear_blit.cc`/`tu_image.cc`。唯一的冲突是 `tu_util.cc` 里我们加了
`tu_pipa_note*` / `tu_pipa_small_threshold()`——补丁很小，手工合并即可。

**建议**：这两条先摘，然后**把 `pipa_gmemin`（10.4 里让输入附件 pass 回 GMEM 的开关）打开跑一轮**。
如果圆角消失，我们就同时拿到两件事：圆角的根因，以及"输入附件 pass 不必强制 sysmem"的性能。

### 2.4 GMEM 输入附件的相干性一串（4 条，同一 MR）

| commit | 内容 |
|---|---|
| `b280f8885ff` | 不再硬编码 `feedback_invalidate` 的 flush，改用 cache-flushing 框架（`tu_flush_for_access(BLIT_WRITE_GMEM → UCHE_READ_GMEM)` + `tu_flush_for_stage(BR, BR)`）。原来写的是 `CACHE_INVALIDATE | BLIT_CACHE_CLEAN | WAIT_FOR_IDLE` |
| `10b1ef466fa` | 实现 `TU_CMD_FLAGS_SUBPASS_SLICE_FENCE`（更弱的 WFI）+ `TU_CMD_FLAG_CACHE_INVALIDATE_GMEM`（更弱的 invalidate） |
| `99200197cc3` | GMEM invalidation 改用 `SUBPASS(_SLICE)_FENCE`，**并且专门处理"只读输入附件其实不在 GMEM"以及"classic renderpass 里 resolve 后再作为输入附件"两种情形** |
| `cc1cf17c2e0` | by-region 依赖也用 SUBPASS_FENCE |

为什么相关：我们 10.4 的证据是"全屏合成 pass 是 `sp1 in2 c2 cpp4`，两个输入附件走
framebuffer fetch，只有 GMEM 分支会把描述符改成 `TILE6_2` + `tex_line_offset = tile0.width*cpp`"。
上面这一串改的正是**这条读回路径的可见性**（GMEM 写入 → UCHE 读）。语义上是"更快"，但
`99200197cc3` 的说明里明确讲了两种容易搞错的情形（只读输入附件、resolve 后读），而我们的
Turnip 在 pin 上没有这些区分。

判断：**优先级低于 2.3**（2.3 是确定的未初始化 bug，这一串是"重写正确性边界"），但它和
`a995eaeda43`（`tu: Make CmdClearAttachments record TU_ACCESS_BLIT_WRITE_GMEM access`，2 行）
一起，构成"GMEM 清了之后纹理单元读到旧数据"这条完整假设。**四条一起摘，代价也不大**（总共
约 120 行，只在 `tu_cmd_buffer.*`、`tu_pass.*`、`tu_clear_blit.cc`、`tu_shader.*`）。

### 2.5 其它值得看但不急的

| commit | 内容 | 价值 |
|---|---|---|
| `df96a4daa7a` | 把 autotune 强制 sysmem/gmem 的**原因**打进 `TU_DEBUG=perf` 日志 | 我们的 pass 现在被 autotune 和我们的 `pipa_small`/输入附件规则双重决定，这条能告诉我们**谁**决定的 |
| `f5f841333e6` | `TU_DEBUG=gmem_warmup`：预分配大 VSC | GMEM 下的命令缓冲预热 |
| `79e16e72329` | autotune 追 binning pass 的耗时 | 分块模式的性能判断更准 |
| `e999786ac06` / `99fed09b0b6` | LRZ fast-clear / `tu6_clear_lrz()` 缺 WFI | 我们 LRZ 已关（`nolrz` 只用于诊断），不急 |
| `b0960060487` | `a6xx: Use a2d for z24s8 blits` | 我们无深度附件，跳过 |
| `5d9cde042b0` | 实现 `VK_QCOM_rotated_copy_commands` | 未来旋转显示可用 |
| `9ac80d5c43c` / `5c380af9737` | ir3 磁盘着色器缓存的**序列化统一**、`ir3_disk_cache.c` → **`.cpp`** | **会让我们的 `patches/mesa/ir3-darwin-build-id.patch` 直接失效**（文件名都变了）。另外缓存格式变更 → 升级后第一次仍要冷编译（一次性） |
| `f098090d620` | `VK_PIPELINE_CREATION_FEEDBACK_APPLICATION_PIPELINE_CACHE_HIT_BIT` 初始化成 true | **我们的 `turnip-graphics-cache-feedback.patch` 一半被上游吃掉**；剩下"还要 `cache_hit &&`"那半仍需要 |

### 2.6 **上游没有解**的两件事（确定）

1. **扫描输出拷贝的 2D 快路径**：`tu_clear_blit.cc` 里
   `if ((dst_va & 63) || (pitch & 63))` / `(src_va & 63) || (pitch & 63)` 的条件
   **pin 与 master 逐字相同**（master 行号 2426 / 2980 / 3217，pin 2436 / 2843 / 3067）。
   我们的扫描输出 pitch 是 **7200**（`7200 % 64 = 32`），仍会走"每行一条"的 2880 region。
2. **线性 image 的 pitch 对齐**：`src/freedreno/fdl/fd6_layout.c` 的
   `fdl_set_pitchalign(layout, layout->pitchalign + 6)` 未变（cpp=4 → 256 字节），
   我们 `align(7200, 256) = 7424` 的结论不变。整个文件只有两条与我们无关的 fdl 提交
   （`41fa61e9858` NV12 多平面、`18399c04126` UBWC 块尺寸）。

**结论**：10.9 的三条路（①改 surface stride ②compute 重排 ③显式 pitch 走 2D 快路径）里，
③ 仍然**必须**用 `VK_EXT_image_drm_format_modifier` + 显式 `row_pitch` 绕开 Turnip 自己的
256 对齐，不能等上游。

### 2.7 补丁冲突评估（同步时）

| 我们的补丁 | 冲突 |
|---|---|
| `ir3-darwin-build-id.patch` | **必须重写**：`ir3_disk_cache.c` → `.cpp` + 序列化重构 |
| `turnip-graphics-cache-feedback.patch` | **削一半**（上游已改初始化），保留 `cache_hit &&` 条件 |
| `turnip-empty-scissor.patch` | **仍需**（master 的 `tu6_scissor_size` 还是 `1 + count*2`），但要重新落盘 |
| `turnip-darwin-kmd.patch` / `tu_knl_darwin.cc` | KMD 接口有大改（`tu_knl.cc`、`tu_knl.h` 都变了），是**整个同步里最重的一块** |
| `turnip-builtin-shader-pack.patch` | 中等 |
| `offline-adreno-compiler-probe.patch`、`freedreno-tools-nir.patch`、`ir3-portable-asm-roundtrip.patch` | 轻 |
| `turnip-host-import-images.patch` | `tu_image.cc` +138/−53，中等 |
| 本地诊断（`tu_pipa_note*`、`pipa_small`、`pipa_linear`） | 未提交，**同步前必须先落成补丁或丢弃**；`tu_util.cc` 与 `5000d6644db` 直接重叠 |

---

## 3. Linux mainline：msm/DRM 与 qcom 设备树

### 3.1 `sm8250-xiaomi-pipa.dts` 存在，且与我们的硬件事实逐条吻合

上游文件：`arch/arm64/boot/dts/qcom/sm8250-xiaomi-pipa.dts`（541 行，`model = "Xiaomi Pad 6"`，
`compatible = "xiaomi,pipa", "qcom,sm8250"`，`qcom,board-id = <0x34 0>`）。

对照 10.12 的 P0 读数：

| 项 | 上游 DTS | 我们的 P0 读数 | 一致 |
|---|---|---|---|
| 帧缓冲 | `/chosen/framebuffer@9c000000`，`simple-framebuffer`，1800×2880，`stride = 1800*4 = 7200`，`a8r8g8b8` | `0x9c000000` / 7200 / 1800×2880 / 32bpp | ✅ |
| carveout | `cont_splash_mem: cont-splash@9c000000`，`0x2300000` | 21 MB 画面只占 0x13C4A00，余量约 10 MB | ✅（仍装不下第二块整屏） |
| MDSS | `display-subsystem@ae00000`，`interrupts = <GIC_SPI 83 ...>` | — | ✅ |
| MDP | `display-controller@ae01000`，`0x8f000` + `vbif@aeb0000` `0x3000` | — | ✅ |
| **display SMMU** | **`iommus = <&apps_smmu 0x820 0x402>;`** | 未读到 | **新信息，见 3.3** |
| PCIe | `&pcie0 { status = "okay"; }`（**没有** wifi 子节点） | — | WiFi 在 PCIe0 |
| USB | `&usb_1_dwc3 { dr_mode = "peripheral"; }` | — | 与我们 `fastboot`/读日志一致 |
| UFS | `&ufs_mem_hc` / `&ufs_mem_phy`，`vcc=vreg_l17a_3p0`、`vccq=vreg_l6a_1p2`、`vccq2=vreg_s4a_1p8` | 我们的 `NativeUfs` 已在用 | 可作电源对照 |
| 内存 | **没有** `/memory` 节点（`sm8250.dtsi` 里 `memory@80000000` 大小写 0，"expect the bootloader to fill in"） | 我们由 `board.toml` 的 `dram_size = 0x980000000` 给 | 一致：上游也不提供 bank 信息 |
| ramoops | `ramoops@b0000000`，`0x400000`，`record-size 0x1000`、`console-size 0x200000`、`ecc-size 16` | 我们的 `BootRamoops` 用 pstore | 可对照 record/console 大小 |

**能直接拿来的**：帧缓冲/carveout/寄存器块基址（已一致）；UFS 三路电源；USB peripheral 模式；
ramoops 参数。**拿不到**：内存 bank 划分（上游没有）、DPU 的私有 `qcom,sde-*` 属性（上游用
catalog 而不是 DT 属性描述）。

### 3.2 DPU 6.0 的寄存器与 flush 语义（对零拷贝 P1 有用）

- 块基址与我们的假设完全一致：VIG0 `0x4000`、VIG1 `0x6000`、LM0 `0x44000`、CTL0 `0x1000`
  （`dpu_6_0_sm8250.h`）；`CTL_FLUSH` = `0x018`（与 zerocopy 文档 §2.1 一致）。
- **但 `trigger_flush` 不是"只写 CTL_FLUSH"**：`dpu_hw_ctl.c:818` 判断
  `mdss_ver->core_major_ver >= 5` → SM8250（6.0）用 `dpu_hw_ctl_trigger_flush_v1`，它先按 pending
  的索引写**子块 flush 寄存器**（`CTL_MERGE_3D_FLUSH 0x100`、`CTL_INTF_FLUSH 0x110`、
  `CTL_WB_FLUSH 0x108`、`CTL_DSPP_n_FLUSH 0x13C+n*4`、`CTL_PERIPH_FLUSH 0x128`、
  `CTL_DSC_FLUSH 0x104`、`CTL_CDM_FLUSH 0x114`），**最后**才写 `CTL_FLUSH`。
  → 只改 SSPP 的 `SRC0_ADDR`/`SRC_YSTRIDE0` 时写 `CTL_FLUSH` 就够；**一旦碰到 INTF 相关配置，
  还要写 `CTL_INTF_FLUSH`**，否则那一半不会生效。P1/P2 如果只翻地址，现有方案成立。
- SM8250 的 CTL 在 catalog 里**没有任何 feature 位** → 没有 `DPU_CTL_ACTIVE_CFG`、
  没有 `CTL_FETCH_PIPE_ACTIVE` 语义 → 视频模式下仍不需要 `CTL_START`/`CTL_PREPARE`，
  与 zerocopy 文档 §2.2 一致。
- `a5b5cc90993`（08-28，`dpu_hw_ctl.c` +1）"clear pending peripheral flush state"：只影响
  **peripheral flush**（DPU v12+ 的机制），SM8250 用不到。列出来是为了避免以后误以为我们漏了什么。

**近期 dpu1 提交的实际情况**：2026 年的 dpu1 改动几乎全是 DPU v12/v13（SM8750 "Kaanapali"、
Eliza、Milos）与 VBIF/DSC/CWB 重构，**没有一条针对 SM8250 或 DPU 6.0 的修复**。
唯一与我们写的寄存器有关的仍是上面的 flush 子块语义。

### 3.3 一个新线索：display 的 SMMU 不是"一条 stream"

10.13 记录 P1c（把 display stream 切到 translation）把设备挂死，判断是"未用平面 `SRC0_ADDR=0`
仍然被 DPU 读到"。上游 DT 给出的是：

```
iommus = <&apps_smmu 0x820 0x402>;
```

按 qcom apps-smmu 的 `<sid mask>` 语义，mask `0x402` 表示**第 0x400 和第 0x002 位不比较**
（即该组覆盖 `0x820`、`0x822`、`0x420`、`0x422` 一类的多个 stream，通常对应 unsecure / secure /
第二个 MDP port）。也就是说：**display 侧不止一条 SID**。上次只把一个 stream 切到 translation、
其余仍按旁路或指向固件的表，完全可能产生"一半的取帧走新表、一半走老路"的 AXI 事务，正好符合
"总线层面挂住"的现象。

这与 10.13 的三条前置条件并列，建议在 P0 的 SMMU 探针里**把 `0x820/0x822/0x420/0x422` 全部
扫一遍 SMR/S2CR/CBAR**，再决定接管哪几条。**注意**：这只是上游 DT 的一条线索，不是实证；
mask 的精确语义建议以 `Documentation/devicetree/bindings/iommu/arm,smmu.yaml` +
`arm-smmu.c` 的 `arm_smmu_smr_match` 为准。

### 3.4 我们的 ADT 能不能"照抄" DTS

不能直接抄。我们的树是 `scripts/make-pipa-adt.py` **手工拼的 Apple 格式 DT**（只有
`/cpus`、`/psci`、`/chosen/nvram-*`、`/product` 等），`board.toml` 里 `[[bootimg.dtb_node]]`
**一条都没在用**（只有一行注释提到）。要加 mdss 节点就是改这个生成器（zerocopy 文档 §1.3 的
路线 A），成本仍在"半天到一天"的量级，但上游 DTS 给了我们可以直接抄的 `reg`/`interrupts` 值
（`0xae00000/0x1000`、`0xae01000/0x8f000`、`0xaeb0000/0x3000`、GIC SPI 83）。

---

## 4. ath11k / QCA6390（供 WiFi 调研参考）

### 4.1 硬件已确认

上次 Android 启动的 ramoops console（`out/pipa/device-logs/20261025-111715/dmesg`）：

```
cnss2 b0000000.qcom,cnss-qca6390: Linked as a consumer to regulator.7   (vdd-wlan-io 1.8 V)
cnss: Got regulator: vdd-wlan-aon,  min_uv 950000
cnss: Got regulator: vdd-wlan-dig,  min_uv 950000
cnss: Got regulator: vdd-wlan-rfa1, min_uv 2000000
cnss: Got regulator: vdd-wlan-rfa2, min_uv 1350000
cnss: BT GPIO: 1121        (tlmm 21)
cnss: Switch control GPIO: 1224
cnss: Assert WLAN_EN GPIO successfully   (tlmm 20)
msm_pcie_enable: PCIe: Assert the reset of endpoint of RC0
pci-msm 1c00000.qcom,pcie: PCI host bridge to bus 0000:00
cnss_pci 0000:01:00.0: Linked as a consumer to 15000000.apps-smmu
```

即：**QCA6390**，在 **PCIe RC0（`pcie@1c00000`，1 lane）**，5 路 regulator，WLAN_EN = tlmm 20、
BT_EN = tlmm 21。上游 pipa.dts 也把 `&pcie0` 打开（但没写 wifi 子节点）。

另一次 Android 启动（`out/pipa/device-logs/20261022-215732/dmesg`）逐行相同，说明这不是偶然状态。
那次日志里还有两条 reserved-memory 事实（下游 cnss2/PIL 方案才需要）：

```
OF: reserved mem: initialized node cnss_wlan_region,     compatible id shared-dma-pool
OF: reserved mem: initialized node pil_wlan_fw_region@86700000, compatible id removed-dma-pool
```

即厂商方案要给 WLAN 协处理器留一块 DMA 区（`0x86700000`）并走 PIL 加载固件。**这只影响"照抄下游
cnss2"这条老路**；下面 4.2 的 mainline 路径不用它。

### 4.2 mainline 的启动链（比我们设想的更简单）

mainline **没有**我们设想的"`qcom,qca6390-pmu` + 单独的 mhi 驱动"那套复杂分工，实际是：

1. **`qcom,qca6390-pmu`** 是一个 **regulator provider**（`Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml`）：
   它描述 QCA6390 模块内部的 PMU，往上有 8 路输入（`vddaon`、`vddpmu`、`vddrfa0p95`、`vddrfa1p3`、
   `vddrfa1p9`、`vddpcie1p3`、`vddpcie1p9`、`vddio`；binding 还定义了 `vdd`/`vddasd`/`vdddig` 等其余可选名），
   往下导出 10 个 LDO 子 regulator
   （`ldo0..ldo9`：rfa_cmn / aon / wlcx / wlmx / btcmx / rfa_0p8 / rfa_1p2 / rfa_1p7 / pcie_0p9 / pcie_1p8），
   并持有 `wlan-enable-gpios`、`bt-enable-gpios`。
2. **`drivers/power/sequencing/pwrseq-qcom-wcn.c`** 负责上电时序；PCIe 端点通过
   `drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.c` 这条**通用 PCI 电源控制**链路拿到 `power-domains`/pwrseq。
3. **ath11k 自带 MHI**（`drivers/net/wireless/ath/ath11k/mhi.c`），**不需要**外部的 `mhi` 驱动。
   PCIe 侧设备 ID 就是 `pci17cb,1101`（QCA6390）。
4. wifi 子节点上把 9 路 supply 指回 PMU 的 10 个 LDO，并给一个 `qcom,calibration-variant`。

### 4.3 同代同厂的现成配方：`sm8250-xiaomi-elish`

`sm8250-xiaomi-elish`（小米平板 5 / 5 Pro）在 mainline 里就是 **SM8250 + QCA6390**，节点布局可以直接
照搬（`arch/arm64/boot/dts/qcom/sm8250-xiaomi-elish-common.dtsi`）：

```dts
qca6390-pmu {
        compatible = "qcom,qca6390-pmu";
        pinctrl-0 = <&bt_en_state>, <&wlan_en_state>;
        vddaon-supply      = <&vreg_s6a_0p95>;
        vddpmu-supply      = <&vreg_s6a_0p95>;
        vddrfa0p95-supply  = <&vreg_s6a_0p95>;
        vddrfa1p3-supply   = <&vreg_s8c_1p35>;
        vddrfa1p9-supply   = <&vreg_s5a_1p9>;
        vddpcie1p3-supply  = <&vreg_s8c_1p35>;
        vddpcie1p9-supply  = <&vreg_s5a_1p9>;
        vddio-supply       = <&vreg_s4a_1p8>;
        wlan-enable-gpios  = <&tlmm 20 GPIO_ACTIVE_HIGH>;
        bt-enable-gpios    = <&tlmm 21 GPIO_ACTIVE_HIGH>;
        regulators { ldo0..ldo9 };
};

&pcieport0 {
        wifi@0 {
                compatible = "pci17cb,1101";
                reg = <0x10000 0x0 0x0 0x0 0x0>;
                /* 9 路 supply 指回上面的 LDO */
                qcom,calibration-variant = "Xiaomi_Pad_5Pro";
        };
};
```

**与 pipa 的差异点（需要现场确认）**：pipa 的 WLAN_EN/BT_EN 是不是同样是 tlmm 20/21（下游 dmesg
显示为 GPIO 1121/1224，编码方式与我们看到的 `pm8150_gpios` 编号体系不同，**必须核实**）；
`qcom,calibration-variant` 要写什么（pipa 的 board-2.bin 里未必有对应条目，没有时 ath11k 只能
退到通用条目，可能起不来）。

### 4.4 结论

- **上游路径确实比"自己写 cnss2"简单**：没有 QMI、没有单独 mhi、没有 PDA/固件下载协议要复刻——
  这些都在 ath11k + 固件里。
- 但我们**不是跑 Linux**：ath11k/PCI 枚举/regulator 框架全都得在 XNU 侧重写（或从 payload 里做）。
  可复用的是**时序与寄存器知识**（9 路电源、两颗 enable GPIO、PCIe RC0 的 reset/PHY/枚举），
  不是代码。所以对 WiFi 调研的价值是"**目标状态机**"，不是"拿来就能编译"。
- 一个**更省事的候选**：先在 UEFI payload 里把 PCIe RC0 枚举出来、读 QCA6390 的配置空间
  （厂商 ID/设备 ID/calibration 是否在），这一步不需要任何 XNU 驱动，就能回答"值不值得做"。

---

## 5. 值得同步 / 不值得同步清单

> 工作量按"已经有构建与上机流程、但需要调试"估；风险按"会不会把当前可用状态弄坏"估。

### 5.1 值得做（按性价比排序）

| # | 内容 | 仓库 / 范围 | 工作量 | 风险 | 解决我们哪个已知问题 |
|---|---|---|---|---|---|
| **T1** | **Turnip 两条一行修复**：`761d5c8a54`（`GRAS_MODE_CNTL` → 0x2，A650 覆盖度/挂死）、`0e8f40efb01`（A650 `TPL1_DBG_ECO_CNTL` 0x01008000，三次卷积） | mesa：2 个文件、2 行 | **0.5 小时** | **极低**（两行常量；`GRAS_MODE_CNTL` 唯一的不确定性是 0x2 在 A650 上的效果，可回退） | **圆角（9.1–10.4）**——这是唯一一条"上游 bug 报告的症状与我们一致（覆盖度/sample mask）"的提交；另一条修采样正确性 |
| **T2** | **Turnip GMEM 规划两 bug**：`d8169d64a40`（`tu_pass.cc`，未初始化 `gmem_offset`）、`5000d6644db`（`tu_util.cc`，divisor 层未初始化） | mesa：2 个文件、约 30 行 | **0.5–1 天**（含与 `tu_util.cc` 诊断补丁的手工合并） | 低（都是"初始化/循环边界"，不改控制流） | **圆角**。我们的全屏 pass 要 41.5 MB GMEM / 只有 1 MB，**必然**走"布局不可行 + tile divisor"这两条分支，而这两个 bug 就在这两条分支里 |
| **T3** | 用 T1+T2 之后打开 `pipa_gmemin`（10.4 的开关）跑 **1–2 轮**，看圆角是否回来 | 设备：`--arg pipa-turnip-debug=nocb,pipa_gmemin` | **1 轮**（构建 + 上机 + 判图） | 中（可能仍是坏的；不影响当前默认配置，因为开关默认关） | 圆角**根因**；若成立还能去掉"输入附件 pass 强制 sysmem"的性能代价 |
| **T4** | Turnip GMEM 输入附件相干性 4 条：`b280f8885ff`、`10b1ef466fa`、`99200197cc3`、`cc1cf17c2e0`（+ `a995eaeda43` 2 行） | mesa：约 120 行，`tu_cmd_buffer.*`/`tu_pass.*`/`tu_clear_blit.cc`/`tu_shader.*` | **1–2 天** | 中（改的是可见性边界；与我们的诊断补丁在同一批文件） | 圆角（读回路径），或至少把这条假设彻底排除 |
| **T5** | 把 `df96a4daa7a`（autotune 记录强制 gmem/sysmem 的**原因**到 `TU_DEBUG=perf`）摘进来 | mesa：`tu_cmd_buffer.cc`/`tu_autotune.cc` | **0.5 天** | 低 | 10.12/§9：现在"哪个 pass 走 GMEM"被 autotune + 我们的 `pipa_small` + 输入附件规则三方决定，日志里分不清 |
| **T6** | 落地上面几条后，把 `turnip-graphics-cache-feedback.patch` 削成只剩 `cache_hit &&`，`ir3-darwin-build-id.patch` 按 `.cpp` 重写 | mesa：补丁维护 | **0.5 天** | 低 | 维护性（10.3 的"未登记改动"问题） |
| **T7** | **metal2vulkan 全量同步**（`720ef1c..a2c440b`，66 条） | metal2vulkan + 13 个桥补丁 + `gpu-metal-vulkan-inputs.json` | **4–7 天**（squash 无法拆；8 个补丁重做、1 个作废、探针脚本改 profile） | **高**：①`0008` 作废 → 影子拷贝可能把 5 ms/pass 还回来；②记录改到 recorder 线程，`tu_knl_darwin.cc` 与 `D/C/B` 计时全部要重定；③frame fetch 从"画错"变"创建失败" | **10.10–10.12 的渲染侧 CPU**（目前是除扫描输出拷贝外唯一的大头：`U cpu 10 ms/帧`，其中 draw 3.75 + close 1.45 + commit 1.23）；以及**每帧 2.1 次提交→合并**（省 `B sub` 的一部分，但不省扫描输出拷贝本身） |
| **T8（反例）** | 只摘 T7 里的**遥测/日记类**（`58cea0e`） | — | — | — | **不成立**：`58cea0e` 也坐在 `0175289` 之上，单摘会冲突。见 5.2 |

### 5.2 不值得做 / 做不了

| 内容 | 原因 |
|---|---|
| **"只挑 metal2vulkan 的 `ca8340b` + `86b5d6f` 两条"** | `0175289`（+107k/−20k，1298 文件）是它们的父提交，**单摘必然大面积冲突**。要么整体（T7），要么不动 |
| **指望上游解决扫描输出拷贝（10.9/10.10 的 ~7.7 ms/帧）** | `tu_clear_blit.cc` 的 64 字节条件与 `fd6_layout.c` 的 256 字节 pitch 对齐**逐字未变**；`b660f64` 只改 buffer→buffer。继续走 ③ 显式 pitch 或 MDSS 零拷贝 |
| **Turnip 全量 rebase（`85fda3b → 02f13375`，542 条 freedreno）** | 最大的一块是 `tu_knl*`（KMD 接口重写，我们的 `tu_knl_darwin.cc` 要跟着重做），收益集中在"跟上游同步"而非我们的问题。**等 T1–T6 的结果再决定** |
| **抄 `8aa6218125a` / `110600ac469`（a630_sqe.fw 的 binning / stride workaround）** | 属性只加在 `a6xx_gen1/gen2`；A650 是 `a6xx_gen3`，**不受影响** |
| **用 `mhi` / `qcom,qca6390-pmu` 驱动"直接搬"给 XNU** | mainline 的 `qca6390-pmu` 是**regulator provider**、ath11k 自带 MHI；而且这些是 Linux 内核框架，XNU 里没有对应物。只能当"目标状态机"参考 |
| **`a5b5cc90993`（peripheral flush）、`7520803bb9a`（LM_6/7 flush 位）、`3c072d50aed`（CTL_FETCH_PIPE_ACTIVE）** | 都是 DPU v12+（SM8750）的机制，SM8250 不涉及 |
| **改 pin 去追 `origin/development`** | 比 master 多 1265 条，且 master 才是发布线 |

### 5.3 给下一步的建议顺序

1. **T1（半天，两行）**：零风险，且是唯一有"症状一致"证据的上游修复。上机一轮看圆角。
2. **T2 + T3（2–3 天）**：两条确定的未初始化 bug，正好落在我们那个 41.5 MB/1 MB 的 pass 上；
   配合 `pipa_gmemin` 一轮就能判定圆角根因。
3. **T5（半天）**：让 autotune 自己说它为什么选 sysmem/gmem——这条能解释 10.7 以来
   "为什么有的 pass 在 GMEM、有的不在"，也让 T3 的结论更可信。
4. **T4**：若 T1–T3 之后圆角仍在，再上这一串。
5. **T7（metal2vulkan）**：单独排期。**在动之前先做一件事**：把 `0008` 的 5 ms/pass 结论在
   新 pin 上重现一遍（只读：读 `e35bf78` 之后的 `linear_plan.rs` 与 `docs/FAST-PATHS.md`，
   确认"全影子"是否真的会给我们 1200×2000 的 surface 每次 pass 都拷一遍）。如果会，
   同步的净收益就要打折，可能需要先把 alias 语义重新设计成补丁。
6. **零拷贝线**：不受本次调研影响。唯一新增的输入是 **§3.3 的 SMMU 多 stream 线索**——
   下次做 P0 探针时顺手扫 `0x820/0x822/0x420/0x422`。

---

## 6. 本次实际做过的操作（可复现）

```
# metal2vulkan
git -C out/pipa/tree/.cache/gpu-reference/metal2vulkan fetch --all --tags
git -C ... log --oneline 720ef1c..origin/master        # 66 条
git -C ... log --reverse --format ... 720ef1c..origin/master   # 每条 +/-

# mesa
git -C out/pipa/tree/.cache/mesa-work fetch origin     # 拉到 upstream/main
git -C ... rev-list --count HEAD..origin/upstream/main -- src/freedreno   # 542
git -C ... log --oneline HEAD..origin/upstream/main -- src/freedreno/vulkan/tu_clear_blit.cc

# linux：只读 HTTP（没有 clone）
gh api repos/torvalds/linux/contents/arch/arm64/boot/dts/qcom/sm8250-xiaomi-pipa.dts
gh api repos/torvalds/linux/contents/arch/arm64/boot/dts/qcom/sm8250-xiaomi-elish-common.dtsi
gh api repos/torvalds/linux/commits?path=drivers/gpu/drm/msm/disp/dpu1
raw.githubusercontent.com/.../dpu1/dpu_hw_ctl.c, dpu_6_0_sm8250.h, qcom,ath11k-pci.yaml
```

没有改任何 pin、没有改代码、没有 `fastboot`、没有 `kernel-round.sh`。
