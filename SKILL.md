---
name: video-watch
description: 收到视频链接（B 站 BV 号/b23.tv、YouTube、直链 mp4 等）或本地视频文件，要求理解内容、总结视频、读画面文字、分析视频时使用。视频链接走本技能，不要走 web-access。
---

# video-watch：看视频（骨架+事件采样体制）

工具位置：`~/.dsh/dsh_wechat/vw/vw.py`，运行方式 `~/.dsh/dsh_wechat/vw/venv/bin/python ~/.dsh/dsh_wechat/vw/vw.py <子命令>`

## 工作流

1. **下载**（若为链接）：`vw.py dl <URL>`（B 站等，用 yt-dlp；失败则请用户手动下载发文件）
2. **量测**（零 token）：`vw.py probe <media>` → 生成 timeline.json（时长、切点、每秒运动量）
3. **计划**：`vw.py plan timeline.json --max 36` → shotlist.json（骨架 16 帧等分 + 事件帧，输出最大盲区与 token 估算）
   - 当前模型上下文 ≥500k 时可 `--max 120`
4. **抽帧**：`vw.py frames shotlist.json <media>` → frames/ 目录，文件名含时间戳
5. **读图**：用 read_image 逐帧读取（或按时间顺序分批），按时间戳记录每帧画面内容
6. **汇总**：输出带时间点的内容描述；明确报告「最大盲区 X 秒」
7. **追问小字**：`vw.py crop <media> --times t --region x,y,w,h --zoom 2` 裁原始分辨率放大细读，不要在整帧里猜小字

## 模型适配

- 先确认当前模型支持图片输入（supports_images）。已知**不支持**：hy4-preview-f、wb-lite、enhance-1.0、auto-chat。
- 不支持图片时：只输出 probe 的元数据（时长/分辨率/切点数），明确声明「当前模型无法看画面，建议切换多模态模型」，不要编造内容。
- 读图步骤若交给子代理（subagent），必须显式指定多模态模型（如 dsv4.1f）。

## 输出要求

- 描述必须带时间点（如 [02:13]）。
- 无音频转写时不得声称「视频里说了什么」；可说「画面字幕显示…」。
- 长视频盲区大时（>60s）要如实说明，不糊弄。
