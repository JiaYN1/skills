# 宠物桌面生成器 MVP

这是一个本地优先的 Windows 桌面宠物原型：选择几张宠物照片，程序会将它们标准化为透明 PNG，生成一个资源包，并可在 Windows 上调用 PyInstaller 打包成单文件 `.exe`。

现在同时包含 Docker 服务端版本：浏览器上传照片后，服务端可调用 AI 图像服务生成动作帧，再由 Windows Worker 完成最终 exe 打包。服务端说明见 [server/README.md](server/README.md)。

Windows Worker 和 Windows + AI 交接步骤见 [WINDOWS_WORKER_HANDOFF.md](WINDOWS_WORKER_HANDOFF.md)。

## 已实现

- 无需服务器即可处理照片，原始照片不会上传。
- 透明、无边框、可拖动的桌面窗口。
- 待机呼吸、左右走动、自动睡觉、点击弹跳反应。
- 右键菜单：立即睡觉、恢复活动、退出。
- 支持一张照片起步，也支持多张照片作为动作/备用帧。
- 服务端通过 AI 提示词要求直接输出透明背景 PNG，服务端只做 alpha 校验、主体缩放和动作帧对齐。
- 统一的身份参考图、透明化、落地基线和主体比例处理，减少不同动作帧漂移。
- 服务端可按动作生成 10-12 张有序走动/睡觉 PNG，并导出 `animation.json` 与可选 `skeleton.json`。
- Windows 上通过 PyInstaller 生成包含资源的单文件 exe。

## Windows 上运行

建议使用 Python 3.11 或更新版本：

```powershell
cd desktop-pet
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python pet_creator.py
```

在生成器中选择照片后，点击“生成宠物”。默认会同时打包 exe；最终文件位于类似下面的目录：

```text
generated-pets/我的宠物/dist/我的宠物.exe
```

PyInstaller 不能可靠地把 Python 程序从 Linux 交叉编译成 Windows exe，所以最终 exe 应在 Windows 环境生成。也可以使用：

```powershell
.\scripts\build_creator.ps1
```

先把生成器本身打成 `dist/PetCreator.exe`，再让最终用户只运行这个生成器。

## 照片顺序

生成器最多接收 8 张照片，并按列表顺序分配：

1. 待机
2. 走动
3. 睡觉
4. 点击反应
5-8. 走动备用帧

只有一张照片也能工作，因为其他动作会在同一张照片上叠加轻微的缩放、上下浮动和旋转；资源包还会保存一个可复用的 2D 骨骼时间线。为了获得更自然的动画，建议照片主体完整、背景简单，并分别准备站立、侧身、趴下等姿态。

生成完成后，可以先预览资源包：

```powershell
python pet_runtime.py --config generated-pets/我的宠物/pet_config.json
```

## AI 动作帧与输出格式

服务端页面的“AI 配置（管理员）”支持：

- 要求 AI 从输入照片中分离宠物主体，并直接返回带真实 alpha 通道的透明 PNG；服务端不再使用本地分割模型。
- 把第一张照片作为身份锚点，把其余照片作为动作参考，并在提示词中锁定毛色、脸部、比例、镜头、主体缩放和落地基线。
- 独立设置走动/睡觉帧数（默认 12/10，最多 12）、播放 FPS 和动作输出格式。

默认 `hybrid` 模式会同时包含：

- `assets/walk_*.png`、`assets/sleep_*.png` 等透明逐帧 PNG；
- `animation.json`：动作顺序、FPS、循环信息和姿态计划；
- `skeleton.json`：引擎无关的 2D 骨骼时间线。照片宠物使用 sprite-backed root rig，PNG 仍是视觉真值，便于后续接入 Spine/DragonBones/自研蒙皮渲染器。

如果只需要位图，可选择 `png`；如果只需要骨骼清单，可选择 `skeleton`。没有 AI 或 AI 失败时，资源包仍会使用照片和同一套骨骼/程序化动画兜底。

部分 OpenAI-compatible 网关会忽略 `n=8` 的逐张返回约定，把 8 帧合成一张 4×2 或 2×4 的 contact sheet。服务端会在资源归一化前识别并拆成独立 PNG；因此旧任务中已经生成的“大图”不会自动修复，需要重新生成资源包。直接打开 PNG 或 ZIP 也只是静态资源，动画预览应运行 `pet_runtime.py` 或最终 exe。

背景要求会写入 AI 提示词：只保留宠物主体并输出真实透明 alpha 通道，不要白底、绿底、棋盘格、房间、地面或阴影。`gpt-image-2` 不发送其不接受的 `background=transparent` 参数；如果模型仍返回不透明图片，服务端会拒绝该任务。

常用环境变量：

```dotenv
POSE_CONSISTENCY=true
ANIMATION_MODE=hybrid
ANIMATION_FPS=12
WALK_FRAME_COUNT=12
SLEEP_FRAME_COUNT=10
```

## 后续产品化方向

当前版本已经覆盖“能生成、能运行、能互动”的动画 MVP。后续可继续加入：

- 关键点/分割模型，把身体部位拆成真正可蒙皮的骨骼图层；
- 自定义行为编辑器、语音互动和系统托盘菜单。
- 生成任务队列、云端上传和下载链接（如果改成网站服务）。
- Windows 签名、自动更新和杀毒软件误报处理。
