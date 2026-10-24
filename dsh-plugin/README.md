# DSH 语音 AI 女友插件（voice-ai-girlfriend）

DeepSeek Harness 的**浏览器语音插件**：麦克风语音输入、回复朗读（TTS）、AI 女友动画窗、QQ 双向对话。

> ✅ **已适配 DSH 0.2.1（2026-10-25 实测，`v0.4.0`）**：在 `dsh 0.2.1-rc.2` / profile `web-v021` / `:3092` 上实测通过——插件正常加载、工具行与女友窗渲染、`useChat` 的 `assistant-step` 回复文本可读、回复触发桥接 `/api/tts` 与 `/api/dh/speak`、余额 pill 显示在 composer 底部统计行。0.2.x 的 Web 插件系统（`window.__ModuleLoader__.load`、`<style data-plugin-css>`、`conversation.input.dock` / `input.left` / `composer.dock` 槽位）与 0.1.3 保持同一套契约，无需改动业务代码。
>
> ✅ **兼容 dsh 0.1.3-alpha.1**（`v0.3.x` 已验证）；rc.8 请使用 `v0.3.0`。

> 🎥 **成品展示**（抖音）：
> - [口播视频效果 ①](https://www.douyin.com/user/self?from_tab_name=main&modal_id=7676326565919149339)
> - [口播视频效果 ②](https://www.douyin.com/user/self?from_tab_name=main&modal_id=7678063553395412259)

> ⚠️ **本插件是完整方案的一部分**：语音识别/合成依赖配套的 **voice bridge**（Python 服务，FunASR + OmniVoice TTS（WSL2 + FlashInfer）），QQ 对话依赖 **NapCatQQ**。请配合完整仓库使用：**[beiyege-01/dsh-voice-ai-girlfriend](https://github.com/beiyege-01/dsh-voice-ai-girlfriend)**（含桥接代码、模型准备、一键启动脚本、安装文档）。

## 适配版本

| 组件 | 适配版本 | 状态 |
|---|---|---|
| **DSH（主用）** | `dsh 0.2.1-rc.2` / profile `web-v021`（端口 3092） | ✅ 2026-10-25 实测通过 |
| **DSH（兼容）** | `dsh 0.1.3-alpha.1` / profile `web-v013`（端口 3080） | ✅ 2026-09-14 实测通过 |
| **DSH（旧版）** | `rc.8` / profile `web` | 用插件 `v0.3.0`；`v0.4.0` 声明 `engines.dsh >=0.1.3-alpha.1` |
| 本插件 | `@beiyege-01/dsh-voice-ai-girlfriend` **`v0.4.0`** | 构建：`node build.mjs` → `lib/client.js` |
| 语音桥接 | `voice_bridge.py`（FastAPI/uvicorn `:8765`，Python 3.14 venv） | 配套主仓库 |
| TTS 引擎 | OmniVoice（WSL2 + FlashInfer `:9877`） | 音色全部为 OmniVoice 克隆 |
| 数字人 | DUIX `guiji2025/duix.avatar-5090:trt10.9`（宿主 `:9000`） | 冷启动 1-3 分钟 |

> **`v0.4.0` 变更**
> - **适配 DSH 0.2.1-rc.2**：在官方 0.2.x Web 插件系统上完成加载、槽位、聊天读取、桥接调用全链路实测。
> - **余额改为真正的 stats pill**：0.1.3 时代的 `window.__dshStatsExtras` 是本地 harness 的 stats 行补丁，官方 0.2.x 没有这个缝，旧版「渲染 null + 注册全局」导致余额不可见。现在 `BalanceBadge` 直接在 `conversation.composer.dock` 渲染 `余额 ¥xx.xx`，点击刷新，不再依赖任何 harness 补丁。
> - `package.json` 新增 `engines.dsh: ">=0.1.3-alpha.1 <0.3.0"`，供新版 DSH 的插件兼容性检查使用。
> - 清理失效文件：删除引用已移除 `@deepseek-ai/dsh-invariants` 的 `src/invariant.ts`、指向不存在基础配置的 `tsdown.config.ts`；`tsconfig.json` 改为独立可读配置。

## 维护节奏

DSH 0.2.x 适配已恢复跟进（2026-10）。欢迎提 issue / PR；主仓库与本插件仓库同步更新。

## 安装

把 `<profile>` 换成你的 DSH profile 名（0.2.x 常用 `web-v021`，0.1.3 是 `web-v013`）：

```bash
dsh plugin --profile <profile> add github:beiyege-01/dsh-voice-ai-girlfriend-plugin
```

> `--profile` 必须写在 `add` 之前（DSH 0.2.1 起 `dsh plugin` 只在该位置识别它）。
>
> pnpm ≥10 对 git 依赖的构建脚本有 allowBuilds 限制：按安装时 pnpm 打印的提示，在 profile 的 `pnpm-workspace.yaml` 的 `allowBuilds` 里加一行允许 `esbuild` 后重跑。

安装后重启该 profile 的 dsh（或在 Web 里按提示重启），浏览器刷新即可看到输入框上方的语音工具行。

## 功能

- 🎙️ **语音输入**：麦克风 → 桥接 FunASR 中文识别 → 注入对话（连续聆听、自动端点）
- 🔊 **语音回复**：回复流式 TTS 朗读（OmniVoice 声音克隆，跑在 WSL2 + FlashInfer 加速，音色由参考音频决定，支持 600+ 语言）
- ⚡ **插话/排队**：亮=说话打断回复；灭=回复读完句子排队接上
- 👧 **数字人窗口**：右侧动画窗（空闲/说话视频，素材自备），自动贴右侧插件边缘不被遮挡
- 💬 **QQ 双向**：QQ 消息注入对话 + 回复文本/语音/图片推送（NapCat）
- 🎭 **三类预设一键切换**：工具行三个按钮分别循环切换 **TTS 音色 / 数字人形象 / 待机动画**，选择自动记忆（localStorage）。音色（`voices/<名字>/`）、待机（`assets/bg-images/<名字>/`）、形象（共享卷 temp 放 mp4）均可自行添加，无需改代码
- 💰 **余额 pill**：composer 底部统计行显示 DeepSeek 余额（点击刷新，10 分钟缓存）

## 使用前提

1. 克隆主仓库并按其 README 装好 voice bridge（`start-all.cmd` 一键）
2. 本插件的桥接地址默认 `http://127.0.0.1:8765`（localStorage `s2s.voice.bridge` 可改）

## 构建

```bash
npm install
node build.mjs   # 产物 lib/client.js（browser bundle）+ lib/index.js（服务端空入口）
```

## 验证记录（DSH 0.2.1-rc.2）

- 插件入口出现在 `window.__DSH_BOOT__` 的 application batch，`/plugins/??<pkg>/client.js` 返回 200。
- 浏览器控制台出现 `[ui-voice] loaded, bridge = http://127.0.0.1:8765`，无插件异常。
- 工具行 8 个控件（音色 / 形象 / 待机 / 麦克风 / 朗读 / 数字人 / QQ / 女友窗 / 插话）全部渲染。
- 发送一条真实消息：`assistant-step` 文本被回复监听器读到，桥接收到 `POST /api/dh/speak`（数字人提交）与 `POST /api/tts`（失败的 TTS 是因为测试机 OmniVoice 未启动，并非插件问题）。
- composer 底部统计行显示 `余额 ¥128.66`。
