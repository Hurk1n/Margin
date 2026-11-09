# Margin

> 用二维码的**纠错余量**换取中心的**自定义像素图案** —— 图案随便画，码照样能扫。

![image-20261109111630609](https://hurkinimages.oss-cn-hangzhou.aliyuncs.com/images/20261109111630680.png)

## 它是怎么做到的

二维码的 Reed–Solomon 纠错有一份冗余预算：H 级每块 28 个纠错码字，最多能纠 **14 个码字错误**。
Margin 的做法是——**只改写数据模块**，让纠错机制去吸收这些"错误"，把中心区域变成字画。

- 功能图案（定位 / 时序 / 对齐 / 格式 / 版本 / 暗模块）一个都不碰；
- 每块的改写量受 `budget` 控制（默认 10，硬上限 14）；
- 数据码字经过交织，中心区域的错误会**天然均摊到 8 个块**，所以放正中心最稳；
- 生成后自动用 zxing 解码自检，失败会逐级降预算重跑。

## 用法

### 图形界面

双击 `Margin.exe`（或 `python app.py`）：

1. 填「扫码后显示的内容」——**裸域名会自动补 `https://`**（`www.hurkin.top` → `https://www.hurkin.top`），否则扫码器只会当成纯文本；
2. 填「中心图案」——文字/字母/数字都行，留空就是普通二维码；
3. 调版本 / 纠错 / 每块预算 / 点径；
4. 点「生成」，底部会给出扫码自检结果；
5. 「保存 PNG」或「保存 SVG」。

### 命令行

```bat
MarginCLI.exe -t "https://example.com" -a "Margin" -o out.png --svg out.svg --budget 10
```

（`Margin.exe` 是无控制台的窗口版，`MarginCLI.exe` 才能在命令行里看到输出。）

| 参数 | 说明 | 默认 |
|---|---|---|
| `-t/--text` | 扫码内容 | 必填 |
| `-a/--art` | 中心图案文字 | 空 |
| `--version` | 版本 6–40 | 自动（4 字符内 v10，5–8 字符 v12，更长 v15） |
| `--ec` | 纠错等级 H/Q/M | H |
| `--budget` | 每块可用的纠错码字 | 10 |
| `--dot` | 点径 0.4–1.0（1.0 = 实心方块） | 0.66 |
| `--scale` | 每模块像素 | 20 |
| `--quiet` | 静区模块数 | 4 |
| `--font` | 字体文件路径 | 系统黑体类 |
| `--ratio` | 图案最大宽度占比 | 0.62 |

### 开发

```bat
pip install -r requirements.txt
python app.py --selftest        :: 跑内置用例
python app.py -t "hello" -a "YG" -o hello.png
build.bat                       :: 重新打包 exe
```

## 参数怎么选

| 想要 | 做法 |
|---|---|
| 字更清楚 | 提高 `--budget`（≤12 安全），或提高 `--version` 让中心区域更大 |
| 更耐扫 | 降低 `--budget`，`--dot 0.8`，`--scale ≥ 20` |
| 印刷 | 用 SVG，`--dot 1.0` 或 0.66 都行；每模块 ≥ 1mm |
| 徽标感 | `--dot 0.66`（圆点）+ 中心 2–4 个字符 |

**预算红线**：H 级每块硬上限 14 个码字。`budget=12` 时已用掉 86%，留给印刷/拍摄的余量不多；
`budget=10`（71%）是推荐的稳妥值。

## 文件

```
Margin/
├─ app.py              入口：无参 = GUI，带参 = CLI
├─ margin/
│  ├─ core.py          QR 结构、功能图案掩码、图案覆写与预算控制、渲染
│  ├─ gui.py           tkinter 界面
│  └─ svg.py           矢量输出
├─ assets/             应用图标
├─ samples/            示例产物
├─ build.bat           打包脚本
├─ requirements.txt
└─ LICENSE
```

打包后的可执行文件放在 [Releases](../../releases) 里，仓库不追踪 `dist/`。

## 已知限制

- 版本 10 的中心对齐图案正好在图形中间，会像一个「方块装饰」压在字上；文字够小（≤2 行、高 ≤12 模块）时可以整体避开。
- 点径太小（<0.5）或模块像素 <8 时，老手机识别率下降。
- 只对 H 级实用；M/L 级的预算撑不起图案。
- 扫码内容是裸域名时会自动补 `https://`；想保留原文就写成完整 URL，或干脆别像域名。

## 下载

到 [Releases](../../releases) 下载 `Margin.exe`（窗口版）或 `MarginCLI.exe`（命令行版），单文件免安装。

## License

MIT © Hurk1n
