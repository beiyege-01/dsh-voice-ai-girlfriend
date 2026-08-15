# DSH 语音 AI 女友（Voice AI Girlfriend）

把 **DeepSeek Harness（DSH）** 变成能"开口说话"的 AI 女友：

- 🎙️ **语音输入**：点一下麦克风，连续聆听，每句话说完自动识别、自动发送（whisper-large-v3）
- 🔊 **语音回复**：代理的回复按**句子级流式 TTS** 朗读（Qwen3-TTS 声音克隆，音色由你的参考音频决定）
- ⚡ **插话 / 排队开关**：点亮=说话立即打断回复；熄灭=连续对话，句子排队自动接上
- 👧 **数字人上屏**：右侧（或左侧）全高动画窗口，空闲播放 `bg-images/`（随仓库分发），回复时切换说话视频（`task-videos/`，自备或第三方 API 回传）

```
┌────────────────────────────────────────────┐
│  浏览器（DSH Web GUI :3080）                 │
│  ┌──────────┐  ┌─────────────────────────┐  │
│  │ 对话面板   │  │ 女友窗（bg/task 视频）   │  │
│  │ 麦克风+⚡  │  │                        │  │
│  └──────────┘  └─────────────────────────┘  │
│   麦克风采集 ──▶ STT ──▶ 代理回复 ──▶ TTS ──▶ 播放 │
└──────────┬─────────────────────────────────┘
           │ HTTP (CORS)
┌──────────▼─────────────────────────────────┐
│  voice_bridge (:8765)                      │
│  /api/stt  whisper-large-v3                │
│  /api/tts  Qwen3-TTS 声音克隆               │
│  /api/media + /media/*  素材静态服务         │
└────────────────────────────────────────────┘
```

## 目录结构

```
dsh-voice-ai-girlfriend/
├── bridge/            # 语音桥接（独立可跑，Python/FastAPI）
│   ├── voice_bridge.py
│   ├── bridge-config.example.json   # 配置模板（复制为 bridge-config.json）
│   ├── requirements.txt
│   ├── start-bridge.cmd             # 只起桥接
│   └── start-all.cmd                # 桥接 + DSH Web 一键启动
├── assets/            # 数字人素材
│   ├── bg-images/     # 空闲动画（随仓库分发，7 个）
│   └── task-videos/   # 回复说话动画（自备/API 回传，不随仓库分发）
│                      #   └─ README.md 里有两种准备方式
├── dsh-plugin/        # DSH 客户端插件源码（mic/开关/女友窗/流式朗读）
│   └── README.md      # 安装到 DSH 的详细步骤
└── docs/              # 开发日志等
```

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11（脚本是 .cmd） |
| GPU | NVIDIA + CUDA（模型推理在 GPU，16GB 显存可流畅跑） |
| Python | 3.10+ |
| Node.js / pnpm | 运行 DSH 用 |
| 模型 | whisper-large-v3（HF 自动下载 ~3GB）+ Qwen3-TTS-12Hz-1.7B-VoiceDesign（本地目录） |

## 快速开始

### 1. 克隆并创建 Python 环境

```bat
git clone https://github.com/<你的用户名>/dsh-voice-ai-girlfriend.git
cd dsh-voice-ai-girlfriend
python -m venv venv-speech
venv-speech\Scripts\activate
pip install -r bridge\requirements.txt
```

### 2. 准备模型

- **STT**：`openai/whisper-large-v3` 会在首次 STT 调用时自动从 HuggingFace 下载（或已缓存），不用配。
- **TTS**：需要一个 Qwen3-TTS-12Hz-1.7B-VoiceDesign 的本地模型目录（见第 4 步，把它填进配置）。

### 3. 准备参考音频（音色来源）

> 参考音频**不在仓库里**，请自备一段 10 秒左右、干净人声的录音（最好按下面的文本朗读），命名为 `ref_audio.wav` 放到**仓库根目录**：

```
靠北啦，不想聊就不聊咯，摆什么臭架子哦，真以为自己很厉害，真以为自己很好看，我也是这么觉得啦，明天继续叫你好不好，笨蛋。
```

然后在 `bridge-config.json` 里把 `tts.ref_text` 改成你实际朗读的文本（音色克隆质量依赖文本与录音一致）。

### 4. 写配置

```bat
copy bridge\bridge-config.example.json bridge\bridge-config.json
```

编辑 `bridge\bridge-config.json`，**把占位路径换成你的真实路径**：

- `tts.model_name`：`C:/你的QwenTTS模型目录/Qwen3-TTS-12Hz-1.7B-VoiceDesign` → 你的模型目录（必改）
- `tts.ref_text`：改成你参考音频实际朗读的文本（建议改）
- 其他（素材目录 `assets/`、参考音频 `ref_audio.wav`）基于仓库根自动解析，一般不用动

### 5. 准备说话动画（可选）

模型回复时女友窗播放的说话视频**不随仓库分发**，两种方式任选（详见 [`assets/task-videos/README.md`](assets/task-videos/README.md)）：

1. **手动放入**：把数字人"开口说话"的短循环视频（`.mp4/.webm/.ogg/.mov/.m4v`）放进 `assets/task-videos/`，女友窗每 30s 自动拾取；
2. **第三方 API 回传**：实时数字人生成服务把生成的视频直接写入 `assets/task-videos/`，轮播自动切换。

> 不装也不影响使用：说话时女友窗会继续播空闲动画。

### 6. 启动

**只起桥接**（先验证语音链路）：

```bat
bridge\start-bridge.cmd
```

**一键全套**（桥接 + DSH Web + 浏览器）：

```bat
set DSH_HARNESS=C:\path\to\deepseek-harness   &  rem 指向 DSH 源码树
bridge\start-all.cmd
```

### 6. 安装 DSH 语音插件

DSH 插件运行在 DSH 框架内，不能独立运行。按 [`dsh-plugin/README.md`](dsh-plugin/README.md) 的步骤把它装进你的 deepseek-harness 源码树，重启 dsh web 后输入栏会出现麦克风按钮。

### 7. 使用

1. 点**麦克风**：开始连续聆听（每句自动识别发送；再点一下停止）
2. 点**⚡**：插话（亮，默认）/ 排队（灭）——说话打断回复 vs 回复读完再自动接上
3. 点**🎬**：显示/隐藏女友动画窗；窗口可拖宽、双击换边
4. 点**🔊**：开/关语音朗读

## 常见问题

- **第一次 TTS 很慢（10~60s）**：模型首次懒加载 + 预热，之后 TTFA 约 0.5s。
- **STT 偶尔识别为空**：whisper 对超短语音（1 个 token）会判空丢弃，日志可见 `degenerate (1-token)`，属正常防护。
- **桥接端口被占**：改 `bridge-config.json` 的 `port`，并同步改插件的 `s2s.voice.bridge`（localStorage）。
- **女友窗不显示**：确认 `assets/` 目录存在、桥接已启动（窗口每 30s 拉一次素材列表）。
- **说话时女友窗不换视频**：`assets/task-videos/` 为空（说话动画自备，见安装第 5 步）；没视频时会继续播空闲动画，属正常。

## 许可

Apache-2.0（详见 LICENSE）。复用 HuggingFace speech-to-speech（Apache-2.0）与 deepseek-harness 插件框架（MIT）；`assets/` 素材为 AI 生成，仅用于演示。
