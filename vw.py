#!/usr/bin/env python3
"""vw.py — 视频「看」工具：骨架+事件采样体制（V3 方案）

子命令：
  probe  media [--out DIR]      量测：元数据 + scdet 切点 + 每秒运动量（零 token）
  plan   timeline.json [--max N] [--out FILE]  生成采样计划（骨架+事件）
  frames plan.json media [--out DIR] [--width W]  按计划抽帧
  crop   media --times t1,t2 [--region x,y,w,h] [--zoom N] [--out DIR]  裁切细读
  dl     URL [--out FILE]       yt-dlp 下载（B 站等）

用法：先 probe，再 plan，再 frames；crop 按需。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

FFMPEG = None
FFPROBE = None
SKELETON = 16
MIN_GAP = 0.6
SCDET_T = 10.0
IDLE_MOTION = 0.05
PIX_THRESH = 128.0
TOKENS_PER_FRAME = 1600


def resolve_tools():
    global FFMPEG, FFPROBE
    here = os.path.dirname(os.path.abspath(__file__))
    env = {"ffmpeg": "VW_FFMPEG", "ffprobe": "VW_FFPROBE"}
    exe = ".exe" if os.name == "nt" else ""
    # 兼容常见 venv 布局（本机部署路径在首位）
    candidates_dir = [
        os.path.join(here, "venv", "lib", "python3.12", "site-packages",
                     "static_ffmpeg", "bin", "darwin_arm64"),
        os.path.join(here, "venv", "lib", "python3.11", "site-packages",
                     "static_ffmpeg", "bin", "darwin_arm64"),
        os.path.join(here, "venv", "Lib", "site-packages", "static_ffmpeg",
                     "bin", "win_amd64"),
    ]

    def find(name):
        p = os.environ.get(env[name]) or shutil.which(name)
        if p:
            return p
        for d in candidates_dir:
            cand = os.path.join(d, name + exe)
            if os.path.exists(cand):
                return cand
        try:  # 最后尝试 static_ffmpeg 首次下载并加入 PATH
            from static_ffmpeg import add_paths
            add_paths()
            p = shutil.which(name)
            if p:
                return p
        except Exception:
            pass
        return None

    FFMPEG = find("ffmpeg")
    FFPROBE = find("ffprobe")
    if not FFMPEG or not FFPROBE:
        sys.exit("找不到 ffmpeg/ffprobe：pip install static-ffmpeg，"
                 "或用 VW_FFMPEG / VW_FFPROBE 环境变量指定路径")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def die(msg):
    sys.exit(f"vw: {msg}")


def ffprobe_info(media):
    r = run([FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", media])
    if r.returncode != 0:
        die(f"ffprobe 失败: {r.stderr[:300]}")
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        die("没有视频流")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    frac = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        num, den = frac.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0
    dur = float(d["format"].get("duration") or v.get("duration") or 0)
    return {
        "media": media, "duration": dur, "fps": round(fps, 3),
        "width": v.get("width"), "height": v.get("height"),
        "rotation": rot, "has_audio": a is not None,
        "size": int(d["format"].get("size") or 0),
    }


def cmd_probe(args):
    info = ffprobe_info(args.media)
    dur = info["duration"]
    if dur <= 0:
        die("无法读取时长")
    # 一次 ffmpeg 调用拿 scdet + signalstats 运动量
    # ffmpeg 7 scdet 输出行: "lavfi.scd.score: 15.022, lavfi.scd.time: 20.866688"
    # signalstats 经 metadata=print 输出: "lavfi.signalstats.YMAX=..." 带 pts_time
    vf = (f"scdet=t={args.scdet},signalstats,metadata=print:key=lavfi.signalstats.YMAX")
    r = run([FFMPEG, "-hide_banner", "-nostats", "-i", args.media,
             "-vf", vf, "-f", "null", "-"])
    cuts, pix_by_sec = [], {}
    for line in r.stderr.splitlines():
        ms = re.search(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", line)
        if ms:
            sc, t = float(ms.group(1)), float(ms.group(2))
            if sc >= args.scdet:
                cuts.append(round(t, 2))
        mp = re.search(r"lavfi\.signalstats\.YMAX=([\d.]+)", line)
        if mp:
            pt = re.search(r"pts_time:([\d.]+)", line)
            if pt:
                t = float(pt.group(1))
                pix_by_sec[int(t)] = max(pix_by_sec.get(int(t), 0), float(mp.group(1)))
    # motion per second: 用 YMAX 差分近似（相邻秒变化量）
    secs = sorted(pix_by_sec)
    motion = {}
    for i in range(1, len(secs)):
        motion[secs[i]] = abs(pix_by_sec[secs[i]] - pix_by_sec[secs[i - 1]])
    if secs and secs[0] not in motion:
        motion[secs[0]] = 0.0
    all_secs = list(range(0, int(dur) + 1))
    for s in all_secs:
        motion.setdefault(s, 0.0)
    cuts = sorted(set(cuts))
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.media)) or ".",
                                       "vw_" + os.path.splitext(os.path.basename(args.media))[0])
    os.makedirs(out_dir, exist_ok=True)
    tl = {"info": info, "duration": dur, "cuts": cuts,
          "pix_max": {str(k): v for k, v in pix_by_sec.items()},
          "motion": {str(k): round(v, 3) for k, v in motion.items()},
          "active_ratio": round(sum(1 for v in motion.values() if v >= IDLE_MOTION) / max(1, len(motion)), 3)}
    path = os.path.join(out_dir, "timeline.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(tl, fh, ensure_ascii=False, indent=1)
    print(f"时长 {dur:.1f}s  {info['width']}x{info['height']}@{info['fps']}"
          f"  旋转{info['rotation']}  音轨{'有' if info['has_audio'] else '无'}")
    print(f"切点 {len(cuts)} 个  活跃秒占比 {tl['active_ratio']:.0%}")
    print(f"→ {path}")
    if not cuts:
        print("  ! 无硬切点：采样由骨架与运动量承担")


def max_gap(times, dur):
    pts = [0.0] + sorted(times) + [dur]
    return max(b - a for a, b in zip(pts, pts[1:]))


def cmd_plan(args):
    with open(args.timeline, encoding="utf-8") as fh:
        tl = json.load(fh)
    dur = tl["duration"]
    motion = {int(k): v for k, v in tl["motion"].items()}
    pix = {int(k): v for k, v in tl["pix_max"].items()}
    secs = sorted(motion)
    sk = args.skeleton
    plan = [(round(i * (dur / sk) + dur / sk / 2, 2), "A",
             f"骨架 {dur / sk:.0f}s 等间隔") for i in range(sk)]
    fps = tl["info"].get("fps") or 30.0
    edge = max(0.05, 1.0 / fps)
    plan += [(round(edge, 2), "A", "开头"), (round(dur - edge, 2), "A", "末尾")]

    events = []
    for t in tl["cuts"]:
        events += [(0, float(t), "切点"), (0, min(float(t) + 0.4, dur), "切点后0.4s")]
    vals = sorted(motion.values())
    if vals:
        p95 = vals[int(len(vals) * 0.95)]
        p10 = vals[int(len(vals) * 0.10)]
        for s in secs:
            if pix.get(s, 0) >= args.pix_thresh:
                events.append((1, s, f"内容突变 YMAX={pix[s]:.0f}"))
            if s in motion and p95 > 0 and motion[s] >= p95:
                events.append((1, s, "运动尖峰"))
            if p10 == 0 and motion[s] == 0 and s % 30 == 0 and dur > 300:
                events.append((2, s, "运动谷底(抽稀)"))
    budget = max(0, args.max - len(plan))
    window = max(1.0, args.window)
    n_win = max(1, int(dur // window) + 1)
    bw = {w: [] for w in range(n_win)}
    for ev in events:
        bw[min(int(ev[1] // window), n_win - 1)].append(ev)
    for w in bw:
        bw[w].sort(key=lambda e: (e[0], e[1]))
    cursor = {w: 0 for w in bw}
    taken = 0
    progressing = True
    while taken < budget and progressing:
        progressing = False
        for w in range(n_win):
            if taken >= budget:
                break
            lst = bw[w]
            while cursor[w] < len(lst):
                pri, t, why = lst[cursor[w]]
                cursor[w] += 1
                if any(abs(t - p[0]) < MIN_GAP for p in plan):
                    continue
                plan.append((round(float(t), 2), "B", why))
                taken += 1
                progressing = True
                break
    plan.sort(key=lambda p: p[0])
    times = [p[0] for p in plan]
    gap = max_gap(times, dur)
    tokens = len(plan) * TOKENS_PER_FRAME
    out = {"media": tl.get("info", {}).get("media"), "duration": dur,
           "skeleton": sk, "max_gap": round(gap, 2), "shots": [
               {"index": i + 1, "t": t, "phase": ph, "reason": why}
               for i, (t, ph, why) in enumerate(plan)],
           "tokens_est": tokens}
    path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.timeline)), "shotlist.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"骨架 {sk} + 事件 {taken} → 共 {len(plan)} 帧  最大盲区 {gap:.1f}s  ≈{tokens} token")
    print(f"→ {path}")


def extract(media, t, dest, width, draw_index=None):
    vf = f"scale={width}:-2"
    if draw_index:
        vf += f",drawtext=text='{draw_index}':x=8:y=6:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6"
    r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
             "-i", media, "-frames:v", "1", "-vf", vf, "-y", dest])
    if not os.path.exists(dest):
        die(f"t={t:.3f}s 处没有抽到帧（是否超出片长？）")


def cmd_frames(args):
    with open(args.plan, encoding="utf-8") as fh:
        pl = json.load(fh)
    media = args.media
    outdir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.plan)), "frames")
    os.makedirs(outdir, exist_ok=True)
    shots = pl["shots"]
    names = []
    for s in shots:
        t = s["t"]
        mm, ss = divmod(t, 60)
        name = os.path.join(outdir, f"f{s['index']:03d}_{int(mm):02d}m{ss:05.2f}s.png")
        extract(media, t, name, args.width)
        names.append(name)
    print(f"{len(names)} 帧 → {outdir}")
    for n in names:
        print(" ", os.path.basename(n))


def cmd_crop(args):
    times = [float(x) for x in args.times.split(",")]
    outdir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.media)), "vw_crops")
    os.makedirs(outdir, exist_ok=True)
    for t in times:
        vf = "crop=iw:ih"
        if args.region:
            x, y, w, h = args.region
            vf = f"crop={w}:{h}:{x}:{y},scale=iw*{args.zoom}:ih*{args.zoom}"
        elif args.zoom != 1:
            vf = f"scale=iw*{args.zoom}:ih*{args.zoom}"
        mm, ss = divmod(t, 60)
        dest = os.path.join(outdir, f"crop_{int(mm):02d}m{ss:05.2f}s.png")
        r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
                 "-i", args.media, "-frames:v", "1", "-vf", vf, "-y", dest])
        if not os.path.exists(dest):
            die(f"t={t:.3f}s 抽帧失败")
        print("→", dest)


def cmd_dl(args):
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "venv", "bin", "yt-dlp")
        if os.path.exists(cand):
            ytdlp = cand
    if not ytdlp:
        die("找不到 yt-dlp：pip install yt-dlp")
    cmd = [ytdlp, "-f", "bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
           "-o", args.out or "%(title).80s.%(ext)s", args.url]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        die("yt-dlp 下载失败（接口可能已变化，或需要登录；可手动下载后发文件）")


def main():
    ap = argparse.ArgumentParser(prog="vw")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("media"); p.add_argument("--out")
    p.add_argument("--scdet", type=float, default=SCDET_T); p.set_defaults(func=cmd_probe)
    p = sub.add_parser("plan"); p.add_argument("timeline")
    p.add_argument("--max", type=int, default=36)
    p.add_argument("--skeleton", type=int, default=SKELETON)
    p.add_argument("--window", type=float, default=120.0)
    p.add_argument("--pix-thresh", type=float, default=PIX_THRESH)
    p.add_argument("--out"); p.set_defaults(func=cmd_plan)
    p = sub.add_parser("frames"); p.add_argument("plan"); p.add_argument("media")
    p.add_argument("--out"); p.add_argument("--width", type=int, default=768)
    p.set_defaults(func=cmd_frames)
    p = sub.add_parser("crop"); p.add_argument("media")
    p.add_argument("--times", required=True)
    p.add_argument("--region"); p.add_argument("--zoom", type=int, default=2)
    p.add_argument("--out"); p.set_defaults(func=cmd_crop)
    p = sub.add_parser("dl"); p.add_argument("url"); p.add_argument("--out")
    p.set_defaults(func=cmd_dl)
    args = ap.parse_args()
    if args.cmd not in ("dl",):
        resolve_tools()
    args.func(args)


if __name__ == "__main__":
    main()
