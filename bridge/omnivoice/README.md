# OmniVoice 语音引擎（WSL2）—— 新手安装说明

> 这个文件夹里的两个文件，是让 **OmniVoice**（能"克隆你声音"的 TTS 模型）跑起来的最小集合：
>
> - `demo_flashinfer.py` —— 服务启动器：加载模型 → 加 FlashInfer 加速 → 开一个网页服务（端口 **9877**）
> - `start_server.sh` —— 在 WSL 里一键启动它（会自动找模型、检查环境）

**你不用手动改这些代码。** 它们会被 `bridge/start-omnivoice-wsl.ps1` 自动复制到 WSL 的 `~/omnivoice-wsl/`。

---

## 一、先搞清楚三件事

| 名词 | 是什么 | 类比 |
|---|---|---|
| **WSL2** | Windows 里的 Linux 子系统 | 你电脑里另开了一台小 Linux 电脑 |
| **OmniVoice** | 一个语音合成模型，能克隆音色 | 给数字人"配音"的引擎 |
| **:9877** | 这个引擎对外开的端口 | 引擎的"门牌号"，别的程序靠它连过来 |

**为什么要放在 WSL2 里？** 因为 OmniVoice 依赖 Linux 下的 FlashInfer + CUDA Graph 加速，Windows 原生跑不起来。
实测效果：克隆首轮出音 **18 秒 → 1.6 秒**，热态 **1.3 秒**。

---

## 二、你需要先准备什么

1. **Windows 11**（10 也行）+ 一块 **NVIDIA 显卡**（16GB 显存推荐，8GB 很紧张）
2. **WSL2**：管理员 PowerShell 里执行一次
   ```powershell
   wsl --install -d Ubuntu-22.04
   ```
   装完**重启电脑**。
3. **模型**（二选一）：
   - 已经有本地模型文件夹（比如 `OmniVoice-bf16`）→ 稍后用 `-Model` 指过去
   - 没有 → 什么都不用做，脚本会**自动从 HuggingFace 下载约 10GB**

---

## 三、三步走

### 第 1 步：建 WSL 里的 Python 环境（只做一次，约 10~20 分钟）

打开 **Ubuntu** 终端（开始菜单搜 Ubuntu），依次执行：

```bash
# 1) 装 uv（Python 包管理器，比 pip 快很多）
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 2) 建目录 + 建环境
mkdir -p ~/omnivoice-wsl && cd ~/omnivoice-wsl
uv venv --python 3.12
source .venv/bin/activate

# 3) 装依赖（顺序别换：先 torch，再 flashinfer）
uv pip install torch --index-url https://download.pytorch.org/whl/cu130
uv pip install flashinfer-python flashinfer-jit-cache omnivoice
```

> ⚠ 显卡是 **Blackwell（RTX 50 系）** 必须用 `cu130`（Cu13 系列）。用 `cu129` 会报 `requires GPUs with sm75`。

### 第 2 步：把服务脚本装进去

回到 Windows，在仓库目录下执行（**它会自动把本文件夹的两个文件复制进 WSL**）：

```powershell
powershell -ExecutionPolicy Bypass -File bridge\start-omnivoice-wsl.ps1
```

或者手动：

```bash
cp /mnt/<你的盘>/<仓库路径>/bridge/omnivoice/demo_flashinfer.py ~/omnivoice-wsl/
cp /mnt/<你的盘>/<仓库路径>/bridge/omnivoice/start_server.sh   ~/omnivoice-wsl/
chmod +x ~/omnivoice-wsl/start_server.sh
```

### 第 3 步：启动

```powershell
# Windows 里（推荐，会顺便接好语音桥）
powershell -ExecutionPolicy Bypass -File bridge\start-omnivoice-wsl.ps1
```

```bash
# 或者 WSL 里（已经有模型的话）
bash ~/omnivoice-wsl/start_server.sh /mnt/d/models/OmniVoice-bf16
```

---

## 四、怎么算成功

等 1~3 分钟，浏览器打开：

```
http://127.0.0.1:9877/
```

**能出现一个带「语音克隆」字样的网页 = 成功。** 打不开就往下看。

命令行验证也可以：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9877/    # 返回 200
```

---

## 五、出错了看这张表

| 你看到的报错 | 原因 | 怎么修 |
|---|---|---|
| `No such file: demo_flashinfer.py` | 文件没装进 WSL | 重跑第 2 步（Windows 里的 ps1 会自动装） |
| `/usr/bin/env: 'bash\r': No such file` | 脚本被存成了 Windows 换行 | `sed -i 's/\r$//' ~/omnivoice-wsl/*.sh` |
| `缺少 Python 环境 .venv` | 没做第 1 步 | 回去做第 1 步 |
| `CUDA_HOME` / nvcc 版本报错 | 系统里的旧 nvcc 干扰 | 脚本已自动处理；手动起时记得 `export CUDA_HOME=$HOME/omnivoice-wsl/.venv/lib/python3.12/site-packages/nvidia/cuda_nvcc` |
| `Address already in use`（9877 被占） | 已经有一个实例在跑 | `pkill -f demo_flashinfer.py` 再启动 |
| `CUDA out of memory` | 显存不够 | 关掉游戏/其他 AI 程序；16GB 以下建议别同时开数字人渲染 |
| 一直连不上、日志停在下载 | 在下载 10GB 模型 | 等；或设镜像 `export HF_ENDPOINT=https://hf-mirror.com` |
| 网页能开但合成很慢（>10 秒） | 没用上 GPU / 首次冷启动 | 第一次慢正常；持续慢看日志里 `FlashInfer applied` 有没有打印 |

**看日志**（90% 的问题这里能看出来）：

```bash
tail -30 ~/omnivoice-wsl/server.log
```

---

## 六、几个常见问题

**Q：能不能不用 WSL2？**
A：能，但会退化成 Qwen3-TTS（不用 WSL，速度慢些、音色少）。README 主文档有「备选：Qwen3-TTS」一节。

**Q：每次重启电脑都要重来吗？**
A：不用。环境只在第 1 步建一次。之后每次开机只要跑一次 `start-omnivoice-wsl.ps1`（已经在跑会自动跳过）。

**Q：模型必须放 `/mnt/e/...` 吗？**
A：不用。脚本按这个顺序找：命令行参数 `-Model` → 环境变量 `OMNIVOICE_MODEL` → 常见本地路径 → 自动下载。

**Q：这个文件夹会被改吗？**
A：仓库里的是"母版"。脚本把它 `cp` 到 `~/omnivoice-wsl/` 后，实际运行的是 WSL 里那份；改仓库里的不会影响已经在跑的实例（重启才生效）。
