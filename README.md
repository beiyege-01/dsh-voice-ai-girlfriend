# DSH 语音 AI 女友（Voice AI Girlfriend）

> "你好呀，我是小雅。从今往后，DeepSeek Harness 不只是你的编程搭子——它开口说话了。"

> 📌 **流式数字人（DUIX 口播视频）已包含在本主线**：回复实时生成数字人口播视频（≤10s 分段流水线、TTS+画面同步播放、数字人开关、视频保留 200 条）。

> 🎥 **成品展示**（抖音）：
> - [口播视频效果 ①](https://www.douyin.com/user/self?from_tab_name=main&modal_id=7676326565919149339)
> - [口播视频效果 ②](https://www.douyin.com/user/self?from_tab_name=main&modal_id=7678063553395412259)

这是一位住在你电脑里的 AI 女友：点一下麦克风，她听你说话、跟你拌嘴、把回答一字一句念给你听；你出门了，她追到你的 QQ 上继续聊；旁边那个窗口里的姑娘也不是摆设——她闲着会发呆，说话时会开口。

她有点小脾气，但你大概也会喜欢上这些：

- ⚡ **嘴快**：你说完话，她 0.5 秒内开口 —— FunASR 中文识别只要 150ms、TTS 几乎秒开，反应比你还快
- 🎧 **耳朵挑**：-35dB 噪声门 + silero VAD 双重把关 —— 风扇、键盘、电视声统统进不来，只有你说话她才理
- 📱 **粘人**：她的回复自动推到你的 **QQ**（文本 + 语音 + 图片）—— 你不在电脑前，她也能找到你
- 👧 **会动**：右侧数字人窗口，空闲时发呆、说话时开口 —— 一个会呼吸的 AI，不是冷冰冰的对话框
- 🎬 **会演**：回复不只是声音——**流式数字人**（DUIX）把你的回复实时生成口播视频：长回复切成 ≤10 秒小段，边生成画面边合成下一段语音，一段接一段连续播放，TTS 与口型同步开口
- 🔇 **懂打断**：你插嘴她就闭嘴听你说；想让她把话说完，点一下开关就切回排队模式
- 🔊 **声音是你的**：TTS 声音克隆（OmniVoice，600+ 语言），音色由你给的参考音频决定，或直接用参数设计音色——她可以长成你喜欢的样子
- 🎛️ **随你调配**：数字人开关（开=视频+声音同步 / 关=接近即时的纯语音朗读）、QQ 推送开关、女友窗开关、插话/排队模式，全部工具行一键切换

```
┌────────────────────────────────────────────┐
│  浏览器（DSH Web GUI :3080）                 │
│  ┌──────────┐  ┌─────────────────────────┐  │
│  │ 对话面板   │  │ 女友窗（bg/task 视频）   │  │
│  │ 麦克风+⚡  │  │ 数字人视频（同步播放）    │  │
│  └──────────┘  └─────────────────────────┘  │
│   麦克风采集 ──▶ STT ──▶ 代理回复 ──▶ TTS ──▶ 播放 │
│     ▲回复文本          回复文本▼            │
└─────┼──────────────────────┬──────────────┘
      │ 插件 QQ 桥 (WS)       │ HTTP (CORS)
┌─────▼──────────────────────▼──────────────┐
│  voice_bridge (:8765)                      │
│  /api/stt  FunASR 中文 ASR                 │
│  /api/tts  OmniVoice 克隆（WSL2·FlashInfer 加速）│
│  /api/dh/* DUIX 数字人（分段流水线/播放/开关）│
│  /api/qq/* QQ 桥（收发 + 语音推送）          │
│  /api/vad  silero 打断 / media 素材         │
└────┬──────────────┬───────────────────────┘
     │ OneBot HTTP+WS │ HTTP（提交音频→轮询视频）
┌────▼──────────┐ ┌──▼──────────────────────┐
│  NapCatQQ      │ │  DUIX 数字人 (:8383)      │
│  小号在线       │ │  音频 → 口型同步视频       │
│  → 文本+语音   │ │  → 女友窗同步播放          │
└───────────────┘ └──────────────────────────┘
```

## 目录结构

```
dsh-voice-ai-girlfriend/
├── bridge/            # 语音桥接（独立可跑，Python/FastAPI）
│   ├── voice_bridge.py            # STT/TTS/数字人(DUIX)/QQ/VAD 全部端点
│   ├── bridge-config.example.json # 配置模板（复制为 bridge-config.json）
│   ├── requirements.txt
│   ├── start-bridge.cmd           # 只起桥接
│   └── start-all.cmd              # 桥接 + DSH Web 一键启动
├── models/            # 模型（gitignore，不入库）：funasr/ + silero-vad/
├── assets/            # 素材：内置 5 套默认待机动画（bg2/bg4/bg9/bg56/bg5），可直接用；也可自备
│   ├── bg-images/     # 空闲动画：内置 bg2/bg4/bg9/bg56/bg5 五套，自建子文件夹即可添加自定义待机
│   └── task-videos/   # 备用说话动画（可选；开了数字人后自动用 DUIX 视频）
├── voices/            # 音色库（自建）：每个子文件夹 = 一个 TTS 音色（见「自定义音色」）
├── dsh-plugin/        # DSH 客户端插件源码（mic/开关/女友窗/数字人/流式朗读）
│   └── README.md      # 安装到 DSH 的详细步骤
└── docs/              # 开发日志等
```

---

# 从零开始安装（小白版）

> 全程在 **Windows** 上操作。下面的命令默认在 **PowerShell** 里执行；
> 除了标注"在项目文件夹里运行"的步骤，其余在哪里运行都行。

## 一、前置准备（一次性装齐）

### 1. 检查你的电脑

| 检查项 | 要求 | 验证命令 |
|---|---|---|
| 系统 | Windows 10/11 64 位 | `winver` |
| 显卡 | NVIDIA 独立显卡（显存建议 16GB 或以上） | `nvidia-smi`（能显示显卡信息即可） |
| 磁盘 | 至少 30GB 剩余空间 | — |
| 内存 | 建议 16GB 以上 | — |

> `nvidia-smi` 不是 NVIDIA 显卡也能显示吗？不能——如果没有 NVIDIA 显卡或驱动没装好，会提示"不是内部或外部命令"或报错。**没有 NVIDIA 显卡就装不了本项目**（模型推理依赖 CUDA GPU）。

**显存占用**（运行时实测）：

| 模式 | 显存占用 |
|---|---|
| **推荐：OmniVoice TTS（WSL2 + FlashInfer）** | ~5.3GB（空闲）~ 11GB（推理峰值，CUDA graph 分桶缓存） |
| Qwen3-TTS 1.7B（fp16，备选降级） | ~3.7GB |
| FunASR Paraformer-large（fp16） | ~1GB |
| DUIX 数字人（视频生成时） | ~4-6GB |

> 全链路（TTS + 数字人同时工作）实测峰值约 **15GB**，16GB 显存基本吃满；OmniVoice 与 DUIX 建议错峰（TTS 合成完再生成视频）。16GB 显存为推荐配置，8GB 会非常紧张不推荐。

### 2. 安装 Git

用来克隆仓库。下载安装：<https://git-scm.com/download/win>，一路下一步。

验证：`git --version` 能输出版本号即可。

### 3. 安装 Python（3.10 或更高）

下载安装：<https://www.python.org/downloads/windows/>

⚠️ **安装时务必勾选 "Add Python to PATH"**，否则后面 `python` 命令会找不到。

验证：打开新终端，`python --version` 能输出版本号即可。

### 4. 更新 NVIDIA 驱动

到 <https://www.nvidia.cn/drivers/> 下载最新驱动安装。驱动太旧会导致 CUDA 相关报错。

> 本项目**不需要**单独安装 CUDA Toolkit——`pip` 装的 PyTorch 自带 CUDA 运行库，只要驱动够新就行。

### 5. 安装 Node.js 和 pnpm（运行 DSH 用）

- Node.js：下载安装 <https://nodejs.org/>（选 LTS 版本），一路下一步。
- 验证：`node --version`
- pnpm（Node.js 装完后，在终端执行）：

```powershell
npm install -g pnpm
```

- 验证：`pnpm --version`

### 6. 准备 deepseek-harness（DSH）源码

DSH 是开源项目，本项目是它的一个插件。**需要先有一份 DSH 源码树**（插件要装进它的源码里）：

```powershell
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
```

> ✅ **已适配 DSH rc.8 与 dsh 0.1.3**：插件协议（`dsh.bundle` manifest + `conversation.input.dock/left` 槽位）按 rc.8 实现，升级 rc.8 无需改动即可运行；**2026-09-05 已在 dsh v0.1.3-alpha.1 实测通过**（0.1.3 槽位/`__ModuleLoader__`/session prompt 契约均未变，零代码改动）。推荐安装（rc.8 与 0.1.3 通用）：`dsh plugin --profile web add github:beiyege-01/dsh-voice-ai-girlfriend-plugin`。余额统计走 `conversation.composer.dock` 槽位。
> `pnpm install` 会装几十秒到几分钟。装完后这个文件夹先放着，后面"安装 DSH 语音插件"步骤要用。
> 记住它的路径（比如 `C:\dev\deepseek-harness`），后面一键启动要用。

### 7. 硬盘空间预估

| 项目 | 大小 |
|---|---|
| 本项目代码 + 素材 | ~8MB |
| Python 虚拟环境 + 依赖（含 PyTorch） | ~5-8GB |
| FunASR Paraformer 模型（models/funasr/） | ~850MB |
| OmniVoice TTS 环境（WSL2 内，含模型 + torch cu130 + FlashInfer） | ~10GB（WSL2 磁盘） |
| deepseek-harness + node_modules | ~2-4GB |

## 二、安装本项目

### 1. 克隆仓库

```powershell
git clone https://github.com/beiyege-01/dsh-voice-ai-girlfriend.git
cd dsh-voice-ai-girlfriend
```

> 之后所有步骤都在这个文件夹（项目根目录）里进行。

### 2. 创建 Python 虚拟环境

```powershell
python -m venv venv-speech
```

激活它：

```powershell
venv-speech\Scripts\activate
```

激活成功后，终端行首会出现 `(venv-speech)`。

### 3. 安装依赖

```powershell
pip install -r bridge\requirements.txt
```

> 这一步会装 PyTorch、transformers、HuggingFace speech-to-speech 等，**体积大、耗时长**（几分钟到几十分钟），耐心等待。
> 网络慢装不动？见文末"常见问题"第 1 条（换清华镜像）。

## 三、准备模型（两个模型）

### 1. STT 模型：FunASR Paraformer 中文模型（放本地 `models/` 目录）

语音识别用**阿里 FunASR 中文 ASR**（Paraformer-large，专为中文设计，同音字/口音识别准确率远高于 whisper）。模型**放在仓库根的 `models/funasr/`**（约 850MB，已 gitignore 不入库）：

```powershell
pip install modelscope
# 1) 先下载到缓存（首次）
python -c "from modelscope import snapshot_download; snapshot_download('iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch')"
# 2) 把模型拷到项目的 models\funasr\ 下（文件名随意，配置里指向它）
xcopy /E /I %USERPROFILE%\.cache\modelscope\models\iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch\snapshots\master models\funasr\speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
```

**打断用 VAD 模型**（`models/silero-vad/silero_vad_v4.jit`，2MB）同样不入库，从 [silero-vad v4.0 tag](https://github.com/snakers4/silero-vad/tree/v4.0) 的 `files/silero_vad.jit` 获取（或使用本项目 release 附带的文件）。

> 想换回 whisper（如 `openai/whisper-large-v3` 或 `-turbo`）？把 `bridge-config.json` 里 `stt.backend` 改为 `"whisper"` 并把 `model_name` 换成 whisper 模型 id 即可（桥接双后端都支持）。

### 2. TTS 引擎：OmniVoice（推荐，跑在 WSL2，FlashInfer 加速）

项目默认 TTS 已从 Qwen3-TTS 换成 **OmniVoice**（小米 k2-fsa/OmniVoice，600+ 语言、克隆/设计双模式），并跑在 **WSL2** 里配合 **FlashInfer**（CUDA Graph 加速，Blackwell 专属）使用。实测收益：

| 指标 | 旧（Qwen3-TTS） | 新（OmniVoice + FlashInfer） |
|---|---|---|
| 克隆首轮出音 | 18.3s | **1.6s** |
| 热态出音 | ~2.2s | **~1.3s** |
| 空闲显存 | 7GB（常驻） | **5.3GB** |
| 音色数量 | 参考音频克隆 | 克隆 + 参数设计（性别/年龄/音调/方言） |

**部署前提**：Windows 11 + WSL2（Ubuntu 22.04）。WSL2 内用 uv 建环境：torch 2.13.0+cu130 + flashinfer 0.6.18 + jit-cache cu130 + nvcc cu13（Blackwell sm_120 必须 cu13 系列，cu129 会报「requires GPUs with sm75」）。**注意**：WSL2 系统里若有旧 nvcc（如 12.8）会干扰 flashinfer 的 CUDA 版本检测，启动服务前必须设置 `CUDA_HOME` 指向 cu13 的 nvcc 目录。

**启动 OmniVoice 服务**（WSL2 内）：

```bash
# 一键（含 CUDA_HOME 配置，模型复用 Windows 挂载 /mnt/e 的路径）
wsl -d Ubuntu-22.04 -- bash ~/omnivoice-wsl/start_server.sh
```

服务监听 `0.0.0.0:9877`，Windows 侧通过 **WSL2 的 IP**（`wsl hostname -I` 查询，重启后可能变化）访问，bridge-config 的 `omnivoice.base` 指向 `http://<WSL2-IP>:9877`。Windows 的 localhost 转发在本机 WSL2 网络模式下失效，必须用 IP。

**备选：Qwen3-TTS（无需 WSL2，降级方案）**：若不使用 WSL2，可在 `bridge-config.json` 里把 `tts` 相关配置切回 Qwen3-TTS-12Hz-1.7B-**Base**（仅 Base 支持参考音频克隆，VoiceDesign 不支持）。模型用 ModelScope 下载：

```powershell
pip install modelscope
modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-Base --local_dir ./Qwen3-TTS-12Hz-1.7B-Base
```

## 四、准备参考音频（决定音色）

AI 女友的声音是**克隆自你的参考音频**的。请自备一段：

- **时长**：10 秒左右（5~20 秒都行）
- **内容**：干净人声、无背景音乐、无杂音，**用你自己的话**自然地朗读一段内容即可（说什么都可以）
- **文件**：命名为 `ref_audio.wav`，放到**项目根目录**（和 `bridge/` 文件夹同级）。

> 配置里的 `tts.ref_text` 必须填**你这段录音实际朗读的那句话**（逐字一致、含标点）——音色克隆质量依赖文本与录音的匹配。

### 多音色切换（可选，推荐）

内置了**多个音色预设**，工具行「🎙️ 音色」按钮可随时切换（说话的声音立即变化，无需重启）。**添加你自己的音色**：

1. 在 `voices/` 下新建一个子文件夹，名字即音色名（如 `voices/我的声音/`）
2. 里面放两个文件：
   - `ref_audio.wav` —— 参考音频（10 秒左右干净人声）
   - `ref_text.txt` —— 上面这段音频实际朗读的文本（UTF-8 编码）
3. 重启桥接，新音色自动出现在「🎙️ 音色」切换列表里

> 项目自带的参考音色不随仓库分发，请按此方式添加自己的（参考音频和文本都属于你的个人素材，不会入库）。

## 五、填写配置（复制模板 + 改 1 个必改项）

在**项目根目录**执行：

```powershell
copy bridge\bridge-config.example.json bridge\bridge-config.json
```

用记事本打开 `bridge\bridge-config.json`：

### 必须改（1 处）

| 位置 | 改成什么 |
|---|---|
| `omnivoice.base` | OmniVoice 服务地址（WSL2 IP，如 `http://192.168.1.244:9877`） |
| `persona.default_voice` | 默认音色名（见 voices/ 下的文件夹名） |
| `tts.ref_text`（备选 Qwen3 时） | 参考音频实际朗读的文本（见第四部分） |

> OmniVoice 模式下 `tts.*` 的模型路径不再使用（引擎跑在 WSL2 服务里）；只有切回备选 Qwen3-TTS 时才需要填 `tts.model_name`：

```
"C:/你的QwenTTS模型目录/Qwen3-TTS-12Hz-1.7B-Base"     ← 正斜杠
"C:\\你的QwenTTS模型目录\\Qwen3-TTS-12Hz-1.7B-Base"    ← 双反斜杠
```

### 建议改（1 处）

| 位置 | 改成什么 |
|---|---|
| `tts.ref_text` | 你参考音频实际朗读的文本（见第四部分） |

### 不用动（已自动处理）

| 配置 | 说明 |
|---|---|
| `media.bg_images_dir` / `task_videos_dir` | 相对路径，基于项目根自动解析 |
| `tts.ref_audio` | 默认读取项目根的 `ref_audio.wav` |
| `stt.*` | FunASR 中文识别配置（backend=funasr，模型在 models/funasr/），默认即可 |

## 六、先验证桥接（强烈建议）

启动桥接：

```powershell
bridge\start-bridge.cmd
```

会弹出一个最小化的终端窗口，等 1-2 秒后，浏览器打开：

```
http://127.0.0.1:8765/api/health
```

看到 `{"status":"ok", ...}` 就说明桥接起来了（此时模型还没加载，等首次调用才会加载）。

更完整的测试（**另开一个终端**，在项目根目录、venv 激活状态下）：

```powershell
venv-speech\Scripts\python.exe bridge\smoke_tts.py --text "你好，我是小雅。"
```

结束后项目根目录会生成 `tts_out.wav`，播放它——**能听到克隆音色的声音，说明 TTS 链路通了**。

## 七、安装 DSH 语音插件

桥接只是"声音的服务"，对话界面和麦克风按钮在 DSH 里，需要装插件。按 [`dsh-plugin/README.md`](dsh-plugin/README.md) 的步骤操作（把 `dsh-plugin\` 整个复制进你的 deepseek-harness 源码树，注册三处、构建、重启）。

## 八、流式数字人：回复出镜对口型（可选增强）

回复不只是一段声音——配合 **DUIX 数字人引擎**，小雅会把你的回复实时生成**口播视频**：TTS 音频交给 DUIX（`http://127.0.0.1:8383`），它渲染出对应口型的视频，女友窗在生成完毕后**视频 + 声音同刻播放**（音频已混入视频，天然同步）。

**流式分段**：长回复自动切成 ≤10 秒的小段，**当前段生成画面时，下一段语音已在后台合成**——一段播完立即续接下一段，全程不干等。**段级抢占**：对话中来了更新的回复，当前长任务的剩余段自动让位（已生成段保留），最新回复永远最快开始。生成的口播视频保留最近 200 条（`GET /api/dh/history` 可回看）。

**分段策略**（默认：**句子优先 + 48 字上限兜底**）：先按句读标点（`。！？!?；;，,`）把回复切成句子，短句累积成段（不超 48 字 ≈ 8-10s 音频）；单句超长（如无标点长句）时，在 48 字窗口内找最近的断点字符（逗号/句号/空格/冒号）切，实在没有则 48 字硬切。→ **按句子断：每段语义完整、TTS 语气连贯、口型对得上；48 字上限：每段 ≤10s，DUIX 十几秒出片、段间续接快**（听感与速度的平衡）。

> ⚙️ **自行调试**：段长由 `bridge-config.json` → `digital_human.segment_chars` 控制（默认 48）。调小（如 40）→ 段更短、出片更快但更碎；调大（如 60）→ 段更长更连贯，但每段生成更久（DUIX 单任务，总等待随之增加）。按你的显卡/听感偏好调整。

**麦克风打断**：你开始说话（点麦克风/插话）时数字人视频立即停止播放——只停播放，生成任务与已存视频不受影响。

**部署 DUIX**（二选一）：

1. **Docker 一键跑**（推荐）：本项目提供了**精简适配版 compose**（[`bridge/docker-compose-5060ti.yml`](bridge/docker-compose-5060ti.yml)）——适配 **RTX 50 系列**（5060 Ti / 5090 同架构），并**已去掉原项目的 Fish Speech TTS 与 FunASR ASR**（语音由本项目自己的 voice_bridge 负责），只保留数字人服务：

   ```powershell
   docker compose -f bridge\docker-compose-5060ti.yml up -d
   ```

   启动后数字人服务在 `http://127.0.0.1:8383`，共享卷 `d:/duix_avatar_data/face2face` 映射到容器 `/code/data`，成品视频直接落在宿主 `D:\duix_avatar_data\face2face\temp\`。

2. **手动跑**：拉取 `guiji2025/duix.avatar-5090` 镜像后 `python /code/app_local.py`（容器内）。

**配置**（`bridge-config.json` → `digital_human` 段）：`duix_base` 服务地址、`avatar_video` 形象视频（放共享卷 temp，如 `my_avatar.mp4`）、`segment_chars` 分段字数（默认 48 ≈ 10s）、`max_keep` 保留条数（默认 200）。

**提速技巧**（实测：7.3s 音频 20.1s → 14.1s，约 -30%）：
- **形象视频换 15fps**（输出规格跟随形象视频，1080p 画质不降）：`ffmpeg -i my_avatar.mp4 -vf fps=15 -c:v libx264 -crf 20 -preset fast -an my_avatar_15fps.mp4` 改名 my_avatar.mp4（原版备份）。
- **关超分**：共享卷放一份 `config.ini`（`[digital] chaofen = 0, batch_size = 2`，compose 已挂载 `/code/config/config.ini`），容器重建不丢，再快约 10%。

**开关**：工具行「数字人」按钮——开：等视频生成完，TTS+画面同步播放；关：去掉视频生成，回到接近即时的纯语音朗读。

> 不开数字人也完全可用：回复走即时逐句 TTS，女友窗播空闲动画即可。

## 九、启动

**只起桥接**（想先单独验证语音）：

```powershell
bridge\start-bridge.cmd
```

**一键全套**（桥接 + NapCatQQ + DSH Web + 浏览器）：

```powershell
set DSH_HARNESS=C:\dev\deepseek-harness
set NAPCAT_DIR=D:\QQ\NapCat\napcat   rem 可选：NapCat 安装目录（默认此值）
bridge\start-all.cmd
```

> - `DSH_HARNESS` 指向你第一步准备的那份 DSH 源码树（第六部分安装过插件的那个）。
> - `start-all.cmd` 会自动：起桥接 → 起 NapCatQQ（**会关闭所有运行中的 QQ 进程**再注入小号）→ 起 DSH Web → 开浏览器。
> - 不想自动起 NapCat（比如你要在电脑上正常用主号 QQ）：`bridge\start-all.cmd nq`（跳过 NapCat，QQ 双向聊天不工作）。
> - 只想单独起 NapCat：`bridge\start-napcat.cmd`（会等 OneBot :3000 就绪，失败时提示去 WebUI 检查）。

## 十、QQ 双向对话（可选）

在 QQ 上直接和 AI 女友聊天：你的 QQ 消息会注入 DSH 对话，回复以**文本 + 小雅语音**推回你的 QQ。

### 1. 装 NapCatQQ（登录 QQ 小号）

- 下载 **NapCat.Shell.Windows.Node.zip**（官方推荐 Shell 版；Framework/LiteLoaderQQNT 路线官方已不推荐）
- 解压到任意目录（如 `D:\QQ\NapCat`），用 `napcat\launcher-win10-user.bat` 启动（**会注入/拉起电脑版 QQ**，先关闭正在运行的 QQ）
- **QQ 登录用一个小号**（防风控；注入后 QQ 窗口通常隐藏，用手机 QQ 查看消息）
- 启动后电脑上 NapCat 的 WebUI：`http://127.0.0.1:6099`（token 看 `napcat\config\webui.json` 的 `token` 字段）

### 2. 在 NapCat WebUI 配置网络

WebUI → 网络配置：

1. **HTTP 服务器**：添加 → 名称随意、Host `127.0.0.1`、端口 `3000`、Token 记下来（发消息用）
2. **WebSocket 客户端**：添加 → 地址填完整 URL `ws://127.0.0.1:8765/api/qq/onebot`（**NapCat 主动连桥接，收 QQ 消息用**；不要配成 WebSocket 服务器，那会抢 8765 端口）

### 3. 配置桥接

`bridge-config.json` 加 `qq` 段：

```json
"qq": {
  "enabled": true,
  "napcat_base": "http://127.0.0.1:3000",
  "napcat_token": "你在 WebUI 设置的 token",
  "target_qq": 你的主号QQ号
}
```

> `target_qq` 是**接收回复的号**（你的主号）；NapCat 登录的小号负责收发。

### 4. 验证

- 从桥接发测试：`POST /api/qq/send {"text":"你好","voice":true}` → 主号收到文本+语音
- 发图片：`POST /api/qq/image {"path":"C:/path/to/a.png"}` → 主号收到图片（agent 生成图片后可直接推给你）
- 完整双向：用手机 QQ 主号**给小号发消息** → DSH 对话出现该消息 → 回复自动以文本+语音发回主号

### ⚠️ 易踩坑要点

| 坑 | 说明 |
|---|---|
| **语音收不到** | ① 别给小号**自己**发语音（QQ 不支持自己给自己发语音）；② 语音必须 **silk 格式**（`pip install pilk`，桥接已内置转换），mp3 base64 发不出去 |
| **QQ 事件进不来** | 必须配 **WebSocket 客户端**（NapCat 连 `ws://127.0.0.1:8765/api/qq/onebot`），配成 WebSocket 服务器会抢 8765 端口失败 |
| **重启 NapCat 后 OneBot 服务丢失** | NapCat 的 OneBot HTTP（3000）重启后有时不自动加载 —— 都在 **WebUI 里管理**（启用/保存），不要 kill 进程重启 |
| **WebUI 每次要 token** | 改 `napcat\config\webui.json` 的 `token` 字段为固定值即可 |
| **回复延迟** | 延迟主要来自 LLM 生成 + 整段 TTS 合成；回复越长越慢（分段推送优化是后续可选项） |
| **端口冲突** | 8765 桥接、3000 OneBot、6099 WebUI —— 被占时改配置并同步改桥接的 `napcat_base` |

## 使用说明

1. 点**麦克风**：开始连续聆听（每句自动识别发送；再点一下停止）
2. 点**⚡**：插话（亮，默认）/ 排队（灭）——说话打断回复 vs 回复读完再自动接上
3. 点**👧**：显示/隐藏女友动画窗；窗口可拖宽、双击换边
4. 点**🎭**：数字人开关——开：回复生成口播视频，TTS+画面同步播放（分段续接）；关：接近即时的纯语音朗读
5. 点**🔊**：开/关语音朗读
6. **QQ 对话**：手机 QQ 主号给小号发消息即可（需已按「十、QQ 双向对话」配置）

---

# 常见问题

1. **pip 装依赖太慢/超时** → 用清华镜像：

   ```powershell
   pip install -r bridge\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

2. **HuggingFace 下载模型慢/失败** → 用国内镜像：

   ```powershell
   set HF_ENDPOINT=https://hf-mirror.com
   ```

   设置后重新执行下载命令（`huggingface-cli download ...`）。

3. **第一次 TTS 很慢（10~60s）** → 正常。模型首次懒加载 + 预热，之后每句约 0.5s 出音。

4. **STT 偶尔识别为空** → FunASR 对超短语音/纯噪声会返回空结果被丢弃（日志见 `funasr returned empty result`）；若切换到 whisper 后端，则其 1-token 退化也会被判空，均属正常防护。

5. **桥接启动但模型加载失败** → 检查 `bridge-config.json` 里 `tts.model_name` 路径是否存在（路径拼写、盘符、斜杠方向），以及显卡驱动是否够新（见前置第 4 条）。

6. **桥接端口被占** → 改 `bridge-config.json` 的 `port`，并同步改插件的 `s2s.voice.bridge`（浏览器 localStorage）。

7. **女友窗不显示** → 确认 `assets/` 目录存在、桥接已启动（窗口每 30s 拉一次素材列表）。

8. **数字人没出视频** → 检查：① 工具行数字人开关是否打开；② `bridge-config.json` 的 `digital_human.enabled` 是否为 true、`duix_base` 是否指向 DUIX 服务；③ 形象视频（`avatar_video` 配置的文件名，如 `my_avatar.mp4`）是否放在共享卷 temp 目录（DUIX 容器映射的 `D:\duix_avatar_data\face2face\temp\`）；④ 生成状态用 `GET http://127.0.0.1:8765/api/dh/status` 查看（done 且带 video_url 即为就绪）。

9. **数字人视频生成了但没播** → 刷新 DSH 页面（插件逻辑更新后需刷新加载）；确认女友窗可见。生成中窗口底部会显示「数字人生成中 i/n…」，一段播完自动续接下一段。

10. **声音不像参考音频** → 检查 `ref_audio.wav` 是否清晰无杂音、参考文本（`voices/<音色名>/ref_text.txt`）是否与录音**逐字一致**（标点也要对）；参考音频建议 3-10 秒单一完整句，不要跨句截取（跨句会导致输出混入参考内容）。OmniVoice 服务未启动时检查 `~/omnivoice-wsl/server.log` 是否有报错。

---

## 自定义素材（音色 / 待机动画 / 数字人形象）

项目自带 **5 套默认待机动画**（`assets/bg-images/bg2|bg4|bg9|bg56|bg5`），开箱即用。以下说明如何添加自己的素材——三类互不影响，随时可在工具行切换：

**🎙️ 自定义音色**（`voices/` 子文件夹，可多个并存）
1. 在 `voices/` 下新建子文件夹，文件夹名即音色名（如 `voices/我的声音/`）
2. 放入 `ref_audio.wav`（10 秒左右干净人声）+ `ref_text.txt`（该音频实际朗读的文本，UTF-8）
3. 重启桥接 → 工具行「🎙️ 音色」按钮循环切换

**🖼️ 自定义待机动画**（`assets/bg-images/` 子文件夹，可多个并存）
1. 在 `assets/bg-images/` 下新建子文件夹（如 `assets/bg-images/我的背景/`），放入视频/图片
2. 无需重启：工具行「🖼️ 待机」按钮即时切换（每次查询自动扫描新文件夹）
3. 文件夹名即待机组名，建议用英文/数字避免 URL 编码问题

**👤 自定义数字人形象**（共享卷 temp 目录，`D:\duix_avatar_data\face2face\temp\`）
1. 把形象视频 mp4 放进 temp 目录（DUIX 容器与宿主共用的共享卷）
2. 工具行「👤 形象」按钮切换；或配置 `bridge-config.json` 的 `digital_human.avatar_video` 指定默认形象
3. 形象素材不随仓库分发（属个人素材），输出规格（分辨率/帧率）跟随形象视频文件

> 三类选择都会记忆（localStorage），刷新/重启后自动恢复。

---

# 许可

Apache-2.0（详见 LICENSE）。复用 HuggingFace speech-to-speech（Apache-2.0）与 deepseek-harness 插件框架（MIT）；`assets/` 素材为 AI 生成，仅用于演示。
