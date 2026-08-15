# DSH 语音插件（ui-voice）

这是 DSH（deepseek-harness）的**客户端插件**：麦克风输入、⚡插话/排队开关、语音朗读开关、AI 女友动画窗、句子级流式 TTS 朗读。它运行在 DSH 框架内（slot 系统、session prompt、locale），**不能独立运行**，必须装进 DSH 源码树。

## 它做了什么

| 组件 | 功能 |
|---|---|
| `MicButton` | 点一下连续聆听；静音 1.8s 端点；barge-in 打断监听（说话即停朗读） |
| `BusyToggle`（⚡） | 插话（steer）/ 排队（queue）投递模式开关，存 `s2s.voice.interrupt` |
| `VoiceToggle`（🔊） | 语音朗读开关，存 `s2s.voice.enabled` |
| `CompanionToggle`（🎬）+ `CompanionWindow` | 女友动画窗：`bg-images/` 空闲 / `task-videos/` 回复，30s 轮询素材 |
| `reply-listener` + `speaker` + `sentences` | 代理回复按句子切分 → 逐句 TTS → FIFO 播放，打断/吞剩余 |

桥接地址默认 `http://127.0.0.1:8765`，可用 localStorage 覆盖：`s2s.voice.bridge`。

---

# 安装到 DSH（小白版）

## 前置条件（已完成主 README「一、前置准备」的前提下）

- ✅ 一份 **deepseek-harness 源码树**（下文叫 `<HARNESS>`），并且已经 `pnpm install` 过
- ✅ pnpm 可用（`pnpm --version` 有输出）

> 没做过？先回主 README 的「一、前置准备」第 6 步。

## 第 1 步：把插件源码放进去

在**项目根目录**（dsh-voice-ai-girlfriend）执行，把 `dsh-plugin\` 复制到 DSH 的客户端插件目录：

```powershell
xcopy /E /I dsh-plugin\* <HARNESS>\packages\client\ui-voice\
```

> `<HARNESS>` 换成你 DSH 源码树的实际路径，比如 `C:\dev\deepseek-harness`。
> 复制完成后确认这个文件存在：`<HARNESS>\packages\client\ui-voice\package.json`。

## 第 2 步：注册插件（三处，照着抄）

用记事本打开下面三个文件，加对应内容：

### ① `<HARNESS>\tsconfig.client.json`

在 `"references"` 数组里（和其他 `./packages/client/...` 行排在一起）加一行：

```jsonc
"references": [
  { "path": "./packages/client/ui-plan" },
  { "path": "./packages/client/ui-voice" },   // ← 加这一行
  { "path": "./packages/client/ui-workspace" }
]
```

### ② `<HARNESS>\packages\bundle\web-app\cordis.patch.yml`

在客户端插件列表里（找 `- id: ui-...` 按字母序的区域），插入：

```yaml
    # Voice chat: composer mic control + bridge STT/TTS + companion window.
    - id: ui-voice
      name: '@deepseek-ai/dsh-client-ui-voice'
```

### ③ `<HARNESS>\packages\bundle\web-app\package.json`

在 `"dependencies"` 里（其他 `@deepseek-ai/dsh-client-...` 行旁边）加一行：

```jsonc
"dependencies": {
  "@deepseek-ai/dsh-client-ui-trajectory": "workspace:^",
  "@deepseek-ai/dsh-client-ui-voice": "workspace:^",   // ← 加这一行
  "@deepseek-ai/dsh-client-ui-workspace": "workspace:^"
}
```

## 第 3 步：构建

在 `<HARNESS>` 目录打开终端，依次执行：

```powershell
cd <HARNESS>
pnpm install
pnpm exec tsc -b packages/client/ui-voice/tsconfig.json
pnpm --filter @deepseek-ai/dsh-client-ui-voice bundle
```

> - 三行都要跑，**前一行成功后再跑下一行**（任何一行报错先看文末 FAQ）。
> - Windows 下不要用项目里某些脚本自带的 `rm` 命令，按上面顺序手动执行即可。

## 第 4 步：重启并验证

**重启 dsh web**（新增插件必须重启，插件清单启动时确定；只刷页面不行）。

启动后按 `F12` 打开浏览器控制台，应看到：

```
[ui-voice] loaded, bridge = http://127.0.0.1:8765
```

输入栏工具行出现：🔊 🎬 ⚡ 🎙️（顺序：朗读、女友窗、插话开关、麦克风）。

> 看到这条日志但麦克风点不了 → 检查桥接是否已启动（`bridge\start-bridge.cmd`）。

---

# 构建 FAQ

| 现象 | 原因 / 解决 |
|---|---|
| `tsc` 报 `TS6133: 'xxx' is declared but its value is never read` | 某处 import 没用到，删掉那个 import 再跑 |
| `tsc` 报找不到 `@deepseek-ai/dsh-client-ui-conversation/client` 等 | `pnpm install` 没跑，或 workspace 链接没建好，重跑 `pnpm install` |
| `bundle` 报 `rm: command not found` | Windows 没有 `rm`，手动先跑 `tsc` 再跑 bundle 那行 |
| 重启后没有麦克风按钮 | 第 2 步三处注册漏了某处；或没重启 dsh web 进程 |
| 控制台报 CORS / fetch 失败 | 桥接没启动，或 `bridge-config.json` 的 `cors_origins` 没包含 `http://127.0.0.1:3080` |

---

# 源码结构

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
