# DSH 0.2.1 适配记录（2026-10-25）

## 结论

`@beiyege-01/dsh-voice-ai-girlfriend` **v0.4.0** 已在 **`dsh 0.2.1-rc.2`**（profile `web-v021`，端口 `:3092`）上实测通过：

- 插件出现在 `window.__DSH_BOOT__` 的 application batch，`/plugins/??<pkg>/client.js` 返回 200；
- 浏览器控制台只有 `[ui-voice] loaded, bridge = http://127.0.0.1:8765`，无插件异常；
- 工具行渲染出 8 个控件（音色 / 形象 / 待机 / 麦克风 / 朗读 / 数字人 / QQ / 女友窗 / 插话）；
- 发送真实消息后，`useChat` 的 `assistant-step` 文本被回复监听器读取，桥接收到 `POST /api/dh/speak` 与 `POST /api/tts`；
- composer 底部统计行显示 `余额 ¥128.66`。

## 为什么业务代码基本不用改

0.2.1 的 Web 插件系统与 0.1.3 保持同一套契约：

| 契约 | 0.2.1 现状 |
|---|---|
| 客户端模块注册 | 仍是 `window.__ModuleLoader__.load({ id, factory })` 闭包；样式仍认 `<style data-plugin data-plugin-css>` |
| 客户端清单 | `package.json` 的 `dsh.client.platform/inject` + `exports["./client"]`，`dsh.bundle.patch` 指向 `cordis.patch.yml` |
| 槽位 | `conversation.input.dock` / `conversation.input.left` / `conversation.composer.dock` 仍是 list 槽位，注册参数（`id`/`order`/`locale`/`inject`）未变 |
| 聊天读取 | `useChat((s) => s)` 仍返回含 `nodes.values()` 的 ChatSnapshot；`kind: 'assistant-step'` 的 `data.blocks[].kind === 'text'` 仍成立 |
| 会话写入 | `ctx.sessions.binding(id).session.prompt(parts, 'queue' \| 'steer')` 与 `getSnapshot().running` 未变 |

因此回复监听、麦克风注入、数字人提交、QQ 桥接都无需改业务逻辑。

## v0.4.0 实际改动

1. **余额徽章改成真正的 stats pill**（`src/client/BalanceBadge.tsx`）
   - 旧实现：`BalanceBadge` 渲染 `null`，把余额塞进 `window.__dshStatsExtras`。这是 0.1.3 时代本地 harness 的 **stats-extras 补丁**，官方 0.2.x 没有这个消费方，所以余额在 0.2.1 完全不可见。
   - 新实现：直接在 `conversation.composer.dock` 渲染 `余额 ¥xx.xx` 按钮，点击刷新，10 分钟自动刷新，不依赖任何 harness 补丁。
2. **兼容性声明**：`package.json` 增加 `engines.dsh: ">=0.1.3-alpha.1 <0.3.0"`（0.2.1 目前仍先看 peerDependencies，下一版本起由该范围独立判定）。
3. **清理失效文件**：
   - 删除 `src/invariant.ts`（引用 0.2.0-rc.2 已移除的 `@deepseek-ai/dsh-invariants`，且从未被入口导入）；
   - 删除 `tsdown.config.ts`（指向不存在的 `../tsdown.client.ts`）；
   - `tsconfig.json` 去掉指向不存在的基础配置的 `extends`/`references`，改为独立 `noEmit` 配置；
   - 删除未被使用的 `VoiceToolbar.module.css`（esbuild 因 CSS side-effect 规则本来就不会打进 bundle）。
4. **脚本**：`bridge/start-full-stack.ps1` 默认启动 0.2.1（`web-v021` / `:3092`），新增 `-V013`、`-Port`、`-Harness`、`-Profile`；`start-all.cmd` 支持 `DSH_PROFILE` / `DSH_PORT`（默认 `web-v021` / `3092`），不再使用旧的 `pnpm dsh web` 别名。
5. **文档**：README 版本矩阵、安装命令（`--profile` 必须在 `add` 之前）、启动参数、余额 pill 行为同步更新。

## 复现验证

```powershell
# 1. 桥接（另开窗口，需要 DEEPSEEK_API_KEY）
cd D:\speech-to-speech
$env:DEEPSEEK_API_KEY = (Get-ItemProperty HKCU:\Environment).DEEPSEEK_API_KEY
.\venv-speech\Scripts\python.exe -m uvicorn voice_bridge:app --host 127.0.0.1 --port 8765

# 2. DSH 0.2.1 + 测试 profile
cd E:\DSH\deepseek-harness-v021
pnpm dsh --profile web-v021voice --port 3093 --no-open

# 3. 打开带 token 的地址，确认输入框上方出现语音工具行、右下女友窗、底部余额
```

测试机环境：Windows 11 / Node 25 / DSH 0.2.1-rc.2 / 浏览器 Edge 156（headless CDP）。
