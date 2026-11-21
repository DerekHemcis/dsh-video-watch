# video-watch (vw)

让任何多模态 AI agent「看」视频：**量测负责“什么时候”（ffmpeg，零 token），眼睛负责“是什么”（模型）**。

一个薄封装工具 + 一个 DSH/agent 技能。把一个视频链接或本地文件变成「带时间戳的关键帧 + 采样计划」，交给模型逐帧读图，最后输出带时间点的内容描述。

## 能力

| 你问 agent | 它做什么 |
|---|---|
| 「这条视频讲了什么？」 | probe 量测 → plan 采样 → frames 抽帧 → 逐帧读图 → 带时间点描述 |
| 「第 3 分 20 秒画面上那行小字写的什么？」 | crop 从原始分辨率裁下该区域放大，再读 |
| 「视频里说了什么？」（可选，未内置） | 暂无音频转写；画面字幕可读时引用，不编造台词 |

## 采样体制：骨架 + 事件（非均匀采样）

均匀抽帧说不清「自己漏了什么」。这里用两层：

- **骨架帧**（默认 16，按片长等分 + 首尾各 1 帧）：保证盲区有数学上界，输出「最大盲区 X 秒」。
- **事件帧**（默认上限 36，按模型上下文调整）：优先喂给 ffmpeg 量测出的信号——scdet 硬切点、画面内容突变、运动尖峰/谷底，按 120s 窗口轮转分配，同一位置 0.6s 内去重。

## 安装

```bash
pip install static-ffmpeg yt-dlp
```

`static-ffmpeg` 首次运行会下载 ffmpeg/ffprobe 二进制到用户目录（含 scdet 等滤镜，免 brew/免 sudo）。也可用 `VW_FFMPEG` / `VW_FFPROBE` 环境变量指定已有二进制。

## 用法

```bash
# 0) 链接落地（B 站 / YouTube / 直链）
python vw.py dl "https://b23.tv/xxxx"

# 1) 量测：元数据 + 切点 + 每秒运动量（零 token）
python vw.py probe video.mp4

# 2) 采样计划：骨架 + 事件，输出最大盲区与 token 估算
python vw.py plan timeline.json --max 36   # 大上下文模型可 --max 120

# 3) 抽帧：PNG，文件名含时间戳
python vw.py frames shotlist.json video.mp4

# 4) 读图：把 frames/ 目录交给多模态模型，按时间戳汇总

# 5) 追问小字：裁原始分辨率区域放大
python vw.py crop video.mp4 --times 200 --region 0,900,1920,100 --zoom 2
```

## DSH / agent 技能

`SKILL.md` 是给 agent 的技能包。放到 agent 的技能目录（如 `~/.agents/skills/video-watch/`），并按 SKILL.md 里的工作流使用。要点：

- 视频链接走本技能，页面抓取走 web-access。
- 先确认当前模型支持图片；纯文本模型只输出元数据并声明无法看画面。
- 读图步骤若交给子代理，显式指定多模态模型。

## 依赖

- ffmpeg/ffprobe（`static-ffmpeg`，需含 scdet/freezedetect/signalstats/drawtext 滤镜）
- yt-dlp（仅 `dl` 子命令）
- Python 3.9+，纯标准库
