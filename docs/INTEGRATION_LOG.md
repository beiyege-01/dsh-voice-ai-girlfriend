# 语音接入开发日志（INTEGRATION LOG）

> 记录 DSH 语音对话集成的每一步执行结果与经验,防止跨会话遗忘。
> 配套设计文档:`DSH-语音接入-设计方案.md`(最终规格)。最后更新:2026-08-16。

---

## 当前状态

- **T1–T8 全部完成并经用户验证 —— 项目收官** ✅(2026-08-16)
  - T1 桥接骨架 / T2 STT / T3 TTS / T4 插件骨架 / T5 语音输入 / T6 回复朗读 / T7 打磨 / T8 女友动画窗+一键启动器,全部通过。
- **收尾待办**:
  1. ~~图标显示小 bug~~ → **澄清非 bug**:白色麦克风 = 待机态,按设计如此,无需修。
  2. **连续聆听模式已实现**(bundle 42.75kB 已上线):点一次一直听,每句话停顿 1.8s 自动断句 → 排队串行 STT → 发送;再点停止并丢弃队列;开始聆听会打断朗读(barge-in)。待用户实测。
  3. **一键启动器实测**(待用户):`D:\speech-to-speech\start-dsh-voice.cmd` —— 关掉当前 dsh web + 停掉开发期后台桥接后,双击启动器走完整流程。
4. **连续聆听实测中发现上游缺陷并已加固**:用户连续说两段,第一段 200,第二段 500 —— 根因是上游 whisper handler 硬编码 `pred_ids[0,1]`(假设≥2 token),近乎静音/超短句只生成 1 个 token 就 IndexError。桥接层 `_transcribe` 已捕获 IndexError → 返回空识别(客户端本就跳过空文本),连续聆听不再断。**教训:上游 handler 的假设要当边界处理,桥接层做防御。**
5. **重复朗读 = 多浏览器标签页**:用户开多个 3080 标签页,每个标签页加载一套插件、各有一个回复监听器,同一条回复被多个标签页各读一遍。**用单个标签页即可。**
6. **进程判据教训**:`python -m uvicorn` 会生成**父子两个 python 进程**(启动父进程 + 服务子进程),不能按 python 进程数判断「桥接是否重复」;正确判据是 **8765 的监听者只有一个**(Get-NetTCPConnection -LocalPort 8765 -State Listen)。曾误把父进程当僵尸杀掉,连带整座桥(任务树清理)被误停,已恢复。
7. **日常启动规范(最终)**:一键 `D:\speech-to-speech\start-dsh-voice.cmd`(起桥接独立窗口 → 等 health → 起 dsh web → 开浏览器);**只保留一个浏览器标签页**;启动器方式下桥接独立存活,不受 dsh web 重启影响。
8. **回声防护(已实现并用户验证通过)**:用户在车里连续说话时,TTS 朗读的声音被仍在聆听的麦克风录回 → 形成回声消息。修复:`ReplySpeaker` 朗读状态变化时,`MicButton` 对 `MicRecorder.setPaused(speaker.speaking)` —— **朗读中麦克风暂停采集(丢弃缓冲、不端点),朗读结束自动恢复聆听**;连续模式不受影响。用户实测:朗读无回声 ✓ 第二段自动发送 ✓ 连续模式保持 ✓。
9. **两个体验增强(已实现上线)**:
   - **动画视频热更新**:CompanionWindow 改为挂载时读取 + **每 30 秒轮询** `/api/media/*`,只有列表变化才更新状态(不重启正在播的视频)。用户往 `bg-images/`、`task-videos/` 丢新视频,最多 30 秒自动生效;若同名字替换的文件仍显示旧内容(浏览器视频缓存),Ctrl+F5 强刷即可。
   - **喇叭打断**:VoiceToggle 关闭时 `speaker.stop()` 立刻停掉正在朗读的回复(此前只控制未来回复,不打断当前)。
11. **句子级流式 TTS(已上线,按原项目逻辑)**:长回复不再等整段合成完 —— 回答文本流式出现时,按句子(。！？!?…)切分,每句一完成就串行 fetch `/api/tts` 入播放队列,边播边合成(第一句响起时后面的还在生成,与原后端 LMOutputProcessor 逐句切分一致)。
    - `voice/sentences.ts`:中文/英文句末标点切分,返回 complete + partial(未终结的尾部不读)。
    - `voice/speaker.ts`:改为 FIFO 播放队列(speak 入队、onended 播下一句;`speaking` = 播放中或队列非空,动画窗/回声防护跟整段)。
    - `voice/reply-listener.tsx`:订阅整个快照,跟踪 running+settled 节点文本;按节点 key 记录「已读完整句数」;**首次见到节点即播种**(当前完整句数标记已读 → 历史/挂载时内容不重读);新完整句按 (anchorSeq,index) 排序进串行 fetch 链(句 N+1 的请求在 N 返回后开始,播放独立排空 → 管道化);partial 句等补完才读。
    - **打断**:MicButton 开始采集 → `interruptReply()`(停播放 + abort 在途请求 + 监听器把当前回复的剩余句标记跳过,下一条回复正常读);关喇叭同样 abort 并停播放。
    - 桥接无需改动(逐句调 /api/tts 即可);逐句合成音色衔接可能有细微差异(与原项目一致)。
13. **自动语音打断(方案 C,已实现并用户验证通过)**:回复朗读期间,麦克风保持聆听(不再全暂停);MicRecorder 增加 interruptMode —— 块级音频丢弃(TTS 回声不会成句)但 RMS 级别持续检测,音量高于 ~-30dBFS 持续 250ms → `onSpeechInterrupt` → `interruptReply()`(停播放 + abort 在途合成 + 监听器跳过当前回复剩余句),随后录音器恢复正常累积,用户正在说的话成为下一条消息。依赖浏览器 AEC(echoCancellation)消回声。日志实证:两次 `TTS cancelled mid-synthesis`(22:20:27 / 22:21:01),打断快且后台不再烧 GPU。用户反馈「好像可以了」。
    - 与方案 A(silero)的差距:打断与断句都基于 RMS 阈值,精度不如 silero-vad;环境吵/外放时可能误触发。A 需桥接加 WebSocket VAD 流,未实施(待用户决定)。
    - 注意:自动打断仅在麦克风处于聆听状态(点过麦克风)时生效;未开麦克风时手动点麦克风仍可打断。
12. **better-sidebar 移到左侧(方案 B)+ 修复两个 bug**(2026-08-16):
    - **移动**:克隆官方源码 `github.com/omdsh-dev/DSH-better-sidebar` 到 `E:\deepseek-works\DSH-better-sidebar`(检出与安装一致的 v0.12.1 标签),改 3 个文件:① `layout.css` 推挤从 #root margin-right 改为中间列 margin-left(右侧完全让给动画窗);② `sidebar.module.css` 面板 left:0+border-right、开关簇 left、收起 translate(-102%)、调宽把手 right:-4、tabBar padding-left;③ `Sidebar.tsx` 面板内联 left=centerRect.left-width、开关簇内联 left、4 处拖拽公式方向翻转、底部面板 seam borderLeft、applyDrag 改写 left、corner 位置。重新构建(tsc+tsdown,Windows 下 build 脚本的 `rm` 不可用需手动分步)→ 替换安装位置 `~/.dsh/profiles/web/node_modules/dsh-better-sidebar/lib/*.js` → 硬刷新生效(README:client 改动无需重启 DSH)。
   - **Bug1 底部面板 0 高度不显示**:根因是 applyDrag 在底部面板关闭时把内联 height 写成 0px(绕过 React 的直接 DOM 写),而 React style diff 与上次渲染值相同会跳过更新 → 陈旧 0px 残留,展开时面板隐形(对话上移是推挤生效但面板高度 0)。**修复:applyDrag 里 height>0 才写**。
   - **Bug2 关喇叭后后台仍狂跑 TTS**:关喇叭只停播放和后续新请求,已发出的 /api/tts 队列仍在桥接合成。**修复(双端)**:客户端 tts() 支持 AbortSignal,回复监听器持有 AbortController 并注册到共享句柄,关喇叭时 `abortTts()` 中止在途请求;桥接 `/api/tts` 用 threading.Event + asyncio watchdog 轮询 `request.is_disconnected()`,`_synthesize` 块间检查 Event → 客户端断开即停止合成(499),GPU 立即释放。
   - **调试手法**:`document.querySelectorAll('[class*="bottomPanel"]')` + getComputedStyle/getBoundingClientRect 拿 DOM 地面真相(内联 height:0px + bottomPanelHidden 定位到 React-vs-DOM 样式冲突);health 的懒加载标志判断模型是否被调用。

## T7 验收通过 ✅(2026-08-16)

- 用户听到朗读,确认语音改造成功;朗读文本清洗/开关/打断均在工作。
- 桥接日志实证:多次 /api/tts 自动触发(短回复 32 字→6.2s、23 字→4.5s 等),TTFA 0.5s 级。
- **用户备注**:图标显示有轻微小 bug,最后整个项目做完再修(记入待修)。

## T8 女友动画窗 + 一键启动(已构建,待用户验证)

- **桥接新增媒体托管**(voice_bridge.py):`/api/media/bg-images`(6 视频+1 图)、`/api/media/task-videos`(2 视频)、静态 `/media/bg-images/*`、`/media/task-videos/*`(StaticFiles,Range 支持)。已实测:列表与静态文件均 200。
- **插件新增**:
  - `ReplySpeaker` 增加 subscribe/emit(朗读状态变化通知动画窗);
  - `CompanionController`(共享可见性,localStorage `s2s.voice.companion`);
  - `CompanionWindow`:全高右侧通栏(默认 55vw,240px~70vw 可调),两层 `<video>`(bg 待机循环轮播 + task 说话循环),opacity 0.6s 交叉淡入;左缘渐变遮罩(左侧时镜像);内侧把手拖动调宽(持久化 `companionW`)、双击换边(持久化 `companionSide`);pointer-events:none 不挡聊天;
  - `CompanionToggle`:输入栏显示器图标按钮,显隐整窗;
  - 说话视频每次回复轮换(taskIndex 递增)。
- **启动器 `start-dsh-voice.cmd`**(CRLF):起桥接(独立最小化窗口,不随 dsh web 重启而死)→ 等 health(30s)→ 起 dsh web(含 modlens env 处理)→ 开浏览器。**从此双击一个文件即可,根治「桥接被 dsh web 重启连坐」问题。**
- 构建:tsc 零错误;bundle 27.76→42.34kB;已上线,刷新即可。
- 踩坑:CSS 镜像不能对容器 scaleX(-1)(会把视频也镜像),改用 `.left .video` 的 mask 反向;拖拽持久化要存拖拽过程中的实时宽度(ref 内更新),不能读闭包旧值。

## T6 验收通过 ✅(2026-08-16,用户听到朗读)

- 用户控制台日志确认:`speaking reply seq = 190941 / 191306` 两次触发;用户原话「我听到声音了」。
- 桥接日志:qwen3 warmup 后 TTFA 0.49s、RTF 2.09;长回复(512 字)生成 57s 音频播放成功。
- 端到端闭环:回复监听 → text 块 → /api/tts → 小雅音色播放,全部打通。

## T7 打磨(已构建,待用户验证)

- **语音朗读总开关 UI**:新组件 VoiceToggle,挂 `conversation.input.left`(order 85),喇叭图标,高亮=开/变暗=关,持久化 `s2s.voice.enabled`;只控制朗读,麦克风输入不受影响。
- **朗读文本清洗**:`voice/clean.ts` 的 `cleanReplyText()` —— 去 fenced code 块(含 ```dsh-ui JSON 围栏)、inline code、URL、标题/引用/列表/强调符号,压缩空白,400 字句界截断。修复「长回复 57 秒朗读噪音」体验。
- **说话打断**:点麦克风开始采集时 `speaker.stop()` 立刻停掉正在朗读的回复(barge-in)。
- 构建:tsc 零错误;bundle 21.77→27.76kB;已上线,刷新即可。

## 关键路径速查

| 项 | 路径/值 |
|---|---|
| 桥接服务源码 | `D:\speech-to-speech\voice_bridge.py` |
| 桥接配置 | `D:\speech-to-speech\bridge-config.json` |
| 验证用启动器 | `D:\speech-to-speech\start-bridge.cmd`(CRLF,直调 venv-speech python) |
| STT 冒烟 | `D:\speech-to-speech\smoke_stt.py [--format raw\|wav] [--file x.wav]` |
| TTS 冒烟 | `D:\speech-to-speech\smoke_tts.py [--text 句子] [--out tts_out.wav]` |
| 桥接 URL | `http://127.0.0.1:8765`(/api/health /api/stt /api/tts) |
| 后端虚拟环境 | `D:\speech-to-speech\venv-speech\Scripts\python.exe`(无需手动激活,直接路径调用) |
| DSH 插件包 | `E:\deepseek-harness\deepseek-harness\packages\client\ui-voice\` |

---

## T1 桥接服务骨架 ✅(用户已验证)

- 产出:`voice_bridge.py`(FastAPI)+ `bridge-config.json` + `start-bridge.cmd`。
- `GET /api/health` → `{status, stt, tts, stt_error, tts_error}`;模型**懒加载**(首次调用才载入)。
- CORS 放行 `http://127.0.0.1:3080` / `http://localhost:3080`(用 curl 预检验证,`access-control-allow-origin` 正确)。
- 验证:浏览器开 `/api/health` 看到 `{"status":"ok","stt":false,"tts":false}`。

**经验**:
- venv 不用「激活」—— 启动器直接用 `venv-speech\Scripts\python.exe` 绝对路径调用即可。
- .cmd 文件必须 CRLF(用 PowerShell 把 LF 转 CRLF)。

## T2 /api/stt ✅(用户已在 PowerShell 复现验证)

- `ModelManager` + `ensure_stt()`:懒加载 `WhisperSTTHandler`(transformers 版 whisper-large-v3),`asyncio.Lock` 双锁(load_lock 防双载 + infer_lock 串行化 GPU 推理)。
- `decode_audio()`:支持 **裸 PCM16**(`application/octet-stream`)+ **WAV**(`audio/wav`,soundfile 解码任意采样率/声道),统一转 16kHz 单声道 float32;时长上限 `X-Max-Audio-Sec`(默认 30s,超长 422)。
- 调用:`next(iter(handler.process(VADAudio(audio=float32_16k))))` → `Transcription.text`。
- 实测(小雅参考音频 14.2s):识别出台湾腔中文,与 ref_text 高度吻合;两种入参结果一致。
- health 变 `stt:true`;模型加载+warmup 约 1.6s。

**经验/观察**:
- whisper-large-v3 对台湾腔中文输出**繁体**(靠杯/來/囉/繼續)—— 功能无碍;若用户要简体需加 OpenCC 转换。
- handler 返回的 `language` 字段为空串是上游小毛病,装饰性,不影响 text。
- 冒烟脚本加了 `--format raw|wav` 供复现。

## T3 /api/tts ✅(用户试听验证通过)

- `ensure_tts()`:懒加载 `Qwen3TTSHandler`(faster-qwen3-tts,Base 模型走 voice_clone 音色克隆)。
- 端点:`POST /api/tts {"text":...}` → 16kHz 单声道 PCM16 WAV;>512 字截断并告警。
- 调用:`b"".join(handler.process(TTSInput(text=..., language_code="zh")))` 收集 int16 块 → `wave` 拼 WAV。
- 实测:「你好,我是小雅…」→ 5.92s WAV(189KB),voice_clone 路径,CUDA graph 已捕获,正式合成 TTFA 0.47s、RTF 1.74。
- health 变 `tts:true`。

**踩坑(重要)**:
- **Pydantic 请求模型必须定义在端点函数之前**:`voice_bridge.py` 顶部有 `from __future__ import annotations`,若 `class TTSRequest` 写在 `@app.post` 之后,装饰器执行时注解还是字符串、FastAPI 解析不到模型,把 `req` 当成 query 参数 → 一律 422「Field required」,且错误信息指向 `loc:["query","req"]` 很迷惑。修复:把模型类挪到端点前。**教训:FastAPI 端点注解的模型类永远先定义。**
- 修复后重启桥接即可,无需清缓存。

## T4 进行中(DSH 插件骨架)

- 新包 `packages/client/ui-voice`(client 插件三件套:package.json/dsh.client manifest、tsconfig、tsdown.client.ts;src/index.ts 空 apply、invariant.ts、css-modules.d.ts、locales.ts、MicButton.tsx + module.css)。
- 挂载点:`conversation.input.left`(list 槽,composer 工具行,`ctx.slots.inject` 等待声明后注册;`order: 80`)。
- 注册三面:tsconfig.client.json references + cordis.patch.yml 行 + web-app/package.json 依赖。
- 构建:`pnpm install` → `pnpm --filter @deepseek-ai/dsh-client-ui-voice bundle` → `pnpm build:web`;生产 dsh web 需刷新(必要时重启)。

**经验(截至 T4)**:
- 插槽语义:`conversation.composer.bar` 是 single(默认输入栏本体,勿占);`conversation.input.left/right` 是 list(工具行小控件正位);`conversation.composer.dock` 是 list(卡片下方氛围读数,不适合放点击控件)。
- 插件跨包引用 slot 类型:`import type { PropsRuntime, PropsLocale } from '@deepseek-ai/dsh-client-ui-slots'` + `import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'`(触发 SlotMap 声明合并)。
- 主题 token 用 `--dsw-alias-*`(如 `--dsw-alias-label-secondary`、`--dsw-alias-interactive-bg-hover`),不写裸色值。
- **构建顺序(踩坑)**:client 插件包 bundle 前必须先 `tsc -b packages/client/<pkg>/tsconfig.json`(产出 lib/types 下的 JS/d.ts),tsdown 的 lib 入口消费的是 lib/types 产物。单包构建:`pnpm exec tsc -b packages/client/ui-voice/tsconfig.json && pnpm --filter @deepseek-ai/dsh-client-ui-voice bundle`;随后 `pnpm build:web` 重建前端产物。
- **新增插件必须重启 dsh web**:运行中的 `dsh web` 的插件清单(含 cordis.patch.yml 行与 `/plugins/.../client.js` 路由)在**启动时**确定,刷新页面不够——ui-voice 的 bundle URL 在旧进程返回 404。重启方式:关掉 start-dsh-web.cmd 窗口 → 重新双击启动 → 刷新 3080。会话持久化在磁盘,重启不丢。
- 注册三面已就位:tsconfig.client.json references、cordis.patch.yml 行、web-app/package.json 依赖;`pnpm install` 后 web-app/node_modules 链接确认存在。

## T5 输入链路(已构建,待用户实测)

- 新增文件:`worklets/mic-capture.ts`(原前端 worklet 源码**逐字内嵌**,Blob URL 注册,绕开插件包无 ?raw 的问题)、`bridge.ts`(桥接 HTTP 客户端,`s2s.voice.bridge` localStorage 可覆盖地址)、`voice/recorder.ts`(MicRecorder:静音端点 1800ms / 单句上限 30s / 阈值 ~-40dBFS)、`contract.ts`(VoiceInjected)。
- MicButton 状态机:idle → listening(红脉冲)→ transcribing(主题色)→ idle;单击开始/再单击停止;每次激活捕获**一句话**(端点判定),自动 POST /api/stt → `conversation.send(text)` 注入;发送后回到 idle(连续聆听模式列为 T7 增强)。
- 注入链路:`ctx.sessions.scope(sessionId).get('conversation').send(text)`(会话作用域解析,与打字等价)。
- 构建:tsc 零错误;bundle 4.25→16.47 kB;web 重建 OK;**服务端已提供新 bundle(16406 字节)→ 刷新页面即可,无需再重启 dsh web**(插件 bundle 按请求读盘;上次 404 只因清单启动时未含该行)。
- 注意:首次说话会懒加载 whisper(30~90s),之后每次识别秒回。
- **运维坑(重要)**:桥接是开发期由会话后台任务拉起的,**用户重启 dsh web 会连同后台任务一起杀掉桥接** → 需要重新拉起(或让用户等 T8 的独立启动器)。检测:访问 8765 拒绝连接即桥接未跑。T8 将用 `start-dsh-voice.cmd` 独立拉起,根治此问题。

## T6 输出链路(已构建,待用户实测听音)

- 新增:`voice/speaker.ts`(ReplySpeaker:AudioContext 播放 + 打断,`speaking` 状态供 T8 动画联动)、`voice/reply-listener.tsx`(ReplySpeakerMount:隐藏的会话级监听组件,渲染 null)。
- 监听方式:`useSession(s => s.chat.nodes)` 快照;只处理 `kind==='assistant'` 且 `data.status==='settled'` 且有 `finalNode` 的消息;只取 `blocks` 中 `kind==='text'` 拼文本(**思考/工具调用/图片结构性剔除**);`lastSpokenRef` 挂载时以最新已结算 seq 为基线,历史回放/翻页/重进会话不重读。
- 播放:POST /api/tts → WAV → decodeAudioData → 播放;新回复打断旧播放;语音开关读 `localStorage s2s.voice.enabled`('0'=关,UI 开关 T7 补)。
- apply 里创建**单一共享 speaker** 注入两个组件(mic + listener),为 T8 动画联动留好接口。
- **踩坑(类型)**:`snapshot.chat.nodes` 是视图节点 `ChatConversationViewNode`,`data` 为 unknown、无 seq/blocks;文本在按 kind 分型的 `data`(assistant → `AssistantChatData`)。且 `ChatNode<'assistant'>` 泛型不可用 —— `ChatNodeDataMap` 的 'assistant' 键由 `conversation-nodes/assistant.ts` 的声明合并提供,但 built d.ts 对包外**剥离了纯 type 副作用再导出**,合并不可见(报「'assistant' does not satisfy ChatNodeKind」)。解法:直接 import 导出的 `AssistantChatData` 类型做结构化断言 `node.data as AssistantChatData`。
- **踩坑(BUG,已修复×2)**:
  1. 聊天视图助手节点 kind 是 **`'assistant-step'`** 而非 `'assistant'` → 初版监听永不匹配(已修复);
  2. **`useSession((s) => s.chat.nodes)` 不会重渲染**:`ChatNodeStore` 是**稳定 live 引用**(注释原文「An old ChatSnapshot observes later flushes through this store」),每次发布的 store 实例不变 → 选择器结果引用不变 → 组件不重渲染 → speak effect 只在挂载时跑一次,新回复永不触发(诊断日志「no new settled assistant」只出现一次即为此)。**修复:选择器改为订阅整个快照 `useSession((s) => s)`**(顶层快照每次发布换引用,必重渲染),effect 内再读 live store 扫描。
- **排查方法(可复用)**:客户端不触发时,先看桥接日志有无对应请求(无=客户端没发),再看 health 懒加载标志;给插件加 console 打点(loaded/挂载基线/每次 effect 决策),让用户贴日志精确定位。这次靠「loaded ✓ 挂载 ✓ speak effect 只跑一次」直接锁定选择器不重渲染问题。
- **排查方法(可复用)**:客户端没触发时,先看桥接日志有没有对应请求(有=客户端已发;无=客户端逻辑问题),再看 health 的懒加载标志(stt/tts true=该模型被调用过)。这次靠「tts:false + 日志无请求」快速定位到纯客户端 bug。
- 构建:tsc 零错误;bundle 16.47→21.77 kB;服务端已提供新 bundle(21704B),**刷新即可,无需重启**。
- 注意:第一次回复会懒加载 qwen3(warmup 10~60s),之后 TTFA 0.5s 级。

## T5 验收通过 ✅(2026-08-16,证据确凿)

- 用户说「你好你好,测试一下」→ 桥接日志 `POST /api/stt 200` + `USER: 你好你好,測試一下` → 聊天流出现同文本消息 → 用户收到。
- **端到端闭环验证**:麦克风 → worklet → 桥接 → whisper(繁体输出)→ conversation.send → 对话注入。识别耗时含首次模型加载约 0.5s warmup,之后秒回。
- 繁体输出确认是 whisper-large-v3 对用户口音的默认行为;若用户要简体,后续可加 OpenCC(记入 T7 候选)。

## T4 状态(用户重启后验收通过)✅

- 用户重启 dsh web 后,输入栏工具行出现麦克风按钮 —— **T4 验收通过**。
- 观察:按钮悬停提示未显示(title 属性不弹)。属装饰性问题,不进需求;后续可换 Tooltip 组件或自定义浮层(记入 T7 打磨候选)。
- 关键经验已记入上文:**新增插件必须重启 dsh web**(插件清单启动时确定,旧进程 404)。

## 追加修复:语音「插话/排队」模式开关(2026-08-16,已验收 ✅)

- **背景**:上一轮把 sendText 改成「turn 运行中 → steer(插话)」。用户反馈:当前逻辑 OK,但想要**连续对话**时应该能切换 —— 加一个开关。
- **新增 BusyToggle(⚡)**:输入栏工具行新按钮,s2s.voice.interrupt('1'=插话,默认;'0'=排队)。
  - 插话模式:turn 运行中 → session.prompt(..., 'steer'),打断当前回复立即回答(修复「第二句卡输入框」)。
  - 排队模式:一律 'queue',当前 turn 结束后自动发送(连续对话,不打断生成中回复)。
  - 麦克风 barge-in(说话即停播放、回声防护)两种模式都保留 —— 开关只管投递方式。
- **其他**:删除未用的 IConversation import(tsc TS6133);slot oice-busy-toggle order 87(companion 86 与 reply 90 之间);locales 补 zh/en 文案。
- 构建:tsc 零错误;bundle 50,071→54,545 B;served 已含 s2s.voice.interrupt/oice-busy-toggle 标记。**刷新页面即可验证**。
- **验收通过(2026-08-16)**,证据(桥接日志):
  - 插话(⚡亮):回复朗读中开口 → TTS 未再合成剩余句子(播放中断+吞剩余回复)→ 立即回答 ✅
  - 排队(⚡灭):回复生成中说第二句 → 回复完整读完(无 cancelled,3 句全合成)→ 第二句自动排队、回合结束自动接上 ✅
  - 用户连说两句的中间偶发「短句没上屏」= whisper 1-token 退化结果被桥接判空丢弃(22:39:44/47 日志 degenerate (1-token)),属 STT 对超短语音的固有防护,非排队问题。
  - 排队 dock 的「插话发送」按钮仅回合运行中可点(空闲置灰,提示「仅运行中可插话发送」),空闲时排队句自动发送、无需手点 —— 已向用户解释,确认非卡住。
  - 用户确认按钮语义保持:⚡亮=插话,⚡灭=排队。

## 修复:重启后历史回复被重新朗读(2026-08-16,已验收 ✅)

- **现象**:重启电脑打开 DSH 后,历史会话的回复自动开始 TTS 朗读。
- **根因**:reply-listener 的防重读 seed 在**第一次 effect 运行时**执行,把快照里已有节点标记为已读。但重启后会话快照是**异步加载**的 —— 首次 effect 跑在空快照上,seed 落空(seededRef 置 true 但没标记任何节点),随后历史节点加载进来全被当成新回复朗读。
- **修复**:改用 **anchor 基线**(T6 思路 + 修正时序):
  - 删掉一次性节点 seed(seededRef),改为 baselineRef:首次出现 **settled** 的 assistant 节点时,把基线设为当前最大 anchorSeq,skipUntilRef = baseline;
  - 朗读只处理 anchor > baseline 的节点 → 历史(含翻页 loadOlder 加载的更旧内容)永不重读;
  - running 节点不参与基线 → 新会话第一条回复、重启后立即说话的新回复照常朗读。
- 构建:tsc 零错误;served bundle 54655 B。**刷新页面即生效**;已在朗读的历史会在刷新后停止(组件卸载时 speaker.stop())。
- 已推送 release 副本(commit cdaa0aa,push 成功后补推完成)。
- **验收通过(2026-08-16)**:用户二次刷新页面后测试 —— 历史不再自动朗读 ✓,新回复正常朗读 ✓。

## 打断改造:服务端 silero VAD(2026-08-16,已验收 ✅)

- **背景**:RMS 阈值打断反复误触发 —— TTS 播放期间环境音/音乐/回声被当成「人声」触发打断,随后累积成乱文(「谢谢观看」「響鐘」)。用户要求参考原项目 hf-realtime-voice 的打断/拾音实现。
- **原项目机制**(已读源码确认):
  - 拾音:前端麦克风 PCM16 16kHz 流式发服务端,服务端 **silero VAD**(VADHandler,神经网络)判定语音;
  - min_speech_ms=384ms(短于 384ms 的语音片段当噪声丢弃)、_SHORT_SEGMENT_MIN_FRAGMENT_MS=100ms;
  - 打断:VAD 检测到真人语音 → SpeechStartedEvent(interrupt_response)→ 服务端打断 TTS;播放期间麦克风照常流式发送(靠 VAD 区分人声/回声,不是暂停拾音)。
- **我们实施(Plan A:silero VAD in bridge)**:
  - 桥接加 WebSocket /api/vad:复用 speech_to_speech.VAD.vad_handler.VADHandler(thresh=0.6, min_speech_ms=384),客户端流式发 PCM16,真人语音 → {"event":"speech_start"};
  - 客户端 VadStream(bridge.ts):打断期间把 chunk 发 VAD,收到 speech_start → onSpeechInterrupt(停 TTS+吞剩余);RMS 阈值(0.06+180ms 确认)保留为桥接无 VAD 端点时的回退;
  - onChunk 在 interruptMode 时只发 VAD 不累积。
- 构建:tsc 零错误;served bundle 58,006 B。**需要重启桥接**(/api/vad 是新端点,运行中的旧进程没有)。
- release 副本已合并 VAD(commit d78928a)并推送;验证 release 桥接 import OK。
- **验收通过(2026-08-16)**:
  - 桥接 /api/vad 端到端验证:40ms chunk 流式喂入 + 真实人声 → speech_start 触发;静音不触发(512 帧修复后)。
  - 用户实测:开口 → 故事朗读立即停止(VAD 打断生效);环境音不再触发打断;短杂音被 silero 丢弃(日志 VAD: discarding segment=158ms)。
  - 语音误识别(「我过失了」等)属 whisper 模型问题,与打断逻辑无关。
  - **追加修复**:打断后新回复被吞 bug —— skip 从「<= 最大 anchor」改为「精确 anchor」,只吞被打断的回复,新回复正常朗读(参考原项目 audio-playback clear 语义);用户实测打断后下一条回复正常朗读 ✅ (commit 06483e9)。

## STT 升级:FunASR Paraformer 中文 ASR(2026-08-16,已验收 ✅)

- **背景**:whisper 家族(含 turbo)对用户口音的中文同音字识别差(尽/静、目/木、楼/牛、庵/安、钱/前 → 准确率约 86%);beam 提高又太慢(large-v3 beam3 9.5s)。
- **方案**:桥接新增 stt.backend 双后端(funasr | whisper),FunASR Paraformer-large 中文模型(ModelScope 下载 ~1GB,iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch);/api/stt 接口不变,前端零改动。
- **安装**:pip install funasr modelscope(清华镜像);模型 snapshot_download 进 modelscope 缓存。
- **实测(用户口音,三句古诗词)**:
  - whisper-turbo:尽→静、目→木、楼→牛、庵→安、钱→前,5 处同音错(86%)
  - FunASR:五处同音字**全部纠正**;仅「仙→鲜」「摘→栽」2 处音近错(~93%)
- **保留 whisper 回退**:config 里 backend 改 whisper 即回退(适合要 whisper 的场景)。
- **响应时间实测(direct benchmark,排除脚本开销)**:2s 音频 642ms(含首次 CUDA graph 捕获)/ 5s 音频 151ms / 14.2s 音频 155ms,RTF 0.003 —— 秒回级,浏览器无感知延迟。之前 smoke 脚本测出的 12.5s 是脚本自身开销(soundfile/scipy 导入+重采样+HTTP),非推理时间。
- release 已推送(commit a547cf7);README/requirements(funasr/modelscope/scipy)/example config 同步;本地 D:\speech-to-speech\requirements.txt 已建(完整依赖)。
- 注意:本地 start-bridge.cmd / start-dsh-voice.cmd 无需改动(uvicorn 启动方式不变;FunASR 是 bridge-config backend 切换)。

## 模型本地化:models/ 目录(2026-08-16)

- **需求**:把 FunASR + silero VAD 模型拷到 D:\speech-to-speech\models\ 独立存储,桥接从本地加载(不依赖 ModelScope/HF/torch.hub 缓存),并更新启动脚本。
- **models/ 结构**:
  - funasr/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/ (850MB,从 modelscope 缓存拷贝)
  - silero-vad/silero_vad_v4.jit (1.4MB,从 silero-vad v4.0 tag 的 files/silero_vad.jit 获取;无 annotator,稳定)
  - silero-vad/silero_vad.jit / *_16k_op15.onnx / *_half.onnx (v5 缓存拷贝,备用)
- **桥接改造**:
  - VADSession 改用本地 v4 jit + VADIterator(不再用 speech_to_speech.VADHandler 的 torch.hub.load);feed 改为**缓冲累积**切 512 帧(不再补零 —— 补零会破坏音频连续性导致 speech_start 不触发)。
  - FunASR 后端 model_name 指向本地路径(bridge-config.json),AutoModel(model=本地目录) 加载。
- **排障记录**:
  - 测试脚本漏了立体声降混 → (512,2) 喂入触发 silero "too short"/annotator 错 —— 非模型问题,降混后 v4/v5 jit 均正常。
  - torch 2.14 dev + silero v5 jit 的 annotator 在新 torch 下行为不稳(实测 17:53 正常、18:0x 报错),故改 v4 稳定版。
  - silero onnx 各版本(op15/half)输入签名与 stft pad 要求不匹配,放弃。
- **启动脚本**:start-bridge.cmd / start-dsh-voice.cmd 加 models 存在性检查提示。
- **验证**:VAD WS 端到端 speech_start=7 speech_end=8(本地 v4);STT 本地 funasr 识别正常(含加载 6.4s,热后 150ms)。
- release:bridge/voice_bridge.py 合并(简化配置 + funasr 路径解析 + 本地 VAD),example config models/ 相对路径,.gitignore 加 models/,启动脚本加检查,README 模型目录说明。

## 前端噪声门 NoiseGate(2026-08-16,已验收 ✅)

- **背景**:用户贴音响时 TTS 声音大量拾入产生乱文;放风扇旁也会偶发误识别(「这是什么」「我爸的手机壳」)。原项目有前端噪声门,我们没启用。
- **发现**:worklet(mic-capture.ts)其实**早已 verbatim 携带原项目的 gate 实现**(attack 5ms/hold 250ms/release 80ms 包络),但主线程从未发送 {kind:'gate'} 启动消息 → gate 一直关闭。
- **改动**:
  - recorder.ts:MicRecorderOptions 加 noiseGateDb;start() 里 node.port.postMessage({kind:'gate', enabled:true, thresholdDb}) 启用。
  - MicButton.tsx:noiseGateDb **-45 → -35**(实测 -45 挡不住桌面风扇声(~-40~-30dB),收紧后挡中响度环境音;正常说话 ~-26~-10dB 不受影响)。
- **验证**:
  - 风扇旁不说话:噪声门生效时不冒乱文(「没有没有没有」= 干净);
  - -45dB 时风扇声仍穿透一次(识别成「这是什么」)→ 收紧 -35dB;
  - -35dB 后正常说话识别不受影响(「我爸的手机壳」准确)。
- **边界**:响亮持续环境音(风扇近距/家人说话/电视)任何 ASR 都挡不住,属物理限制;识别本身(清晰说话)准确。
- release 已推送(commit 8ce803a)。
