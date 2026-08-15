# DSH 语音插件（ui-voice）

这是 DSH（deepseek-harness）的**客户端插件**：麦克风输入、⚡插话/排队开关、语音朗读开关、AI 女友动画窗、句子级流式 TTS 朗读。它运行在 DSH 框架内（slot 系统、session prompt、locale），**不能独立运行**。

## 它做了什么

| 组件 | 功能 |
|---|---|
| `MicButton` | 点一下连续聆听；静音 1.8s 端点；barge-in 打断监听（说话即停朗读） |
| `BusyToggle`（⚡） | 插话（steer）/ 排队（queue）投递模式开关，存 `s2s.voice.interrupt` |
| `VoiceToggle`（🔊） | 语音朗读开关，存 `s2s.voice.enabled` |
| `CompanionToggle`（🎬）+ `CompanionWindow` | 女友动画窗：`bg-images/` 空闲 / `task-videos/` 回复，30s 轮询素材 |
| `reply-listener` + `speaker` + `sentences` | 代理回复按句子切分 → 逐句 TTS → FIFO 播放，打断/吞剩余 |

桥接地址默认 `http://127.0.0.1:8765`，可用 localStorage 覆盖：`s2s.voice.bridge`。

## 安装到 DSH

> 前置：一份 deepseek-harness 源码树（下面简称 `<HARNESS>`），pnpm 可用，`pnpm install` 已完成。

### 1. 放置插件源码

```bat
xcopy /E /I dsh-plugin\* <HARNESS>\packages\client\ui-voice\
```

### 2. 注册插件（三处）

以现有插件为参照（比如 `packages/client/ui-conversation`），把 `ui-voice` 加进：

1. **`<HARNESS>\tsconfig.client.json`** —— `references` 数组加一行指向 `packages/client/ui-voice`（含其 `tsconfig.json`）。
2. **`<HARNESS>\packages\bundle\web-app\cordis.patch.yml`** —— 按字母序加一行插件注册（客户端插件列表）。
3. **`<HARNESS>\packages\bundle\web-app\package.json`** —— `dependencies` 加 `"@deepseek-ai/dsh-client-ui-voice": "workspace:^"`。

### 3. 构建

```bat
cd <HARNESS>
pnpm install
pnpm exec tsc -b packages/client/ui-voice/tsconfig.json
pnpm --filter @deepseek-ai/dsh-client-ui-voice bundle
```

> 注意：Windows 下不要用构建脚本里的 `rm`，手动按上面顺序跑 tsc 再 bundle。

### 4. 重启并验证

重启 dsh web（**新增插件必须重启**，插件清单启动时确定）。启动后浏览器控制台应看到：

```
[ui-voice] loaded, bridge = http://127.0.0.1:8765
```

输入栏工具行出现：🔊 🎬 ⚡ 🎙️（顺序：朗读、女友窗、插话开关、麦克风）。

## 源码结构

```
src/client/
├── index.ts                 # 插件入口：注册 5 个 slot + sendText（steer/queue）
├── bridge.ts                # 桥接 HTTP 封装（stt/tts/media）
├── contract.ts              # 注入给组件的接口（sendText/speaker/companion/…）
├── MicButton.tsx            # 麦克风 + 连续聆听 + barge-in
├── BusyToggle.tsx           # ⚡ 插话/排队开关
├── VoiceToggle.tsx          # 🔊 朗读开关
├── CompanionToggle.tsx      # 🎬 女友窗开关
├── locales.ts               # zh/en 文案
└── voice/
    ├── recorder.ts          # 采集 + 静音端点 + 打断检测
    ├── reply-listener.tsx   # 监听回复 → 句子级 TTS 流式
    ├── speaker.ts           # AudioContext 播放队列（可打断）
    ├── sentences.ts         # 中文句子切分 + 纯标点过滤
    ├── companion.tsx        # 女友动画窗（拖宽/换边）
    └── companion-controller.ts
```
