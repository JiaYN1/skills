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
- 可选 `rembg` 模型抠图；没有安装时使用轻量的纯色背景抠除兜底。
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

只有一张照片也能工作，因为其他动作会在同一张照片上叠加轻微的缩放、上下浮动和旋转。为了获得更自然的动画，建议照片主体完整、背景简单，并分别准备站立、侧身、趴下等姿态。

生成完成后，可以先预览资源包：

```powershell
python pet_runtime.py --config generated-pets/我的宠物/pet_config.json
```

## 后续产品化方向

当前版本解决的是“能生成、能运行、能互动”的 MVP。若要达到商业产品质量，下一步应加入：

- AI 背景移除和姿态一致性处理。
- 根据照片生成更完整的走路/睡觉逐帧 PNG 或 2D 骨骼动画。
- 自定义行为编辑器、语音互动和系统托盘菜单。
- 生成任务队列、云端上传和下载链接（如果改成网站服务）。
- Windows 签名、自动更新和杀毒软件误报处理。
